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
            fut.set_result(text)  # answer to ask_user
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
        fut = asyncio.get_running_loop().create_future()
        self.answers[run_id] = fut
        self.set_status(run_id, "waiting_input", question=question, options=options or [], tool_call_id=tool_call_id)
        try:
            return await fut
        finally:
            self.answers.pop(run_id, None)
            self.set_status(run_id, "running")

    def take_inbox(self, run_id: str) -> list[str]:
        return self.inbox.pop(run_id, [])

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
        if out_dir.exists():
            shutil.rmtree(out_dir, ignore_errors=True)
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
        self.set_status(run_id, "completed", summary=summary, outputs=outputs)


runs = RunManager()
