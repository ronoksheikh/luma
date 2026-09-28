"""Run a toolbox tool in the sandbox: ``python -m luma_engine.toolkit`` as the sandbox user,
params as JSON on stdin, JSON lines back on stdout (log / progress / image / call / result /
error), stderr streamed as log output.  Params and results are validated against the
manifest's JSON Schemas; ``ctx.call_tool`` requests are answered by ``rpc`` (the agent's
budgeted tool layer).  A crash, a timeout or a memory blow-up in a tool is an error result —
never an exception in the backend.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from .. import db
from ..config import config
from ..secrets_store import redact
from ..terminal import nonet_group, sandbox_argv, sandbox_env
from . import manifest as M
from . import venv

RPC = Callable[[str, dict], Awaitable[Any]]


@dataclass
class Result:
    ok: bool
    result: Any = None
    error: str | None = None
    traceback: str | None = None
    kind: str = "ok"  # ok | params | error | timeout | returns | cancelled
    images: list[str] = field(default_factory=list)  # absolute paths
    logs: list[str] = field(default_factory=list)
    duration: float = 0.0
    out_dir: Path | None = None

    @property
    def counts_as_failure(self) -> bool:
        """Bad params are the caller's mistake, not the tool's."""
        return not self.ok and self.kind in ("error", "timeout", "returns")


def network_allowed(m: dict) -> bool:
    from ..routes_settings import load_settings

    return bool(m.get("network")) and bool(load_settings().get("toolbox_network"))


async def run_tool(row: db.ToolboxTool, params: dict, pdir: Path, call_id: str | None = None, on_log: Callable[[str], None] | None = None,
                   on_progress: Callable[[float, str], None] | None = None, rpc: RPC | None = None, timeout: float | None = None) -> Result:
    m = row.manifest or {}
    errs = M.validate_instance(m.get("parameters") or {"type": "object"}, params, "params")
    if errs:
        props = ", ".join(f"{k}: {v.get('type', '?')}" for k, v in ((m.get("parameters") or {}).get("properties") or {}).items())
        return Result(False, error=f"invalid parameters for {row.name}: " + "; ".join(errs) + f". Expected {{{props}}}"
                      f"{'; required ' + str(m['parameters'].get('required')) if m.get('parameters', {}).get('required') else ''}.", kind="params")
    if not venv.ready.wait(timeout=180):
        return Result(False, error="the tool environment (/data/venv) is still being prepared; try again in a minute", kind="error")
    tdir = config.data_dir / row.path
    call_id = call_id or secrets.token_hex(6)
    out_dir = pdir / "work" / "toolruns" / row.name / call_id
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(out_dir, 0o2775)
        os.chmod(out_dir.parent, 0o2775)
        os.chmod(out_dir.parent.parent, 0o2775)
    except OSError:
        pass
    net = network_allowed(m)
    env = sandbox_env(str(pdir), {"PYTHONDONTWRITEBYTECODE": "1", "OPENBLAS_NUM_THREADS": "2", "OMP_NUM_THREADS": "2"})
    argv = sandbox_argv([venv.python(), "-m", "luma_engine.toolkit"], env, group=None if net else nonet_group())
    job = {"tool_dir": str(tdir), "params": params, "workspace": str(pdir), "out_dir": str(out_dir), "network": net,
           "max_memory_mb": int((m.get("resources") or {}).get("max_memory_mb") or 2048), "max_file_mb": 2048,
           "deny_read": [str(config.secrets_dir), str(config.data_dir / "studio.db"), str(config.data_dir / "git")]}
    limit = float(timeout or m.get("timeout_s") or 300)
    t0 = time.monotonic()
    res = Result(False, out_dir=out_dir)
    proc = await asyncio.create_subprocess_exec(*argv, cwd=str(pdir), env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE, start_new_session=True, limit=16 * 1024 * 1024)
    assert proc.stdin and proc.stdout and proc.stderr
    proc.stdin.write((json.dumps(job) + "\n").encode())
    await proc.stdin.drain()

    def log(line: str) -> None:
        line = redact(line)
        res.logs.append(line)
        if len(res.logs) > 2000:
            del res.logs[:500]
        if on_log:
            on_log(line)

    async def answer(msg: dict) -> None:
        name, cparams = msg.get("name", ""), msg.get("params") or {}
        if rpc is None:
            reply = {"id": msg.get("id"), "ok": False, "error": "call_tool is only available inside an agent run"}
        else:
            try:
                reply = {"id": msg.get("id"), "ok": True, "result": await rpc(name, cparams)}
            except Exception as e:  # noqa: BLE001
                reply = {"id": msg.get("id"), "ok": False, "error": redact(str(e))[:4000]}
        proc.stdin.write((json.dumps(reply, default=str) + "\n").encode())
        await proc.stdin.drain()

    async def read_out() -> None:
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                return
            try:
                msg = json.loads(raw)
            except ValueError:
                log(raw.decode(errors="replace").rstrip("\n"))
                continue
            t = msg.get("type")
            if t == "log":
                log(str(msg.get("msg", "")))
            elif t == "progress":
                if on_progress:
                    on_progress(float(msg.get("pct") or 0), str(msg.get("msg") or ""))
            elif t == "image":
                p = Path(msg.get("path", "")).resolve()
                if p.exists() and pdir.resolve() in p.parents:
                    res.images.append(str(p))
                else:
                    log(f"[luma] ignored emit_image outside the workspace: {msg.get('path')}")
            elif t == "call":
                await answer(msg)
            elif t == "result":
                res.ok, res.result = True, msg.get("result")
            elif t == "error":
                res.ok, res.error, res.traceback, res.kind = False, redact(str(msg.get("error"))), redact(str(msg.get("traceback") or "")), "error"

    async def read_err() -> None:
        while True:
            raw = await proc.stderr.readline()
            if not raw:
                return
            log(raw.decode(errors="replace").rstrip("\n"))

    try:
        await asyncio.wait_for(asyncio.gather(read_out(), read_err(), proc.wait()), limit)
    except asyncio.TimeoutError:
        _kill(proc)
        res.ok, res.kind, res.error = False, "timeout", f"{row.name} timed out after {limit:.0f}s (manifest timeout_s) and was killed"
    except asyncio.CancelledError:
        _kill(proc, graceful=True)
        raise
    finally:
        try:
            proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
    res.duration = round(time.monotonic() - t0, 3)
    if res.kind == "ok" and not res.ok:
        tail = "\n".join(res.logs[-15:])
        rc = proc.returncode
        why = "killed (memory limit / signal)" if rc is not None and rc < 0 else f"exited with code {rc}"
        res.kind, res.error = "error", f"{row.name} {why} without a result. Last output:\n{tail}"
    if res.ok:
        errs = M.validate_instance(m.get("returns") or {"type": "object"}, res.result, "result")
        if errs:
            res.ok, res.kind = False, "returns"
            res.error = f"{row.name} returned a result that does not match its `returns` schema: " + "; ".join(errs)
    return res


def _kill(proc, graceful: bool = False) -> None:
    """Signal the tool's process group (as the sandbox user when the leader is sudo)."""
    from ..jobs import jobs

    if graceful:
        jobs._signal_group(proc.pid, signal.SIGTERM)
        time.sleep(0.5)
    jobs._signal_group(proc.pid, signal.SIGKILL)
    try:
        proc.kill()
    except (ProcessLookupError, PermissionError):
        pass
