"""inspect_asset / render_preview / view_image / render_final / audio_analyze / encode / qc_report."""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path

from sqlalchemy import select

from ... import db
from ...jobs import JobError, jobs
from ...terminal import sandbox_python
from .base import ToolContext, ToolError, ToolOutput, parse_json_tail, run_engine, tool, truncate


def _find_asset(ctx: ToolContext, path: str) -> db.Asset | None:
    with db.session() as s:
        rows = list(s.scalars(select(db.Asset).where(db.Asset.project_id == ctx.project_id)))
    for a in rows:
        if path in (a.path, a.filename, a.id, f"./{a.path}"):
            return a
    return None


@tool(
    "inspect_asset",
    """The stored automatic analysis of an uploaded asset (SVG geometry: paths, bboxes, fills, gradients incl.
    objectBoundingBox, viewBox, symbol/wordmark groups, pivot, symmetry; images: size, palette, transparency; PDFs:
    text, hex colours, fonts; audio: duration, LUFS, peak). For PDFs pass `pages` (1-based) to also LOOK at page
    renders (vision models).""",
    {"path": {"type": "string", "description": "e.g. assets/logo.svg (or the filename)"},
     "pages": {"type": "array", "items": {"type": "integer"}, "description": "PDF pages to view (1-based)"}},
    ["path"],
)
async def inspect_asset(ctx: ToolContext, a: dict) -> ToolOutput:
    asset = _find_asset(ctx, str(a["path"]))
    if asset is None:
        p = ctx.resolve(a["path"], must_exist=True)
        from luma_engine.analysis import analyze_file

        analysis = await asyncio.to_thread(analyze_file, str(p))
        kind = analysis.get("type")
    else:
        analysis, kind = asset.analysis or {}, asset.kind
    images = []
    if kind == "pdf" and a.get("pages"):
        for n in a["pages"][:6]:
            pages = analysis.get("pages") or []
            if 1 <= int(n) <= len(pages) and pages[int(n) - 1].get("image"):
                images.append(pages[int(n) - 1]["image"])
    text = json.dumps(analysis, indent=1, default=str)
    return ToolOutput(truncate(text, ctx, "analysis"), images=images if ctx.vision else [],
                      ui={"images": [{"path": i, "url": ctx.url(i)} for i in images]})


@tool(
    "render_preview",
    """Render labelled keyframes of a scene file into a contact sheet PNG (runs the scene in the sandbox via
    `python -m luma_engine preview`). Give `times` (seconds) or `frames`; default 8 evenly spaced. The sheet is shown
    to you (vision) or summarised numerically. LOOK at it critically: brand accuracy, timing, composition, legibility,
    artefacts. Iterate at least twice before the final render.""",
    {
        "scene_path": {"type": "string", "description": "e.g. work/scene.py"},
        "times": {"type": "array", "items": {"type": "number"}},
        "frames": {"type": "array", "items": {"type": "integer"}},
        "scale": {"type": "number", "default": 0.5, "description": "0.1–1.0 of the output resolution"},
        "motion_blur": {"type": "boolean", "default": True},
    },
    ["scene_path"],
)
async def render_preview(ctx: ToolContext, a: dict) -> ToolOutput:
    scene = ctx.resolve(a["scene_path"], must_exist=True)
    out_dir = ctx.pdir / "work" / "previews"
    out_dir.mkdir(parents=True, exist_ok=True)
    n = len(list(out_dir.glob("sheet_*.png"))) + 1
    out = out_dir / f"sheet_{n:03d}.png"
    scale = max(0.1, min(float(a.get("scale") or 0.5), 1.0))
    args = ["preview", str(scene), "--scale", str(scale), "--out", str(out)]
    if a.get("times"):
        args += ["--times", ",".join(str(float(t)) for t in a["times"][:24])]
    elif a.get("frames"):
        args += ["--frames", ",".join(str(int(f)) for f in a["frames"][:24])]
    if a.get("motion_blur") is False:
        args.append("--no-blur")
    args += _overrides(ctx)
    rc, text, _ = await run_engine(ctx, args, timeout=900)
    if rc != 0 or not out.exists():
        raise ToolError(f"preview failed (exit {rc}):\n{truncate(text, ctx, 'preview')}")
    res = parse_json_tail(text) or {}
    if res.get("seconds") and res.get("tiles"):  # seconds per tile at this scale → per full-size frame (for render estimates)
        ctx.runner.render_rate[ctx.run_id] = float(res["seconds"]) / len(res["tiles"]) / max(scale * scale, 0.01)
    rel = ctx.rel(out)
    ctx.emit("image", {"path": rel, "url": ctx.url(rel), "label": f"Contact sheet {n}", "kind": "contact_sheet", "tiles": res.get("tiles")})
    msg = f"contact sheet: {rel} ({res.get('size')}, {res.get('seconds')}s) tiles: {json.dumps(res.get('tiles'))}"
    if not ctx.vision:
        from luma_engine.qc import image_stats

        msg += "\n(no vision) image stats: " + json.dumps(await asyncio.to_thread(image_stats, str(out)))
    return ToolOutput(msg, images=[rel] if ctx.vision else [], ui={"images": [{"path": rel, "url": ctx.url(rel)}]})


