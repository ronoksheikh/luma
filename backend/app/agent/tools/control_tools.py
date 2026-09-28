"""ask_user / finish."""
from __future__ import annotations

import json
import zipfile

from .base import ToolContext, ToolError, ToolOutput, tool

OUTPUT_KINDS = {".mp4": "mp4", ".mov": "prores", ".png": "end_card", ".srt": "srt", ".zip": "source_zip", ".wav": "audio", ".json": "qc"}


@tool(
    "ask_user",
    """Pause and ask the user a question — only when a decision is genuinely theirs. Renders buttons/chips (`options`)
    plus a text box (allow_free_text). multi_select lets them pick several. With timeout_s the run continues with
    `default` (an option) when nobody answers. Returns their answer.""",
    {"question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
     "allow_free_text": {"type": "boolean", "default": True}, "multi_select": {"type": "boolean", "default": False},
     "timeout_s": {"type": "number", "description": "seconds to wait before using `default`"}, "default": {"type": "string"}},
    ["question"],
)
async def ask_user(ctx: ToolContext, a: dict) -> ToolOutput:
    q = str(a.get("question") or "").strip()
    if not q:
        raise ToolError("question is empty")
    opts = [str(o) for o in (a.get("options") or [])][:8]
    free = bool(a.get("allow_free_text", True))
    if not opts and not free:
        raise ToolError("give options or allow free text")
    timeout = float(a["timeout_s"]) if a.get("timeout_s") else None
    default = None
    if timeout:
        d = a.get("default") or (opts[0] if opts else None)
        if d is None:
            raise ToolError("timeout_s needs a default answer")
        default = {"selections": [d]} if opts else {"text": d}
    payload = {"question": q, "options": opts, "allow_free_text": free, "multi_select": bool(a.get("multi_select"))}
    ans = await ctx.runner.request_user(ctx.run_id, "ask", payload, ctx.tool_call_id, timeout, default)
    sel = [str(x) for x in ans.get("selections") or []]
    text = str(ans.get("text") or "").strip()
    prefix = "No answer before the timeout; using the default: " if ans.get("timeout") else ""
    if sel and len(sel) > 1:
        body = f"The user selected: {', '.join(sel)}"
    elif sel:
        body = f"The user answered: {sel[0]}"
    else:
        body = f"The user answered: {text}"
    if sel and text:
        body += f"\nThey added: {text}"
    return ToolOutput(prefix + body)


@tool(
    "request_approval",
    """A formal sign-off gate with the drafts attached (artifact ids from present_*). REQUIRED before expensive steps —
    a 4K or long final render, a long ElevenLabs generation, continuing past the run's cost/time thresholds — unless the
    project is on Autopilot. The run pauses until the user chooses. Returns APPROVED or the requested changes.""",
    {"title": {"type": "string"}, "summary": {"type": "string", "description": "what you are asking them to approve and what happens next (cost/time)"},
     "artifacts": {"type": "array", "items": {"type": "string"}}, "choices": {"type": "array", "items": {"type": "string"}, "maxItems": 4}},
    ["title", "summary"],
)
async def request_approval(ctx: ToolContext, a: dict) -> ToolOutput:
    from ... import artifacts as A

    arts = []
    for aid in a.get("artifacts") or []:
        try:
            art = A.get(str(aid))
        except A.ArtifactError:
            raise ToolError(f"no artifact {aid} — present the drafts first (present_video/present_storyboard/…)") from None
        if art["project_id"] != ctx.project_id:
            raise ToolError(f"no artifact {aid}")
        arts.append(art)
    choices = [str(c) for c in (a.get("choices") or ["Approve", "Request changes"])][:4]
    payload = {"title": str(a["title"])[:200], "summary": str(a["summary"])[:4000], "artifacts": arts, "choices": choices}
    if ctx.settings.get("project", {}).get("autopilot"):
        with_db = await _record_auto_approval(ctx, payload)
        return ToolOutput(f"APPROVED automatically (project Autopilot is on). Request {with_db}.")
    ans = await ctx.runner.request_user(ctx.run_id, "approval", payload, ctx.tool_call_id)
    choice = ans.get("choice") or ""
    note = str(ans.get("note") or ans.get("text") or "").strip()
    if choice == choices[0]:
        return ToolOutput(f"APPROVED by the user ({choice}).{(' Note: ' + note) if note else ''}")
    if not choice and note:
        return ToolOutput(f"NOT APPROVED — the user replied: {note}. Address it and ask again.")
    return ToolOutput(f"NOT APPROVED — the user chose “{choice}”.{(' Requested changes: ' + note) if note else ''} Revise, present again and re-request approval.")


