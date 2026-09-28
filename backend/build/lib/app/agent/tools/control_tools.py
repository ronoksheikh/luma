"""ask_user / finish."""
from __future__ import annotations

import json
import zipfile

from .base import ToolContext, ToolError, ToolOutput, tool

OUTPUT_KINDS = {".mp4": "mp4", ".mov": "prores", ".png": "end_card", ".srt": "srt", ".zip": "source_zip", ".wav": "audio", ".json": "qc"}


@tool(
    "ask_user",
    "Pause and ask the user a question (use sparingly — only when a decision genuinely needs them, e.g. ambiguous brand direction). Returns their answer.",
    {"question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}, "maxItems": 6}},
    ["question"],
)
async def ask_user(ctx: ToolContext, a: dict) -> ToolOutput:
    q = str(a.get("question") or "").strip()
    if not q:
        raise ToolError("question is empty")
    opts = [str(o) for o in (a.get("options") or [])][:6]
    ans = await ctx.runner.ask(ctx.run_id, q, opts, ctx.tool_call_id)
    return ToolOutput(f"The user answered: {ans}")


@tool(
    "finish",
    """Deliver the result and end the run. `outputs` are project paths (MP4 required; ProRes, end-card PNG, SRT,
    mixed WAV optional). A scene-source zip (work/*.py, *.json, *.md) and an end card (if missing) are added
    automatically. Only call after QC passes (or explain remaining issues in the summary).""",
    {"summary": {"type": "string", "description": "Markdown summary for the user: what you made, creative decisions, QC results."},
     "outputs": {"type": "array", "items": {"type": "string"}}},
    ["summary", "outputs"],
)
async def finish(ctx: ToolContext, a: dict) -> ToolOutput:
    outs = [str(o) for o in (a.get("outputs") or [])]
    if not outs:
        raise ToolError("list at least the final MP4 in outputs")
    items = []
    for o in outs:
        p = ctx.resolve(o, must_exist=True)
        items.append(p)
    if not any(p.suffix.lower() == ".mp4" for p in items):
        raise ToolError("outputs must include the final .mp4")
    out_dir = ctx.pdir / "outputs"
    out_dir.mkdir(exist_ok=True)
    # scene source zip
    zpath = out_dir / "scene_source.zip"
    work = ctx.pdir / "work"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(work.rglob("*")):
            if f.is_file() and f.suffix.lower() in (".py", ".json", ".md", ".txt", ".srt", ".svg") and ".luma" not in f.parts \
                    and "previews" not in f.parts and f.stat().st_size < 5_000_000:
                z.write(f, str(f.relative_to(ctx.pdir)))
    items.append(zpath)
    # end card if none given: last frame of the MP4
    if not any(p.suffix.lower() == ".png" for p in items):
        mp4 = next(p for p in items if p.suffix.lower() == ".mp4")
        card = out_dir / f"{mp4.stem}_end_card.png"
        import asyncio
        import subprocess

        def grab():
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-sseof", "-0.1", "-i", str(mp4), "-frames:v", "1", "-update", "1", str(card)],
                           capture_output=True, timeout=120)

        await asyncio.to_thread(grab)
        if card.exists():
            items.append(card)
    listed = []
    for p in items:
        rel = ctx.rel(p)
        kind = OUTPUT_KINDS.get(p.suffix.lower(), "file")
        listed.append({"kind": kind, "path": rel, "url": ctx.url(rel)})
        ctx.emit("artifact", {"kind": kind, "path": rel, "url": ctx.url(rel), "final": True})
    summary = str(a.get("summary") or "")
    ctx.runner.finished[ctx.run_id] = {"summary": summary, "outputs": listed}
    return ToolOutput(json.dumps({"delivered": listed}, indent=1), finish=True, ui={"outputs": listed, "summary": summary})
