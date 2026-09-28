"""Runs (SSE events, messages, cancel), jobs, the demo, and the terminal WebSocket."""
from __future__ import annotations

import asyncio
import json
import shutil

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from . import db
from .agent.runner import runs
from .config import config
from .events import sse_stream
from .jobs import jobs
from .routes_projects import add_asset_bytes, ensure_workspace, get_project, project_json
from .secrets_store import resolve
from .security import host_ok, origin_ok
from .terminal import terminals

router = APIRouter()


def run_json(r: db.Run) -> dict:
    return {k: getattr(r, k) for k in ("id", "project_id", "kind", "status", "model", "steps", "prompt_tokens", "completion_tokens",
                                        "el_chars", "error", "summary", "outputs", "created_at", "updated_at")} | {"active": runs.is_active(r.id)}


class MessageIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=50000)


@router.get("/api/projects/{project_id}/runs")
def list_runs(project_id: str):
    get_project(project_id)
    with db.session() as s:
        return [run_json(r) for r in s.scalars(select(db.Run).where(db.Run.project_id == project_id).order_by(db.Run.created_at.desc()))]


@router.post("/api/projects/{project_id}/runs", status_code=201)
async def create_run(project_id: str, body: MessageIn, request: Request):
    get_project(project_id)
    creds = resolve(request)
    if not creds.has_llm:
        raise HTTPException(400, "No LLM configured. Open Settings and add a base URL, model and API key.")
    r = runs.create(project_id, "agent", creds.llm_model)
    runs.creds[r.id] = creds
    await runs.post_message(r.id, body.text, creds)
    return run_json(runs.get(r.id))


@router.get("/api/runs/{run_id}")
def get_run(run_id: str):
    r = runs.get(run_id)
    if r is None:
        raise HTTPException(404, "run not found")
    return run_json(r)


@router.get("/api/runs/{run_id}/events")
async def run_events(run_id: str, request: Request, after: int | None = None):
    if runs.get(run_id) is None:
        raise HTTPException(404, "run not found")
    last = request.headers.get("last-event-id")
    last_id = int(last) if last and last.isdigit() else (after or 0)
    return StreamingResponse(sse_stream(run_id, last_id, request.is_disconnected), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@router.post("/api/runs/{run_id}/message")
async def post_message(run_id: str, body: MessageIn, request: Request):
    r = runs.get(run_id)
    if r is None:
        raise HTTPException(404, "run not found")
    creds = resolve(request)
    if r.kind == "agent" and not runs.is_active(run_id) and run_id not in runs.answers and not creds.has_llm and run_id not in runs.creds:
        raise HTTPException(400, "No LLM configured. Open Settings and add a base URL, model and API key.")
    await runs.post_message(run_id, body.text, creds if creds.has_llm else None)
    return run_json(runs.get(run_id))


@router.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str):
    try:
        return await runs.cancel(run_id)
    except KeyError:
        raise HTTPException(404, "run not found") from None


@router.get("/api/projects/{project_id}/jobs")
def list_jobs(project_id: str):
    return jobs.list(project_id)


@router.post("/api/projects/{project_id}/jobs/{name}/kill")
def kill_job(project_id: str, name: str):
    try:
        return jobs.kill(project_id, name)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(404, str(e)) from None


# ======================================================================================
# demo
# ======================================================================================


@router.post("/api/demo", status_code=201)
async def start_demo():
    with db.session() as s:
        p = s.scalars(select(db.Project).where(db.Project.kind == "demo")).first()
        if p is None:
            p = db.Project(name="Demo — Veyra outro", kind="demo",
                           brief="Keyless demo: the fan_unfold template on the bundled original sample logo (Veyra).",
                           settings={"width": 1920, "height": 1080, "fps": 60, "duration": 5.0, "formats": ["mp4"], "avoid_colors": ["#FF0000"],
                                     "voice_language": "en", "voice_tone": "", "captions": False})
            s.add(p)
    pid = p.id
    pdir = ensure_workspace(pid)
    for name in ("veyra-symbol.svg", "veyra-brand.pdf"):
        if not (pdir / "assets" / name).exists():
            src = config.samples_dir / name
            await asyncio.to_thread(add_asset_bytes, pid, name, src.read_bytes())
    for r in (runs.get(x) for x in list(runs.tasks)):
        if r is not None and r.project_id == pid and runs.is_active(r.id):
            return {"project": project_json(get_project(pid)), "run": run_json(r)}
    r = runs.create(pid, "demo", "")
    runs.start(r.id)
    return {"project": project_json(get_project(pid)), "run": run_json(runs.get(r.id))}


# ======================================================================================
# terminal websocket
# ======================================================================================


@router.websocket("/ws/terminal/{project_id}")
async def terminal_ws(ws: WebSocket, project_id: str):
    if not host_ok(ws.headers.get("host")) or not origin_ok(ws.headers.get("origin"), ws.headers.get("host")):
        await ws.close(code=4403)
        return
    with db.session() as s:
        if s.get(db.Project, project_id) is None:
            await ws.close(code=4404)
            return
    await ws.accept()
    ensure_workspace(project_id)
    term = terminals.get(project_id)
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue(maxsize=5000)

    def on_out(text: str):
        loop.call_soon_threadsafe(_put, q, {"type": "output", "data": text})

    def on_status(st: dict):
        loop.call_soon_threadsafe(_put, q, st)

    term.listeners.add(on_out)
    term.status_listeners.add(on_status)
    try:
        await asyncio.to_thread(term.ensure)
        await ws.send_text(json.dumps({"type": "output", "data": term.scrollback}))
        await ws.send_text(json.dumps(term.status()))

        async def pump():
            while True:
                msg = await q.get()
                if msg.get("type") == "output":  # merge bursts
                    parts = [msg["data"]]
                    while not q.empty() and len(parts) < 200:
                        nxt = q.get_nowait()
                        if nxt.get("type") != "output":
                            await ws.send_text(json.dumps({"type": "output", "data": "".join(parts)}))
                            parts = []
                            await ws.send_text(json.dumps(nxt))
                            continue
                        parts.append(nxt["data"])
                    if parts:
                        await ws.send_text(json.dumps({"type": "output", "data": "".join(parts)}))
                else:
                    await ws.send_text(json.dumps(msg))

        sender = asyncio.create_task(pump())
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                t = msg.get("type")
                if t == "input" and term.takeover:
                    term.write(str(msg.get("data", ""))[:4096])
                elif t == "resize":
                    term.resize(int(msg.get("cols", 120)), int(msg.get("rows", 32)))
                elif t == "takeover":
                    term.set_takeover(bool(msg.get("on")))
                elif t == "interrupt":
                    term.interrupt()
        finally:
            sender.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        term.listeners.discard(on_out)
        term.status_listeners.discard(on_status)


def _put(q: asyncio.Queue, item) -> None:
    try:
        q.put_nowait(item)
    except asyncio.QueueFull:
        pass


_ = shutil