async def _record_auto_approval(ctx: ToolContext, payload: dict) -> str:
    import time

    from ... import db
    from ...events import bus

    with db.session() as s:
        q = db.UserRequest(run_id=ctx.run_id, kind="approval", payload=payload, tool_call_id=ctx.tool_call_id, status="answered",
                           answer={"choice": payload["choices"][0], "auto": True}, answered_at=time.time())
        s.add(q)
        s.flush()
        qid = q.id
    bus.publish(ctx.run_id, "approval_request", {"request_id": qid, "tool_call_id": ctx.tool_call_id, "auto": True, **payload})
    bus.publish(ctx.run_id, "approval_result", {"request_id": qid, "kind": "approval", "answer": {"choice": payload["choices"][0], "auto": True},
                                                "status": "answered", "tool_call_id": ctx.tool_call_id})
    return qid


@tool(
    "present_options",
    """Offer directions to choose from ("here are 3 directions"), each with a label, description and optional
    preview_artifact (an artifact id: storyboard still, voice preview, video). The run pauses; the pick is returned.""",
    {"title": {"type": "string"}, "options": {"type": "array", "minItems": 2, "maxItems": 6, "items": {"type": "object", "properties": {
        "label": {"type": "string"}, "description": {"type": "string"}, "preview_artifact": {"type": "string"}}, "required": ["label"]}}},
    ["title", "options"],
)
async def present_options(ctx: ToolContext, a: dict) -> ToolOutput:
    from ... import artifacts as A

    opts = []
    for o in a["options"][:6]:
        prev = None
        if o.get("preview_artifact"):
            try:
                prev = A.get(str(o["preview_artifact"]))
            except A.ArtifactError:
                raise ToolError(f"no artifact {o['preview_artifact']}") from None
        opts.append({"label": str(o["label"])[:80], "description": str(o.get("description") or "")[:600], "preview": prev})
    if len({o["label"] for o in opts}) != len(opts):
        raise ToolError("option labels must be unique")
    payload = {"title": str(a["title"])[:200], "options": opts}
    ans = await ctx.runner.request_user(ctx.run_id, "options", payload, ctx.tool_call_id)
    choice = ans.get("choice")
    note = str(ans.get("note") or ans.get("text") or "").strip()
    if choice:
        return ToolOutput(f"The user picked “{choice}”.{(' Note: ' + note) if note else ''}")
    return ToolOutput(f"The user did not pick an option and replied: {note}")


@tool(
    "notify",
    "Toast + browser notification for the user (e.g. 'Final render done', 'Waiting for your approval').",
    {"message": {"type": "string"}, "level": {"type": "string", "enum": ["info", "success", "warning", "error"], "default": "info"}},
    ["message"],
)
async def notify(ctx: ToolContext, a: dict) -> ToolOutput:
    from ... import db

    msg = str(a["message"]).strip()[:500]
    level = a.get("level") or "info"
    if level not in ("info", "success", "warning", "error"):
        raise ToolError("level must be info, success, warning or error")
    with db.session() as s:
        n = db.Notification(project_id=ctx.project_id, run_id=ctx.run_id, level=level, message=msg)
        s.add(n)
        s.flush()
        nid = n.id
    ctx.emit("notify", {"id": nid, "message": msg, "level": level})
    return ToolOutput("notified")


@tool(
    "report_progress",
    "Update the run's progress header (overall stage, percent, ETA) — separate from per-tool progress bars.",
    {"stage": {"type": "string"}, "percent": {"type": "number"}, "eta_s": {"type": "number"}, "detail": {"type": "string"}},
    ["stage", "percent"],
)
async def report_progress(ctx: ToolContext, a: dict) -> ToolOutput:
    pct = max(0.0, min(100.0, float(a["percent"])))
    ctx.emit("progress_stage", {"stage": str(a["stage"])[:80], "percent": pct, "eta_s": a.get("eta_s"), "detail": str(a.get("detail") or "")[:300]})
    return ToolOutput(f"progress: {a['stage']} {pct:.0f}%")


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