def _overrides(ctx: ToolContext) -> list[str]:
    st = ctx.settings.get("project", {})
    out = []
    for k in ("width", "height", "fps", "duration"):
        if st.get(k):
            out += [f"--{k}", str(st[k])]
    return out


@tool(
    "view_image",
    """Look at an image (PNG/JPEG/WebP; SVG/PDF are rasterised) — optionally a zoomed crop [x, y, w, h] in pixels.
    With a vision model you receive the image; otherwise numeric stats (size, histogram, mean colour, sharpness,
    bright-region boxes).""",
    {"path": {"type": "string"}, "crop": {"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4}},
    ["path"],
)
async def view_image(ctx: ToolContext, a: dict) -> ToolOutput:
    p = ctx.resolve(a["path"], must_exist=True)
    import cv2

    from luma_engine.qc import image_stats

    def prep() -> tuple[str, dict]:
        src = p
        suf = p.suffix.lower()
        tmp_dir = ctx.pdir / "work" / "previews" / "views"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        if suf == ".svg":
            import cairosvg

            src = tmp_dir / (p.stem + "_svg.png")
            cairosvg.svg2png(url=str(p), write_to=str(src), output_width=1600)
        elif suf == ".pdf":
            import pymupdf

            src = tmp_dir / (p.stem + "_p1.png")
            pymupdf.open(str(p))[0].get_pixmap(dpi=110).save(str(src))
        img = cv2.imread(str(src), cv2.IMREAD_UNCHANGED)
        if img is None:
            raise ToolError("not a readable image")
        crop = a.get("crop")
        if crop:
            x, y, w, h = (int(v) for v in crop)
            img = img[max(0, y) : y + h, max(0, x) : x + w]
            if img.size == 0:
                raise ToolError("crop is outside the image")
        out = tmp_dir / f"view_{int(time.time() * 1000)}.png"
        if max(img.shape[:2]) < 512 and crop:
            s = 512 / max(img.shape[:2])
            img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_NEAREST)
        cv2.imwrite(str(out), img)
        rgb = cv2.cvtColor(img[..., :3] if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR), cv2.COLOR_BGR2RGB) / 255.0
        return ctx.rel(out), image_stats(rgb.astype("float32"))

    rel, stats = await asyncio.to_thread(prep)
    ctx.emit("image", {"path": rel, "url": ctx.url(rel), "label": f"view {ctx.rel(p)}" + (f" crop {a['crop']}" if a.get("crop") else ""), "kind": "view"})
    return ToolOutput(f"image {rel}; stats: {json.dumps(stats)}", images=[rel] if ctx.vision else [], ui={"images": [{"path": rel, "url": ctx.url(rel)}]})


