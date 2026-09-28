"""Typed, persisted, replayable run events with live fan-out (SSE).

Event types: see ``EVENT_TYPES`` (streaming, tools, media, run lifecycle, plus the
long-job types: plan, memory, checkpoints, artifacts/presentations, user requests,
notifications, stages, budget, sub-agents, compaction).  IDs are the SQLite autoincrement primary key, so they are
monotonically increasing across the whole database; clients resume with
``Last-Event-ID``.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncIterator

from sqlalchemy import select

from . import db
from .secrets_store import redact_obj

EVENT_TYPES = {
    "text_delta", "tool_call_start", "tool_args_delta", "tool_output_delta", "tool_result", "image", "audio",
    "progress", "artifact", "usage", "error", "run_status", "user_message", "job", "retry", "context",
    # long jobs, presentation and collaboration
    "todo_update", "plan_revision", "memory_update", "checkpoint", "present", "ask_user", "approval_request",
    "approval_result", "options_request", "notify", "progress_stage", "budget", "subagent_start", "subagent_end",
    "compaction", "system_note",
    # self-extending toolbox
    "tool_created", "tool_tested", "tool_registered", "tool_updated", "tool_promoted", "tool_disabled", "skill_written", "plugin_created",
    "template_saved",
}


class EventBus:
    def __init__(self):
        self._subs: dict[str, set[asyncio.Queue]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish(self, run_id: str, type: str, data: dict | None = None) -> dict:
        """Persist and fan out. Safe to call from any thread."""
        if type not in EVENT_TYPES:
            raise ValueError(f"unknown event type {type}")
        data = redact_obj(data or {})
        ts = time.time()
        with db.session() as s:
            row = db.Event(run_id=run_id, type=type, data=data, ts=ts)
            s.add(row)
            s.flush()
            eid = row.id
        evt = {"id": eid, "run_id": run_id, "type": type, "data": data, "ts": ts}
        self._fanout(run_id, evt)
        return evt

    def _fanout(self, run_id: str, evt: dict) -> None:
        queues = list(self._subs.get(run_id, ()))
        if not queues:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        for q in queues:
            if running is not None and running is self._loop:
                _put(q, evt)
            elif self._loop is not None:
                self._loop.call_soon_threadsafe(_put, q, evt)

    def subscribe(self, run_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=10000)
        self._subs.setdefault(run_id, set()).add(q)
        return q

    def unsubscribe(self, run_id: str, q: asyncio.Queue) -> None:
        s = self._subs.get(run_id)
        if s:
            s.discard(q)
            if not s:
                self._subs.pop(run_id, None)

    def history(self, run_id: str, after: int = 0, limit: int | None = None) -> list[dict]:
        with db.session() as s:
            q = select(db.Event).where(db.Event.run_id == run_id, db.Event.id > after).order_by(db.Event.id)
            if limit:
                q = q.limit(limit)
            return [{"id": e.id, "run_id": e.run_id, "type": e.type, "data": e.data, "ts": e.ts} for e in s.scalars(q)]


def _put(q: asyncio.Queue, evt: dict) -> None:
    try:
        q.put_nowait(evt)
    except asyncio.QueueFull:  # slow client: drop; it will resume from the DB on reconnect
        pass


bus = EventBus()


def sse_format(evt: dict) -> str:
    return f"id: {evt['id']}\nevent: {evt['type']}\ndata: {json.dumps(evt, separators=(',', ':'))}\n\n"


async def sse_stream(run_id: str, last_id: int, is_disconnected, keepalive: float = 15.0) -> AsyncIterator[str]:
    """Replay history after ``last_id`` then stream live events (no gaps, no dups)."""
    q = bus.subscribe(run_id)  # subscribe first so nothing published during replay is lost
    try:
        yield "retry: 2000\n\n"
        for evt in bus.history(run_id, last_id):
            last_id = evt["id"]
            yield sse_format(evt)
        while True:
            if await is_disconnected():
                break
            try:
                evt = await asyncio.wait_for(q.get(), timeout=keepalive)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            if evt["id"] <= last_id:
                continue
            last_id = evt["id"]
            yield sse_format(evt)
    finally:
        bus.unsubscribe(run_id, q)


class Coalescer:
    """Batches high-frequency deltas (text, tool args, terminal output) into ~50 ms
    events so the DB and the browser are not flooded."""

    def __init__(self, run_id: str, interval: float = 0.05):
        self.run_id = run_id
        self.interval = interval
        self._buf: dict[tuple, list[str]] = {}
        self._meta: dict[tuple, dict] = {}
        self._last = time.monotonic()

    def add(self, type: str, key: str, text: str, **meta) -> None:
        k = (type, key)
        self._buf.setdefault(k, []).append(text)
        self._meta[k] = meta
        if time.monotonic() - self._last >= self.interval:
            self.flush()

    def flush(self) -> None:
        self._last = time.monotonic()
        buf, self._buf = self._buf, {}
        for (type, key), parts in buf.items():
            text = "".join(parts)
            if not text:
                continue
            data = {**self._meta.get((type, key), {}), "delta": text}
            bus.publish(self.run_id, type, data)
