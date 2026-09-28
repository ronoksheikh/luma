"""Run budget: tokens and estimated cost (OpenRouter pricing when known), ElevenLabs characters,
wall-clock time and steps — plus the approval gates for expensive actions."""
from __future__ import annotations

import os
import time

from sqlalchemy import select

from .. import db

EL_TOOLS = {"el_tts": "text", "el_sound_effect": "text", "el_music": "prompt", "el_design_voice": "text"}
EXPENSIVE = {"render_final", "batch_render", "reframe_export", "spawn_subagent"}


def pricing(base_url: str, model: str) -> dict | None:
    return (db.get_setting("model_pricing") or {}).get(f"{base_url}|{model}")


def step_cost(pr: dict | None, usage: dict | None) -> float:
    if not usage:
        return 0.0
    if usage.get("cost") is not None:  # providers that report the charged cost (OpenRouter usage accounting)
        return float(usage["cost"])
    if not pr:
        return 0.0
    return float(usage.get("prompt_tokens") or 0) * pr["prompt"] + float(usage.get("completion_tokens") or 0) * pr["completion"]


def status(run: db.Run, settings: dict, started_at: float | None, base_url: str = "", model: str = "") -> dict:
    pr = pricing(base_url, model)
    wall = float(settings.get("wall_clock_minutes", 60)) * 60
    elapsed = time.time() - started_at if started_at else 0.0
    max_steps = int(settings.get("max_steps", 120))
    tokens = (run.prompt_tokens or 0) + (run.completion_tokens or 0)
    return {
        "steps": run.steps, "max_steps": max_steps, "steps_left": max(0, max_steps - run.steps),
        "prompt_tokens": run.prompt_tokens or 0, "completion_tokens": run.completion_tokens or 0, "tokens": tokens,
        "max_tokens": int(settings.get("max_tokens") or 0) or None,
        "cost_usd": round(run.cost_usd or 0.0, 4), "max_cost_usd": float(settings.get("max_cost_usd") or 0) or None,
        "pricing_known": bool(pr), "pricing": pr,
        "el_chars": run.el_chars or 0, "el_budget": int(settings.get("el_char_budget", 5000)),
        "elapsed_s": round(elapsed, 1), "wall_clock_s": wall,
    }


def estimate_render_minutes(settings: dict, rate_s_per_frame: float | None) -> float:
    st = settings.get("project", {})
    frames = float(st.get("duration", 5)) * float(st.get("fps", 60))
    px = float(st.get("width", 1920)) * float(st.get("height", 1080))
    rate = rate_s_per_frame if rate_s_per_frame else 0.6 * px / (1920 * 1080)
    workers = max(1, min(os.cpu_count() or 1, 8))
    return frames * rate / workers / 60


def approved_since(run_id: str, since: float) -> bool:
    with db.session() as s:
        rows = list(s.scalars(select(db.UserRequest).where(db.UserRequest.run_id == run_id, db.UserRequest.kind == "approval",
                                                          db.UserRequest.created_at >= since).order_by(db.UserRequest.created_at.desc())))
    for r in rows:
        if r.status != "answered":
            continue
        ch = (r.payload.get("choices") or ["Approve"])[0]
        return (r.answer or {}).get("choice") == ch
    return False


def last_user_message_at(run_id: str) -> float:
    with db.session() as s:
        m = s.scalars(select(db.Message).where(db.Message.run_id == run_id, db.Message.role == "user").order_by(db.Message.id.desc())).first()
        # system notes are stored as user messages too; they do not invalidate an approval
        while m is not None and isinstance(m.content.get("content"), str) and m.content["content"].startswith("[System note]"):
            m = s.scalars(select(db.Message).where(db.Message.run_id == run_id, db.Message.role == "user", db.Message.id < m.id)
                          .order_by(db.Message.id.desc())).first()
        return m.created_at if m else 0.0