@tool(
    "render_final",
    """Final render of a scene: frames (multi-process, real temporal motion blur, RESUMABLE — existing frames are
    skipped), reference end card, the scene's own audio() mixed to −14 LUFS / −1 dBTP, H.264 MP4 (+ ProRes), SRT
    from scene.captions(), and QC. Runs as a detached job with live progress; this call waits up to `wait_s`
    (default from settings) and otherwise returns the job name — poll with terminal_poll. Output: renders/<name>/.""",
    {"scene_path": {"type": "string"}, "name": {"type": "string", "default": "final"}, "prores": {"type": "boolean", "default": False},
     "wait_s": {"type": "integer"}},
    ["scene_path"],
)
async def render_final(ctx: ToolContext, a: dict) -> ToolOutput:
    scene = ctx.resolve(a["scene_path"], must_exist=True)
    name = str(a.get("name") or "final")
    if not name.replace("-", "").replace("_", "").isalnum():
        raise ToolError("name must be alphanumeric (plus - and _)")
    py = sandbox_python()
    prores = bool(a.get("prores")) or "prores" in (ctx.settings.get("project", {}).get("formats") or [])
    cmd = f"{py} -m luma_engine pipeline {scene} --out {ctx.pdir / 'renders' / name} --name {name}" + (" --prores" if prores else "")
    cmd += " " + " ".join(_overrides(ctx))
    job_name = f"render-{name}"
    existing = jobs.get(ctx.project_id, job_name)
    if existing and existing.status in ("queued", "running"):
        raise ToolError(f"{job_name} is already {existing.status}; poll it with terminal_poll or kill it")
    try:
        job = await asyncio.to_thread(jobs.spawn, ctx.project_id, job_name, cmd, ctx.run_id, str(ctx.pdir), ctx.tool_call_id)
    except JobError as e:
        raise ToolError(str(e)) from None
    wait = int(a.get("wait_s") or ctx.settings.get("tool_wait_seconds") or 600)
    res = await jobs.wait(job["id"], wait)
    man_p = ctx.pdir / "renders" / name / "manifest.json"
    if res["status"] in ("queued", "running"):
        prog = (jobs.by_id(job["id"]).progress or {})
        return ToolOutput(f"render still running as job '{job_name}' (progress {json.dumps(prog)}). Keep working (e.g. on audio) and "
                          f"check with terminal_poll('{job_name}'); re-calling render_final resumes and skips finished frames.",
                          ui={"job": job_name})
    if res["status"] != "done" or not man_p.exists():
        tail = jobs.poll(ctx.project_id, job_name, 60).get("log_tail", "")
        raise ToolError(f"render {res['status']} (exit {res.get('exit_code')}). Log tail:\n{tail}")
    man = json.loads(man_p.read_text())
    ui = {"images": []}
    for key in ("end_card",):
        if man.get(key):
            rel = ctx.rel(Path(man[key]))
            ctx.emit("image", {"path": rel, "url": ctx.url(rel), "label": "End card", "kind": "end_card"})
    if man.get("mp4"):
        rel = ctx.rel(Path(man["mp4"]))
        ctx.emit("artifact", {"kind": "preview_mp4", "path": rel, "url": ctx.url(rel), "label": f"{name}.mp4 (render)"})
    if man.get("audio"):
        rel = ctx.rel(Path(man["audio"]["path"]))
        ctx.emit("audio", {"path": rel, "url": ctx.url(rel), "label": "Scene sound design (mastered)", "kind": "mix",
                           "lufs": man["audio"].get("lufs"), "true_peak_dbtp": man["audio"].get("true_peak_dbtp")})
    if man.get("qc"):
        rel = ctx.rel(Path(man["qc"]["path"]))
        ctx.emit("artifact", {"kind": "qc", "path": rel, "url": ctx.url(rel)})
    from ...checkpoints import auto

    cp = await asyncio.to_thread(auto, ctx.project_id, ctx.run_id, f"Render “{name}” finished", render=name)
    res = _rel_manifest(ctx, man)
    if cp:
        res["checkpoint"] = cp["id"]
    return ToolOutput(json.dumps(res, indent=1), ui=ui)


def _rel_manifest(ctx: ToolContext, man: dict) -> dict:
    def fix(v):
        if isinstance(v, str) and v.startswith(str(ctx.pdir)):
            return ctx.rel(Path(v))
        if isinstance(v, dict):
            return {k: fix(x) for k, x in v.items()}
        if isinstance(v, list):
            return [fix(x) for x in v]
        return v

    return fix(man)


