"""Run lifecycle: start / follow-up / ask_user answers / cancel, plus the keyless demo."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

from sqlalchemy import select

from .. import db
from ..config import config
from ..events import bus
from ..jobs import jobs
from ..secrets_store import Credentials, redact
from ..terminal import terminals

ACTIVE_STATUSES = ("running", "waiting_input")


class RunManager:
    def __init__(self):
        self.tasks: dict[str, asyncio.Task] = {}
        self.creds: dict[str, Credentials] = {}
        self.inbox: dict[str, list[str]] = {}
        self.answers: dict[str, asyncio.Future] = {}
        self.finished: dict[str, dict] = {}
        self.notes: dict[str, list[str]] = {}  # system notes for the agent (e.g. the user edited the plan)
        self.compact_requests: dict[str, str] = {}
        self._resuming: set[str] = set()
        self.pending_request: dict[str, str] = {}
        self.started_at: dict[str, float] = {}
        self.render_rate: dict[str, float] = {}  # measured seconds per full-size frame (from previews)
        self.crossed: dict[tuple, float] = {}  # when a run crossed its cost/time approval thresholds

    # -- state -----------------------------------------------------------------------------
    def create(self, project_id: str, kind: str = "agent", model: str = "") -> db.Run:
        with db.session() as s:
            r = db.Run(project_id=project_id, kind=kind, status="idle", model=model)
            s.add(r)
        return r

    def get(self, run_id: str) -> db.Run | None:
        with db.session() as s:
            return s.get(db.Run, run_id)

    def set_status(self, run_id: str, status: str, **extra) -> None:
        with db.session() as s:
            r = s.get(db.Run, run_id)
            if r is None:
                return
            r.status = status
            r.updated_at = time.time()
            for k, v in extra.items():
                if hasattr(r, k):
                    setattr(r, k, v)
            data = {"status": status, "steps": r.steps, "error": r.error}
        data.update({k: v for k, v in extra.items() if k not in ("outputs",)})
        bus.publish(run_id, "run_status", data)

    def is_active(self, run_id: str) -> bool:
        t = self.tasks.get(run_id)
        return t is not None and not t.done()

    # -- messages --------------------------------------------------------------------------
    async def post_message(self, run_id: str, text: str, creds: Credentials | None = None) -> None:
        run = self.get(run_id)
        if run is None:
            raise KeyError(run_id)
        if creds is not None and creds.has_llm:
            self.creds[run_id] = creds
        text = text.strip()
        if not text:
            raise ValueError("empty message")
        bus.publish(run_id, "user_message", {"text": text})
        fut = self.answers.get(run_id)
        if fut is not None and not fut.done():
            fut.set_result({"text": text, "via": "message"})  # a typed reply answers the pending question/approval
            return
        if self.is_active(run_id):
            self.inbox.setdefault(run_id, []).append(text)  # injected before the next model call
            return
        with db.session() as s:
            s.add(db.Message(run_id=run_id, role="user", content={"role": "user", "content": text}))
        self.start(run_id)

    def start(self, run_id: str) -> None:
        run = self.get(run_id)
        if run.kind == "demo":
            coro = self._demo(run_id)
        else:
            from .loop import AgentLoop

            creds = self.creds.get(run_id)
            if creds is None or not creds.has_llm:
                self.set_status(run_id, "failed", error="No LLM configured: add an API key, base URL and model in Settings.")
                return
            coro = AgentLoop(run_id, run.project_id, creds, self).run()
        self.tasks[run_id] = asyncio.create_task(self._guard(run_id, coro))

    async def _guard(self, run_id: str, coro) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            self.set_status(run_id, "cancelled")
            raise
        except Exception as e:  # noqa: BLE001
            tb = redact(traceback.format_exc())
            bus.publish(run_id, "error", {"message": redact(f"{type(e).__name__}: {e}"), "traceback": tb[-4000:]})
            self.set_status(run_id, "failed", error=redact(str(e))[:2000])
        finally:
            self.answers.pop(run_id, None)

    async def ask(self, run_id: str, question: str, options: list[str] | None, tool_call_id: str) -> str:
        ans = await self.request_user(run_id, "ask", {"question": question, "options": options or [], "allow_free_text": True}, tool_call_id)
        return ans.get("text") or ", ".join(ans.get("selections") or []) or ans.get("choice") or ""

    EVENT_OF = {"ask": "ask_user", "approval": "approval_request", "options": "options_request"}

    async def request_user(self, run_id: str, kind: str, payload: dict, tool_call_id: str, timeout_s: float | None = None,
                           default: dict | None = None) -> dict:
        """Pause the run until the user answers (buttons, chips or a typed message) or the timeout picks
        `default`. The request is persisted, so it survives reloads and is visible after a restart."""
        with db.session() as s:
            q = db.UserRequest(run_id=run_id, kind=kind, payload=payload, tool_call_id=tool_call_id)
            s.add(q)
            s.flush()
            qid = q.id
        deadline = time.time() + timeout_s if timeout_s else None
        bus.publish(run_id, self.EVENT_OF[kind], {"request_id": qid, "tool_call_id": tool_call_id, "timeout_s": timeout_s, "deadline": deadline,
                                                  "default": default, **payload})
        fut = asyncio.get_running_loop().create_future()
        self.answers[run_id] = fut
        self.pending_request[run_id] = qid
        extra = {"question": payload.get("question") or payload.get("title"), "options": payload.get("options") if kind == "ask" else
                 [o["label"] if isinstance(o, dict) else o for o in (payload.get("choices") or payload.get("options") or [])]}
        self.set_status(run_id, "waiting_input", tool_call_id=tool_call_id, request_id=qid, request_kind=kind, **extra)
        status = "answered"
        try:
            try:
                ans = await (asyncio.wait_for(fut, timeout_s) if timeout_s else fut)
            except asyncio.TimeoutError:
                ans, status = {**(default or {}), "timeout": True}, "timeout"
        except asyncio.CancelledError:
            with db.session() as s:
                row = s.get(db.UserRequest, qid)
                row.status, row.answered_at = "cancelled", time.time()
            raise
        finally:
            self.answers.pop(run_id, None)
            self.pending_request.pop(run_id, None)
        with db.session() as s:
            row = s.get(db.UserRequest, qid)
            row.status, row.answer, row.answered_at = status, ans, time.time()
        bus.publish(run_id, "approval_result", {"request_id": qid, "kind": kind, "answer": ans, "status": status, "tool_call_id": tool_call_id})
        self.set_status(run_id, "running")
        return ans

    def answer_request(self, run_id: str, request_id: str, answer: dict) -> None:
        if self.pending_request.get(run_id) != request_id:
            raise ValueError("that request is not waiting for an answer")
        fut = self.answers.get(run_id)
        if fut is None or fut.done():
            raise ValueError("that request is not waiting for an answer")
        fut.set_result(answer)

    def take_inbox(self, run_id: str) -> list[str]:
        return self.inbox.pop(run_id, [])

    def take_notes(self, run_id: str) -> list[str]:
        return self.notes.pop(run_id, [])

    def system_note(self, run_id: str, text: str) -> None:
        """Tell the agent something at its next step (plan edits, memory edits, restores). If the
        run is idle the note waits in its history for the next activation."""
        bus.publish(run_id, "system_note", {"text": text})
        if self.is_active(run_id):
            self.notes.setdefault(run_id, []).append(text)
        else:
            with db.session() as s:
                s.add(db.Message(run_id=run_id, role="user", content={"role": "user", "content": f"[System note]\n{text}"}))

    # -- resume after a crash / restart ----------------------------------------------------
    RESUMABLE = ("interrupted", "failed", "stopped", "cancelled")

    async def resume(self, run_id: str, creds: Credentials | None) -> dict:
        """Continue an interrupted run: close tool calls that never returned, re-attach to jobs that
        are still alive, re-queue resumable renders that died with the old process, cancel stale user
        requests, then restart the loop with a note describing all of it (the plan, memory, notes and
        the full persisted conversation are rebuilt from the database)."""
        from .. import plan

        run = self.get(run_id)
        if run is None:
            raise KeyError(run_id)
        if self.is_active(run_id):
            raise ValueError("run is already active")
        if run.status not in self.RESUMABLE:
            raise ValueError(f"run is {run.status}; only interrupted/stopped/failed/cancelled runs can be resumed")
        if run.kind != "demo":
            if creds is not None and creds.has_llm:
                self.creds[run_id] = creds
            if run_id not in self.creds:
                raise ValueError("No LLM configured: add an API key, base URL and model in Settings.")
        closed = self._close_dangling_calls(run_id)
        with db.session() as s:
            stale = list(s.scalars(select(db.UserRequest).where(db.UserRequest.run_id == run_id, db.UserRequest.status == "pending")))
            for q in stale:
                q.status, q.answered_at = "cancelled", time.time()
            stale_titles = [q.payload.get("title") or q.payload.get("question") for q in stale]
        alive, requeued = [], []
        for j in ([] if run.kind == "demo" else jobs.list(run.project_id, run_id)):
            if j["status"] == "running":
                alive.append(j["name"])
            elif j["status"] == "lost" and _resumable_job(j["command"]):
                newer = jobs.get(run.project_id, j["name"])
                if newer and newer.id != j["id"]:
                    continue
                tcid = f"resume_{j['id']}"
                bus.publish(run_id, "tool_call_start", {"id": tcid, "name": "render_final", "index": 0, "resumed": True})
                bus.publish(run_id, "tool_args_delta", {"id": tcid, "delta": json.dumps({"resumed_job": j["name"]})})
                nj = await asyncio.to_thread(jobs.spawn, run.project_id, j["name"], j["command"], run_id, None, tcid)
                requeued.append(j["name"])
                asyncio.create_task(self._watch_job(run_id, nj["id"], tcid))
        if run.kind == "demo":
            self._resuming.add(run_id)
            bus.publish(run_id, "system_note", {"text": "Demo resumed: finished frames are kept and the render continues.", "kind": "resume"})
            self.start(run_id)
            return {"resumed": run_id, "jobs_alive": alive, "jobs_requeued": requeued}
        prog = plan.progress(run_id)
        note = ["The run was interrupted (server/container restart) and has been RESUMED. Your context was rebuilt from the "
                "persisted conversation, plan, memory and notes."]
        if closed:
            note.append(f"Tool calls that never returned (treat their results as unknown and verify on disk): {', '.join(closed)}.")
        if alive:
            note.append(f"Jobs still running and re-attached: {', '.join(alive)} — poll them with terminal_poll.")
        if requeued:
            note.append(f"Renders re-queued (they resume and skip finished frames): {', '.join(requeued)} — poll with terminal_poll.")
        if stale_titles:
            note.append(f"These questions/approvals were pending and are cancelled — ask again if still needed: {'; '.join(map(str, stale_titles))}.")
        if prog["total"]:
            cur = prog["current"]["title"] if prog["current"] else "none"
            note.append(f"Plan: {prog['done']}/{prog['total']} done, in progress: {cur}. Re-read the plan and continue.")
        with db.session() as s:
            s.add(db.Message(run_id=run_id, role="user", content={"role": "user", "content": "[System note]\n" + "\n".join(note)}))
        bus.publish(run_id, "system_note", {"text": " ".join(note), "kind": "resume"})
        self.start(run_id)
        return {"resumed": run_id, "closed_calls": closed, "jobs_alive": alive, "jobs_requeued": requeued, "cancelled_requests": len(stale_titles)}

    def _close_dangling_calls(self, run_id: str) -> list[str]:
        """Every assistant tool call needs a tool result before the conversation can continue."""
        with db.session() as s:
            rows = list(s.scalars(select(db.Message).where(db.Message.run_id == run_id).order_by(db.Message.id)))
        answered = {r.content.get("tool_call_id") for r in rows if r.role == "tool"}
        closed = []
        for r in rows:
            for tc in r.content.get("tool_calls") or []:
                if tc["id"] not in answered:
                    closed.append(f"{tc['function']['name']} ({tc['id']})")
                    with db.session() as s:
                        s.add(db.Message(run_id=run_id, role="tool", content={"role": "tool", "tool_call_id": tc["id"],
                                         "content": "Error: interrupted — the server restarted while this tool was running; its result is unknown."}))
                    bus.publish(run_id, "tool_result", {"id": tc["id"], "name": tc["function"]["name"], "status": "cancelled",
                                                        "output": "interrupted by a server restart"})
        return closed

    async def _watch_job(self, run_id: str, job_id: str, tcid: str) -> None:
        t0 = time.time()
        res = await jobs.wait(job_id, 24 * 3600)
        bus.publish(run_id, "tool_result", {"id": tcid, "name": "render_final", "status": "success" if res["status"] == "done" else "error",
                                            "seconds": round(time.time() - t0, 1), "output": f"resumed job {res['name']} {res['status']}"})
        if res["status"] == "done":
            from .. import checkpoints

            await asyncio.to_thread(checkpoints.auto, res["project_id"], run_id, f"Render {res['name']} finished (resumed)")

    async def cancel(self, run_id: str) -> dict:
        """Stop everything: LLM stream + ElevenLabs requests (task cancel), terminal
        command (Ctrl-C) and spawned jobs (process-group kill)."""
        run = self.get(run_id)
        if run is None:
            raise KeyError(run_id)
        t = self.tasks.get(run_id)
        killed = await asyncio.to_thread(jobs.kill_run, run_id)
        term = terminals.get(run.project_id, create=False)
        if term is not None and term.alive:
            term.interrupt()
        if t is not None and not t.done():
            t.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(t), 10)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
        if self.get(run_id).status in ACTIVE_STATUSES or t is None:
            if self.get(run_id).status in ACTIVE_STATUSES:
                self.set_status(run_id, "cancelled")
        return {"cancelled": run_id, "jobs_killed": [j["name"] for j in killed]}

    async def shutdown(self) -> None:
        for rid, t in list(self.tasks.items()):
            if not t.done():
                t.cancel()

    def recover(self) -> None:
        """Runs that were active when the server stopped are marked interrupted."""
        with db.session() as s:
            for r in s.scalars(select(db.Run).where(db.Run.status.in_(ACTIVE_STATUSES))):
                r.status = "interrupted"
                s.add(db.Event(run_id=r.id, type="run_status", data={"status": "interrupted", "note": "server restarted"}, ts=time.time()))

    # -- demo ------------------------------------------------------------------------------
    async def _demo(self, run_id: str) -> None:
        """Render fan_unfold on the bundled sample logo — no LLM, no ElevenLabs."""
        from ..routes_projects import ensure_workspace, file_url

        run = self.get(run_id)
        pid = run.project_id
        pdir = ensure_workspace(pid)
        self.set_status(run_id, "running")
        bus.publish(run_id, "text_delta", {"delta": "Rendering the **fan_unfold** template on the bundled *Veyra* sample logo — "
                                                    "no LLM and no API keys involved. This proves the engine, audio, encoder and QC work.\n"})
        out_rel = "renders/demo"
        out_dir = pdir / out_rel
        if out_dir.exists() and run_id not in self._resuming:
            shutil.rmtree(out_dir, ignore_errors=True)
        self._resuming.discard(run_id)
        s = db.get_setting("demo_overrides") or {}  # tests use a smaller render
        width, height = int(s.get("width", 1920)), int(s.get("height", 1080))
        fps, duration = float(s.get("fps", 60)), float(s.get("duration", 5.0))
        tcid = f"demo_{run_id}"
        args = {"template": "fan_unfold", "logo": "assets/veyra-symbol.svg", "wordmark": "Veyra", "size": f"{width}x{height}", "fps": fps, "duration": duration}
        bus.publish(run_id, "tool_call_start", {"id": tcid, "name": "render_final", "index": 0})
        bus.publish(run_id, "tool_args_delta", {"id": tcid, "delta": json.dumps(args)})
        py = config.sandbox_python or sys.executable
        workers = max(1, min(os.cpu_count() or 1, 8))
        cmd = (f"{py} -m luma_engine demo --out {out_rel} --workers {workers} --width {width} --height {height} "
               f"--fps {fps} --duration {duration} --logo assets/veyra-symbol.svg")
        t0 = time.time()
        job = await asyncio.to_thread(jobs.spawn, pid, f"demo-{run_id[-6:]}", cmd, run_id, str(pdir), tcid)
        res = await jobs.wait(job["id"], 3600)
        manifest_p = out_dir / "manifest.json"
        if res["status"] != "done" or not manifest_p.exists():
            poll = jobs.poll(pid, job["name"], 30)
            bus.publish(run_id, "tool_result", {"id": tcid, "name": "render_final", "status": "error", "seconds": round(time.time() - t0, 1),
                                                "output": poll.get("log_tail", "")})
            self.set_status(run_id, "failed", error=f"demo render {res['status']} (exit {res.get('exit_code')})")
            return
        man = json.loads(manifest_p.read_text())
        outputs = []
        (pdir / "outputs").mkdir(exist_ok=True)
        for key, name in (("mp4", "luma_demo.mp4"), ("end_card", "luma_demo_end_card.png")):
            src = Path(man[key])
            if not src.is_absolute():
                src = pdir / src
            dst = pdir / "outputs" / name
            shutil.copy2(src, dst)
            outputs.append({"kind": key, "path": f"outputs/{name}", "url": file_url(pid, f"outputs/{name}")})
        wav = out_dir / "mix.wav"
        if wav.exists():
            shutil.copy2(wav, pdir / "audio" / "demo_mix.wav")
            bus.publish(run_id, "audio", {"path": "audio/demo_mix.wav", "url": file_url(pid, "audio/demo_mix.wav"), "label": "Demo sound design (final mix)",
                                          "kind": "mix", **{k: man["audio"].get(k) for k in ("lufs", "true_peak_dbtp", "duration_s")}})
            try:
                from luma_engine import audio as A

                x = A.read_audio(str(wav))
                ev = json.loads((out_dir / "events.json").read_text())["events"]
                A.spectrogram_png(x, str(pdir / "renders" / "demo" / "spectrogram.png"), events=ev, title="demo mix")
                bus.publish(run_id, "image", {"path": f"{out_rel}/spectrogram.png", "url": file_url(pid, f"{out_rel}/spectrogram.png"), "label": "Mix spectrogram + events", "kind": "spectrogram"})
            except Exception:
                pass
        qc_p = out_dir / "qc.json"
        if qc_p.exists():
            shutil.copy2(qc_p, pdir / "outputs" / "qc_report.json")
            outputs.append({"kind": "qc", "path": "outputs/qc_report.json", "url": file_url(pid, "outputs/qc_report.json")})
        bus.publish(run_id, "image", {"path": "outputs/luma_demo_end_card.png", "url": file_url(pid, "outputs/luma_demo_end_card.png"), "label": "End card (reference lockup)", "kind": "end_card"})
        for o in outputs:
            bus.publish(run_id, "artifact", o)
        bus.publish(run_id, "tool_result", {"id": tcid, "name": "render_final", "status": "success", "seconds": round(time.time() - t0, 1),
                                            "output": json.dumps({"qc": man.get("qc"), "frames": man["frames"]["frames"], "audio": man.get("audio", {}).get("lufs")})})
        qc = man.get("qc") or {}
        summary = (f"Demo complete: {man['frames']['frames']} frames at {fps:g} fps ({duration:g} s), "
                   f"QC {'passed' if qc.get('pass') else 'FAILED: ' + ', '.join(qc.get('failed', []))}.")
        bus.publish(run_id, "text_delta", {"delta": "\n" + summary})
        from .. import checkpoints

        await asyncio.to_thread(checkpoints.auto, pid, run_id, "Demo render finished")
        self.set_status(run_id, "completed", summary=summary, outputs=outputs)


def _resumable_job(command: str) -> bool:
    """Renders resume (existing frames are skipped), so they can simply be started again."""
    return "luma_engine pipeline" in command or "luma_engine render" in command or "luma_engine demo" in command


runs = RunManager()
