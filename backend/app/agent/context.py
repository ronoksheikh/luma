"""Context management: pinned project facts, token budgeting, summarisation, images."""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path

from sqlalchemy import select

from .. import db
from ..assets import summarize_for_llm
from ..config import DEFAULT_PROJECT_SETTINGS

MAX_IMAGE_MESSAGES = 3  # only the most recent images are sent as pixels
PIN_FILES = [("work/brand.json", 5000), ("work/plan.md", 6000), ("work/events.json", 5000)]


def estimate_tokens(obj) -> int:
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, default=str)
    return int(len(s) / 3.6) + 4


def pinned_facts(project_id: str, pdir: Path, el_status: str, vision: bool) -> str:
    with db.session() as s:
        p = s.get(db.Project, project_id)
        assets = list(s.scalars(select(db.Asset).where(db.Asset.project_id == project_id).order_by(db.Asset.created_at)))
    st = {**DEFAULT_PROJECT_SETTINGS, **(p.settings or {})}
    lines = ["# Pinned project facts (always current)", "", "## Brief", (p.brief or "(no brief — ask the user's first message)").strip(), "",
             "## Output settings",
             f"- resolution {st['width']}x{st['height']}, {st['fps']} fps, duration {st['duration']} s "
             f"(= {int(round(st['duration'] * st['fps']))} frames), formats {st['formats']}",
             f"- colours to avoid: {st['avoid_colors'] or 'none'}",
             f"- voice-over language/tone: {st['voice_language']} / {st['voice_tone'] or 'unspecified'}; captions {'on' if st['captions'] else 'off'}",
             f"- workspace (cwd of the terminal): {pdir}  → assets/ work/ renders/ audio/ outputs/",
             f"- vision: {'available — you SEE images returned by render_preview/view_image' if vision else 'NOT available — rely on numeric stats and QC'}",
             f"- ElevenLabs: {el_status}", "", "## Assets"]
    if not assets:
        lines.append("(no uploads)")
    for a in assets:
        lines.append(f"- {a.path} ({a.kind}, {a.size // 1024} KB): {summarize_for_llm({'kind': a.kind, 'analysis': a.analysis})[:2500]}")
    for rel, cap in PIN_FILES:
        f = pdir / rel
        if f.exists():
            txt = f.read_text(errors="replace")
            lines += ["", f"## {rel} (pinned; keep it up to date)", txt[:cap] + ("\n…(truncated)" if len(txt) > cap else "")]
    return "\n".join(lines)


def image_part(path: Path, max_side: int = 1536) -> dict | None:
    try:
        from PIL import Image

        im = Image.open(path).convert("RGB")
        im.thumbnail((max_side, max_side))
        b = io.BytesIO()
        im.save(b, "JPEG", quality=85)
        return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode(), "detail": "high"}}
    except Exception:
        return None


def materialize(messages: list[dict], pdir: Path, vision: bool) -> list[dict]:
    """Turn stored messages into API messages: luma_image parts become data URLs for the
    most recent few image messages; older ones become short text placeholders."""
    img_idx = [i for i, m in enumerate(messages) if isinstance(m.get("content"), list) and any(p.get("type") == "luma_image" for p in m["content"])]
    keep = set(img_idx[-MAX_IMAGE_MESSAGES:]) if vision else set()
    out = []
    for i, m in enumerate(messages):
        c = m.get("content")
        if isinstance(c, list):
            parts = []
            for p in c:
                if p.get("type") == "luma_image":
                    part = image_part(pdir / p["path"]) if i in keep else None
                    parts.append(part or {"type": "text", "text": f"[image {p['path']} omitted from context — call view_image to look again]"})
                else:
                    parts.append(p)
            if not vision:
                parts = [{"type": "text", "text": "\n".join(p.get("text", "") for p in parts if p.get("type") == "text")}]
            m = {**m, "content": parts}
        out.append(m)
    return out


def load_history(run_id: str) -> list[tuple[int, dict]]:
    with db.session() as s:
        rows = list(s.scalars(select(db.Message).where(db.Message.run_id == run_id, db.Message.archived == 0).order_by(db.Message.id)))
    return [(r.id, r.content) for r in rows]


def split_for_summary(history: list[tuple[int, dict]], keep_last: int = 14) -> tuple[list[tuple[int, dict]], list[tuple[int, dict]]]:
    """Old part / recent part, never separating assistant tool_calls from their tool results."""
    if len(history) <= keep_last + 2:
        return [], history
    cut = len(history) - keep_last
    while cut < len(history) and history[cut][1].get("role") == "tool":
        cut += 1
    while cut > 0 and history[cut][1].get("role") == "tool":
        cut -= 1
    return history[:cut], history[cut:]


SUMMARY_PROMPT = """Summarise the earlier part of this motion-design session so the director can continue seamlessly.
Keep: the user's requests and feedback (verbatim where short), brand facts discovered (exact hex colours, fonts,
geometry), the plan/shot list and event timings, files created and what each contains, render/QC results, ElevenLabs
assets generated (paths, voice IDs, word-timing files), open problems and next steps. Be concise but complete; use
bullet points. Do not invent anything."""