@tool(
    "audio_analyze",
    """Analyse an audio file (or a video's audio): duration, integrated LUFS, true peak, onsets, and a waveform +
    log-spectrogram PNG with event markers (pass an events JSON — e.g. renders/final/events.json or your
    work/events.json — to check picture/sound alignment).""",
    {"path": {"type": "string"}, "events_path": {"type": "string"}},
    ["path"],
)
async def audio_analyze(ctx: ToolContext, a: dict) -> ToolOutput:
    p = ctx.resolve(a["path"], must_exist=True)
    out = ctx.pdir / "work" / "previews" / f"spectrogram_{p.stem}_{int(time.time())}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    args = ["analyze-audio", str(p), "--png", str(out)]
    if a.get("events_path"):
        args += ["--events", str(ctx.resolve(a["events_path"], must_exist=True))]
    rc, text, _ = await run_engine(ctx, args, timeout=300, stream=False)
    if rc != 0:
        raise ToolError(f"audio analysis failed:\n{truncate(text, ctx, 'audio')}")
    res = parse_json_tail(text) or {}
    rel = ctx.rel(out)
    ctx.emit("image", {"path": rel, "url": ctx.url(rel), "label": f"Spectrogram · {p.name}", "kind": "spectrogram"})
    return ToolOutput(json.dumps({k: v for k, v in res.items() if k != "png"}, indent=1) + f"\nspectrogram: {rel}",
                      images=[rel] if ctx.vision else [], ui={"images": [{"path": rel, "url": ctx.url(rel)}]})


@tool(
    "encode",
    """Encode/mux a deliverable into outputs/: `source` is a frames folder (PNG sequence, e.g. renders/final/frames)
    or an existing video; `audio` (wav/mp3) is muxed (AAC 320k for MP4, PCM for ProRes). MP4 = H.264 High, CRF 12,
    yuv420p, BT.709 tags, +faststart. Optional `srt` captions: copied as a sidecar, and burned in when burn_captions.
    Make sure the audio length equals the video length (luma_engine.audio.fit_length).""",
    {"source": {"type": "string"}, "audio": {"type": "string"}, "format": {"type": "string", "enum": ["mp4", "prores"], "default": "mp4"},
     "out_name": {"type": "string", "default": "final"}, "fps": {"type": "number"}, "srt": {"type": "string"},
     "burn_captions": {"type": "boolean", "default": False}},
    ["source"],
)
async def encode(ctx: ToolContext, a: dict) -> ToolOutput:
    src = ctx.resolve(a["source"], must_exist=True)
    fmt = a.get("format") or "mp4"
    name = str(a.get("out_name") or "final")
    outs = ctx.pdir / "outputs"
    outs.mkdir(exist_ok=True)
    out = outs / (f"{name}.mp4" if fmt == "mp4" else f"{name}_prores.mov")
    audio = str(ctx.resolve(a["audio"], must_exist=True)) if a.get("audio") else None
    fps = float(a.get("fps") or ctx.settings.get("project", {}).get("fps") or 60)
    if src.is_dir():
        args = ["encode", "--frames", str(src), "--out", str(out), "--fps", str(fps)]
        if audio:
            args += ["--audio", audio]
        if fmt == "prores":
            args.append("--prores")
        rc, text, _ = await run_engine(ctx, args, timeout=3600)
        if rc != 0:
            raise ToolError(f"encode failed:\n{truncate(text, ctx, 'encode')}")
    else:
        from luma_engine import encode as E

        def work():
            if audio:
                E.mux(str(src), audio, str(out))
            elif src.resolve() != out.resolve():
                shutil.copy2(src, out)

        try:
            await asyncio.to_thread(work)
        except Exception as e:  # noqa: BLE001
            raise ToolError(f"mux failed: {e}") from None
    result = {"path": ctx.rel(out)}
    if a.get("srt"):
        srt = ctx.resolve(a["srt"], must_exist=True)
        side = outs / f"{name}.srt"
        if srt.resolve() != side.resolve():
            shutil.copy2(srt, side)
        result["srt"] = ctx.rel(side)
        ctx.emit("artifact", {"kind": "srt", "path": ctx.rel(side), "url": ctx.url(ctx.rel(side))})
        if a.get("burn_captions") and fmt == "mp4":
            from luma_engine import encode as E

            burned = outs / f"{name}_captioned.mp4"
            await asyncio.to_thread(E.burn_subtitles, str(out), str(side), str(burned))
            result["captioned"] = ctx.rel(burned)
            ctx.emit("artifact", {"kind": "mp4_captioned", "path": ctx.rel(burned), "url": ctx.url(ctx.rel(burned))})
    from luma_engine.encode import video_info

    info = await asyncio.to_thread(video_info, str(out))
    result["info"] = info
    ctx.emit("artifact", {"kind": "mp4" if fmt == "mp4" else "prores", "path": ctx.rel(out), "url": ctx.url(ctx.rel(out))})
    return ToolOutput(json.dumps(result, indent=1, default=str))


