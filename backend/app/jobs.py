"""Background jobs: long renders etc. run as detached processes (own session / process
group, tracked by PID in the DB) so they outlive the tool call that started them.

Logs go to ``work/.luma/jobs/<name>.log``; JSON lines ``{"type": "progress", ...}``
become ``progress`` events.  Concurrency is limited (default 1): extra jobs queue.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable

from sqlalchemy import select

from . import db
from .config import config
from .events import bus
from .secrets_store import redact
from .terminal import sandbox_argv, sandbox_env

NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
ACTIVE = ("queued", "running")


class JobError(Exception):
    pass


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        with open(f"/proc/{pid}/stat") as f:
            state = f.read().rsplit(")", 1)[1].split()[0]
        return state not in ("Z", "X")
    except (FileNotFoundError, IndexError, ProcessLookupError):
        return False


class JobManager:
    def __init__(self):
        self.procs: dict[str, subprocess.Popen] = {}
        self.tails: dict[str, asyncio.Task] = {}
        self.listeners: dict[str, list[Callable[[str, dict], None]]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake = None

    def bind_loop(self, loop):
        self._loop = loop

    # -- queries -------------------------------------------------------------------------
    def get(self, project_id: str, name: str) -> db.Job | None:
        with db.session() as s:
            return s.scalars(select(db.Job).where(db.Job.project_id == project_id, db.Job.name == name).order_by(db.Job.started_at.desc().nullsfirst(), db.Job.id.desc())).first()

    def by_id(self, job_id: str) -> db.Job | None:
        with db.session() as s:
            return s.get(db.Job, job_id)

    def list(self, project_id: str | None = None, run_id: str | None = None) -> list[dict]:
        with db.session() as s:
            q = select(db.Job)
            if project_id:
                q = q.where(db.Job.project_id == project_id)
            if run_id:
                q = q.where(db.Job.run_id == run_id)
            return [db.to_dict(j) for j in s.scalars(q.order_by(db.Job.started_at))]

    def running_count(self) -> int:
        with db.session() as s:
            return len(list(s.scalars(select(db.Job).where(db.Job.status == "running"))))

    # -- lifecycle -----------------------------------------------------------------------
    def spawn(self, project_id: str, name: str, command: str | list[str], run_id: str | None = None, cwd: str | None = None,
              tool_call_id: str | None = None, shell: bool = True) -> dict:
        if not NAME_RE.match(name):
            raise JobError("job name must match [A-Za-z0-9._-]{1,64}")
        existing = self.get(project_id, name)
        if existing and existing.status in ACTIVE:
            raise JobError(f"job '{name}' is already {existing.status}; poll or kill it first")
        pdir = config.projects_dir / project_id
        log_dir = pdir / "work" / ".luma" / "jobs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{name}.log"
        log_path.write_text("")
        cmd_text = command if isinstance(command, str) else " ".join(command)
        with db.session() as s:
            job = db.Job(project_id=project_id, run_id=run_id, name=name, command=cmd_text, status="queued",
                         log_path=str(log_path), tool_call_id=tool_call_id)
            s.add(job)
            s.flush()
            jid = job.id
        self._argv[jid] = (command, shell, cwd or str(pdir / "work"))
        self._publish(jid, "queued")
        self._schedule()
        return db.to_dict(self.by_id(jid))

    _argv: dict[str, tuple] = {}

    def _schedule(self) -> None:
        """Start queued jobs while under the concurrency limit."""
        limit = max(1, int(db.get_setting("job_concurrency", config.job_concurrency) or 1))
        with db.session() as s:
            queued = list(s.scalars(select(db.Job).where(db.Job.status == "queued").order_by(db.Job.id)))
        for job in queued:
            if self.running_count() >= limit:
                break
            if job.id in self._argv:
                self._start(job.id)

    def _start(self, jid: str) -> None:
        command, shell, cwd = self._argv.pop(jid)
        job = self.by_id(jid)
        env = sandbox_env(cwd)
        argv = ["bash", "-lc", command] if shell and isinstance(command, str) else list(command)
        argv = sandbox_argv(argv, env)
        logf = open(job.log_path, "ab", buffering=0)
        try:
            proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        except OSError as e:
            logf.close()
            self._finish(jid, "failed", None, f"could not start: {e}")
            return
        finally:
            pass
        logf.close()
        self.procs[jid] = proc
        with db.session() as s:
            j = s.get(db.Job, jid)
            j.pid, j.status, j.started_at = proc.pid, "running", time.time()
        self._publish(jid, "running")
        if self._loop is not None:
            fut = asyncio.run_coroutine_threadsafe(self._tail(jid), self._loop)
            self.tails[jid] = fut  # type: ignore[assignment]

    async def _tail(self, jid: str) -> None:
        job = self.by_id(jid)
        path = Path(job.log_path)
        pos = 0
        buf = ""
        last_emit = 0.0
        pending_text = []
        while True:
            try:
                with open(path, "rb") as f:
                    f.seek(pos)
                    chunk = f.read()
                    pos += len(chunk)
            except FileNotFoundError:
                chunk = b""
            if chunk:
                buf += chunk.decode(errors="replace")
                lines = buf.split("\n")
                buf = lines.pop()
                for ln in lines:
                    self._line(jid, ln, pending_text)
            now = time.monotonic()
            if pending_text and now - last_emit > 0.25:
                self._emit_output(jid, "".join(pending_text))
                pending_text.clear()
                last_emit = now
            proc = self.procs.get(jid)
            done = proc.poll() is not None if proc is not None else not _alive(job.pid)
            if done and not chunk:
                if buf:
                    self._line(jid, buf, pending_text)
                if pending_text:
                    self._emit_output(jid, "".join(pending_text))
                rc = proc.returncode if proc is not None else None
                cur = self.by_id(jid)
                if cur.status == "running":
                    self._finish(jid, "done" if rc == 0 else "failed", rc)
                break
            await asyncio.sleep(0.2)

    def _line(self, jid: str, line: str, pending: list) -> None:
        s = line.strip()
        if s.startswith("{") and '"type"' in s:
            try:
                obj = json.loads(s)
            except ValueError:
                obj = None
            if isinstance(obj, dict) and obj.get("type") == "progress":
                with db.session() as ss:
                    j = ss.get(db.Job, jid)
                    j.progress = obj
                    run_id, name, tcid = j.run_id, j.name, j.tool_call_id
                if run_id:
                    bus.publish(run_id, "progress", {"job": name, "tool_call_id": tcid, **{k: v for k, v in obj.items() if k != "type"}})
                return
        pending.append(line + "\n")

    def _emit_output(self, jid: str, text: str) -> None:
        j = self.by_id(jid)
        if j and j.run_id:
            bus.publish(j.run_id, "tool_output_delta", {"job": j.name, "tool_call_id": j.tool_call_id, "delta": redact(text)[-20000:]})
        for cb in self.listeners.get(jid, []):
            cb("output", {"text": text})

    def _finish(self, jid: str, status: str, rc: int | None, note: str | None = None) -> None:
        with db.session() as s:
            j = s.get(db.Job, jid)
            j.status, j.exit_code, j.finished_at = status, rc, time.time()
        if note:
            with open(self.by_id(jid).log_path, "a") as f:
                f.write(note + "\n")
        self.procs.pop(jid, None)
        self._publish(jid, status)
        self._schedule()

    def _publish(self, jid: str, status: str) -> None:
        j = self.by_id(jid)
        if j and j.run_id:
            bus.publish(j.run_id, "job", {"job": j.name, "id": j.id, "status": status, "exit_code": j.exit_code,
                                          "tool_call_id": j.tool_call_id, "command": redact(j.command)[:500]})

    def kill(self, project_id: str, name: str, grace: float = 3.0) -> dict:
        job = self.get(project_id, name)
        if job is None:
            raise JobError(f"no job named '{name}'")
        return self.kill_id(job.id, grace)

    def kill_id(self, jid: str, grace: float = 3.0) -> dict:
        job = self.by_id(jid)
        if job.status == "queued":
            self._argv.pop(jid, None)
            self._finish(jid, "killed", None, "[luma] killed before start")
            return db.to_dict(self.by_id(jid))
        if job.status != "running":
            return db.to_dict(job)
        self._signal_group(job.pid, signal.SIGTERM)
        t0 = time.time()
        proc = self.procs.get(jid)
        while time.time() - t0 < grace:
            if (proc.poll() is not None) if proc else not _alive(job.pid):
                break
            time.sleep(0.05)
        else:
            self._signal_group(job.pid, signal.SIGKILL)
            if proc:
                try:
                    proc.wait(2)
                except subprocess.TimeoutExpired:
                    pass
        with db.session() as s:
            j = s.get(db.Job, jid)
            if j.status == "running":
                j.status, j.finished_at = "killed", time.time()
                j.exit_code = proc.returncode if proc else None
        self.procs.pop(jid, None)
        self._publish(jid, "killed")
        self._schedule()
        return db.to_dict(self.by_id(jid))

    def _signal_group(self, pid: int | None, sig: int) -> None:
        if not pid:
            return
        try:
            os.killpg(pid, sig)
            return
        except ProcessLookupError:
            return
        except PermissionError:
            pass
        if config.sandbox_user:  # the leader is sudo (root); signal the sandbox user's processes
            subprocess.run(["sudo", "-n", "-u", config.sandbox_user, "--", "kill", f"-{signal.Signals(sig).name[3:]}", "--", f"-{pid}"],
                           capture_output=True, timeout=10)

    def kill_run(self, run_id: str) -> list[dict]:
        out = []
        with db.session() as s:
            ids = [j.id for j in s.scalars(select(db.Job).where(db.Job.run_id == run_id, db.Job.status.in_(ACTIVE)))]
        for jid in ids:
            out.append(self.kill_id(jid))
        return out

    def poll(self, project_id: str, name: str, tail_lines: int = 40) -> dict:
        job = self.get(project_id, name)
        if job is None:
            raise JobError(f"no job named '{name}'")
        d = db.to_dict(job)
        try:
            lines = Path(job.log_path).read_text(errors="replace").splitlines()
        except FileNotFoundError:
            lines = []
        lines = [ln for ln in lines if not ln.startswith('{"type": "progress"')]
        d["log_tail"] = redact("\n".join(lines[-tail_lines:]))
        if job.started_at:
            d["elapsed_s"] = round((job.finished_at or time.time()) - job.started_at, 1)
        return d

    async def wait(self, jid: str, timeout: float) -> dict:
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            j = self.by_id(jid)
            if j.status not in ACTIVE:
                return db.to_dict(j)
            await asyncio.sleep(0.3)
        return db.to_dict(self.by_id(jid))

    def recover(self) -> None:
        """On startup: jobs whose process vanished are marked lost; live ones re-tailed."""
        with db.session() as s:
            for j in s.scalars(select(db.Job).where(db.Job.status.in_(ACTIVE))):
                if j.status == "running" and _alive(j.pid):
                    continue
                j.status, j.finished_at = "lost", time.time()
        with db.session() as s:
            live = [j.id for j in s.scalars(select(db.Job).where(db.Job.status == "running"))]
        for jid in live:
            if self._loop is not None:
                asyncio.run_coroutine_threadsafe(self._tail(jid), self._loop)


jobs = JobManager()
