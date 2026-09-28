"""Project memory: durable key → value facts (brand colours, chosen voice, approved style,
user preferences) that are pinned into every run of the project."""
from __future__ import annotations

import re
import time

from sqlalchemy import select

from . import db

MAX_KEY, MAX_VALUE, MAX_ENTRIES = 200, 4000, 300


class MemoryError_(Exception):
    pass


def entries(project_id: str) -> list[dict]:
    with db.session() as s:
        return [db.to_dict(m) for m in s.scalars(select(db.Memory).where(db.Memory.project_id == project_id).order_by(db.Memory.key))]


def write(project_id: str, key: str, value: str, source: str = "agent") -> dict:
    key = str(key or "").strip()
    value = str(value if value is not None else "").strip()
    if not key:
        raise MemoryError_("key is required")
    if len(key) > MAX_KEY or len(value) > MAX_VALUE:
        raise MemoryError_(f"key ≤ {MAX_KEY} and value ≤ {MAX_VALUE} characters")
    with db.session() as s:
        m = s.scalars(select(db.Memory).where(db.Memory.project_id == project_id, db.Memory.key == key)).first()
        if m is None:
            n = len(list(s.scalars(select(db.Memory.id).where(db.Memory.project_id == project_id))))
            if n >= MAX_ENTRIES:
                raise MemoryError_(f"memory is full ({MAX_ENTRIES} entries): delete or merge old entries")
            m = db.Memory(project_id=project_id, key=key, value=value, source=source)
            s.add(m)
        else:
            m.value, m.source, m.updated_at = value, source, time.time()
        s.flush()
        return db.to_dict(m)


def delete(project_id: str, key_or_id: str) -> bool:
    with db.session() as s:
        m = s.scalars(select(db.Memory).where(db.Memory.project_id == project_id,
                                              (db.Memory.key == key_or_id) | (db.Memory.id == key_or_id))).first()
        if m is None:
            return False
        s.delete(m)
        return True


def read(project_id: str, key: str | None = None) -> list[dict]:
    es = entries(project_id)
    return [e for e in es if e["key"] == key] if key else es


def search(project_id: str, query: str, limit: int = 10) -> list[dict]:
    words = [w for w in re.findall(r"\w+", (query or "").lower()) if len(w) > 1]
    if not words:
        return []
    scored = []
    for e in entries(project_id):
        hay = f"{e['key']} {e['value']}".lower()
        score = sum(hay.count(w) for w in words) + 2 * sum(1 for w in words if w in e["key"].lower())
        if score:
            scored.append((score, e))
    scored.sort(key=lambda x: -x[0])
    return [e for _, e in scored[:limit]]


def compact(project_id: str, max_chars: int = 4000) -> str:
    es = entries(project_id)
    if not es:
        return ""
    lines = ["## Project memory (durable decisions — respect them; update with memory_write)"]
    for e in es:
        who = " (user)" if e["source"] == "user" else ""
        lines.append(f"- {e['key']}{who}: {e['value']}")
    out = "\n".join(lines)
    return out if len(out) <= max_chars else out[:max_chars] + "\n…(truncated — use memory_search / memory_read)"