@tool(
    "qc_report",
    """Run QC on a delivered video: exact frame count / fps / duration (from project settings), first frame black
    (optional), flicker/discontinuity spikes, final frames still, final frame vs reference lockup PNG (mean abs diff),
    audio true peak ≤ −1 dBTP, loudness ≈ −14 LUFS, audio length = video length, A/V sync of marked events (events
    JSON with kind impact/seat/boom/hit/sync). Fix every failure and re-run.""",
    {"output": {"type": "string"}, "reference": {"type": "string"}, "events_path": {"type": "string"},
     "first_frame_black": {"type": "boolean", "default": False}, "target_lufs": {"type": "number", "default": -14}},
    ["output"],
)
async def qc_report(ctx: ToolContext, a: dict) -> ToolOutput:
    out = ctx.resolve(a["output"], must_exist=True)
    st = ctx.settings.get("project", {})
    fps = float(st.get("fps") or 60)
    dur = float(st.get("duration") or 0)
    report_p = ctx.pdir / "outputs" / f"qc_{out.stem}.json"
    args = ["qc", str(out), "--fps", str(fps), "--out", str(report_p), "--lufs", str(float(a.get("target_lufs") or -14))]
    if dur:
        from luma_engine.encode import video_info

        info = await asyncio.to_thread(video_info, str(out))
        # voiced pieces may legitimately extend the duration; only enforce frames when it matches the setting
        if abs(info.get("duration", 0) - dur) < 0.5 / fps or not a.get("allow_duration_change", True):
            args += ["--frames", str(int(round(dur * fps))), "--duration", str(dur)]
        else:
            args += ["--frames", str(info["frames"]), "--duration", str(info["frames"] / fps)]
    if a.get("reference"):
        args += ["--reference", str(ctx.resolve(a["reference"], must_exist=True))]
    if a.get("events_path"):
        args += ["--events", str(ctx.resolve(a["events_path"], must_exist=True))]
    if a.get("first_frame_black"):
        args.append("--first-black")
    rc, text, _ = await run_engine(ctx, args, timeout=900, stream=False)
    rep = parse_json_tail(text)
    if rc != 0 or not isinstance(rep, dict):
        raise ToolError(f"QC failed to run:\n{truncate(text, ctx, 'qc')}")
    rel = ctx.rel(report_p)
    ctx.emit("artifact", {"kind": "qc", "path": rel, "url": ctx.url(rel), "pass": rep.get("pass")})
    compact = {"pass": rep["pass"], "checks": [{k: c[k] for k in ("name", "pass", "value", "expected", "detail")} for c in rep["checks"]]}
    from ...review import sync_qc_todos

    sync = await asyncio.to_thread(sync_qc_todos, ctx.run_id, ctx.project_id, rep, rel)
    note = ""
    if sync["created"]:
        note += f"\n{len(sync['created'])} failing check(s) were added to the plan as BLOCKED items under 'Fix QC failures' — fix them and re-run qc_report."
    if sync["closed"]:
        note += f"\n{len(sync['closed'])} QC item(s) in the plan were closed: those checks pass now."
    return ToolOutput(truncate(json.dumps(compact, indent=1, default=str), ctx, "qc") + note, status="success", ui={"qc": compact})
