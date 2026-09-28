"""present_*: the director SHOWS work to the user as rich inline cards. Every presented item becomes
a versioned artifact (same version_group → v2, v3…) pinned to the Artifacts shelf."""
from __future__ import annotations

import asyncio
import json
import zipfile
from pathlib import Path

from ... import artifacts
from .base import ToolContext, ToolError, ToolOutput, parse_json_tail, run_engine, tool, truncate

VG = {"version_group": {"type": "string", "description": "present again with the same group to create v2, v3… (default: derived from the title)"}}
TEXT_EXT = {".txt", ".md", ".py", ".json", ".srt", ".vtt", ".csv", ".log", ".yaml", ".yml", ".toml", ".js", ".ts", ".html", ".css", ".svg", ".xml", ".sh"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
LANG = {".py": "python", ".json": "json", ".js": "javascript", ".ts": "typescript", ".sh": "bash", ".md": "markdown", ".html": "xml",
        ".svg": "xml", ".xml": "xml", ".css": "css", ".yaml": "yaml", ".yml": "yaml", ".toml": "ini", ".srt": "plaintext", ".txt": "plaintext"}


def _present(ctx: ToolContext, card: str, art: dict, extra: dict | None = None) -> dict:
    payload = {"card": card, "artifact": art, **(extra or {})}
    ctx.emit("present", payload)
    return payload


def _out(art: dict, what: str) -> ToolOutput:
    return ToolOutput(f"Presented {what} as artifact {art['id']} (“{art['title']}” v{art['version']}, group {art['version_group']}). "
                      f"Use the id as todo evidence or in present_comparison.", ui={"artifact": art["id"]})


def _media(ctx: ToolContext, path: str, kinds: set[str], label: str) -> tuple[Path, str]:
    p = ctx.resolve(path, must_exist=True)
    if p.is_dir() or p.suffix.lower() not in kinds:
        raise ToolError(f"{path} is not a {label} file ({', '.join(sorted(kinds))})")
    return p, ctx.rel(p)


def _probe(p: Path) -> dict:
    from luma_engine.production import media_probe

    return media_probe(str(p))


@tool(
    "present_video",
    """Show a video inline: player with frame stepping, speed, loop, chapter markers and download. Use for drafts and
    finals. `chapters` = [{t, label}] (e.g. Seed 0.1s, Unfold 1.3s, Bloom 2.6s); `poster_frame` in seconds (default: last frame).""",
    {"path": {"type": "string"}, "title": {"type": "string"}, "caption": {"type": "string"}, "poster_frame": {"type": "number"},
     "chapters": {"type": "array", "items": {"type": "object", "properties": {"t": {"type": "number"}, "label": {"type": "string"}}, "required": ["t", "label"]}},
     "loop": {"type": "boolean", "default": True}, **VG},
    ["path", "title"],
)
async def present_video(ctx: ToolContext, a: dict) -> ToolOutput:
    p, rel = _media(ctx, a["path"], VIDEO_EXT, "video")
    try:
        info = await asyncio.to_thread(_probe, p)
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"cannot read {rel}: {e}") from None
    if not info["video"]:
        raise ToolError(f"{rel} has no video stream")
    v = info["video"][0]
    dur = v["duration"] or info["duration"] or 0
    chapters = []
    for c in a.get("chapters") or []:
        t = float(c.get("t", 0))
        if not 0 <= t <= dur + 1e-3:
            raise ToolError(f"chapter {c.get('label')!r} at {t}s is outside the video (0–{dur:.3f}s)")
        chapters.append({"t": round(t, 4), "label": str(c.get("label"))[:80]})
    chapters.sort(key=lambda c: c["t"])
    from luma_engine.production import make_thumbnail

    poster_dir = ctx.pdir / "work" / ".luma" / "posters"
    poster = poster_dir / f"{p.stem}_{int(p.stat().st_mtime)}_{a.get('poster_frame', 'end')}.png"
    try:
        await asyncio.to_thread(make_thumbnail, str(p), str(poster), a.get("poster_frame"))
        poster_rel = ctx.rel(poster)
    except Exception:  # noqa: BLE001
        poster_rel = None
    meta = {"duration": dur, "fps": v["fps"], "frames": v["frames"], "width": v["width"], "height": v["height"], "codec": v["codec"],
            "has_audio": bool(info["audio"]), "chapters": chapters, "loop": bool(a.get("loop", True)), "caption": a.get("caption") or "",
            "poster": poster_rel, "poster_url": ctx.url(poster_rel) if poster_rel else None, "size_bytes": info["size_bytes"]}
    art = artifacts.create(ctx.project_id, ctx.run_id, "video", a["title"], rel, a.get("version_group"), meta)
    _present(ctx, "video", art)
    return _out(art, f"video {rel} ({dur:.3f}s, {v['frames']} frames, {v['width']}x{v['height']})")


@tool(
    "present_image",
    "Show stills, end cards or contact sheets inline. `path` or `paths`; layout single | grid | carousel.",
    {"path": {"type": "string"}, "paths": {"type": "array", "items": {"type": "string"}}, "title": {"type": "string"},
     "caption": {"type": "string"}, "layout": {"type": "string", "enum": ["single", "grid", "carousel"], "default": "single"}, **VG},
    ["title"],
)
async def present_image(ctx: ToolContext, a: dict) -> ToolOutput:
    paths = list(a.get("paths") or []) or ([a["path"]] if a.get("path") else [])
    if not paths:
        raise ToolError("give `path` or `paths`")
    if len(paths) > 48:
        raise ToolError("at most 48 images")
    from PIL import Image

    imgs = []
    for x in paths:
        p, rel = _media(ctx, x, IMG_EXT, "image")
        try:
            with Image.open(p) as im:
                w, h = im.size
        except Exception:  # noqa: BLE001
            raise ToolError(f"{rel} is not a readable image") from None
        imgs.append({"path": rel, "url": ctx.url(rel), "width": w, "height": h})
    layout = a.get("layout") or ("single" if len(imgs) == 1 else "grid")
    art = artifacts.create(ctx.project_id, ctx.run_id, "image", a["title"], imgs[0]["path"], a.get("version_group"),
                           {"images": imgs, "layout": layout, "caption": a.get("caption") or ""})
    _present(ctx, "image", art)
    return _out(art, f"{len(imgs)} image(s)")


@tool(
    "present_audio",
    """Show an audio player with a waveform. `transcript` = a word-timings JSON path (from el_tts: *.words.json) for
    word highlighting during playback, or plain text.""",
    {"path": {"type": "string"}, "title": {"type": "string"}, "transcript": {"type": "string"}, "waveform": {"type": "boolean", "default": True}, **VG},
    ["path", "title"],
)
async def present_audio(ctx: ToolContext, a: dict) -> ToolOutput:
    p, rel = _media(ctx, a["path"], AUDIO_EXT, "audio")
    from luma_engine.production import waveform_peaks

    try:
        wf = await asyncio.to_thread(waveform_peaks, str(p), 480) if a.get("waveform", True) else {"peaks": [], "duration": None}
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"cannot read {rel}: {e}") from None
    meta = {"peaks": wf["peaks"], "duration": wf["duration"], "words": None, "transcript": None}
    tr = str(a.get("transcript") or "").strip()
    if tr:
        cand = ctx.pdir / tr
        if tr.endswith(".json") and cand.resolve().is_file() and ctx.pdir.resolve() in cand.resolve().parents:
            d = json.loads(cand.read_text())
            words = d.get("words", d) if isinstance(d, dict) else d
            meta["words"] = [{"text": w["text"], "start": float(w["start"]), "end": float(w["end"])} for w in words if "start" in w][:5000]
            meta["transcript"] = " ".join(w["text"] for w in meta["words"])
        else:
            meta["transcript"] = tr[:10000]
    try:
        from luma_engine import audio as A

        x = await asyncio.to_thread(A.read_audio, str(p))
        meta["lufs"] = round(A.lufs(x), 2)
        meta["true_peak_dbtp"] = round(A.true_peak_db(x), 2)
    except Exception:  # noqa: BLE001
        pass
    art = artifacts.create(ctx.project_id, ctx.run_id, "audio", a["title"], rel, a.get("version_group"), meta)
    _present(ctx, "audio", art)
    return _out(art, f"audio {rel} ({wf['duration']}s)")


def _file_preview(ctx: ToolContext, p: Path) -> dict:
    ext = p.suffix.lower()
    pv: dict = {"kind": "none"}
    if ext in TEXT_EXT and p.stat().st_size < 2_000_000:
        txt = p.read_text(errors="replace")
        pv = {"kind": "text", "language": LANG.get(ext, "plaintext"), "text": txt[:8000], "truncated": len(txt) > 8000}
        if ext == ".json":
            try:
                pv["text"] = json.dumps(json.loads(txt), indent=2)[:8000]
            except ValueError:
                pass
    elif ext == ".zip":
        with zipfile.ZipFile(p) as z:
            items = [{"name": i.filename, "size": i.file_size} for i in z.infolist()[:300]]
        pv = {"kind": "zip", "entries": items, "count": len(items)}
    elif ext == ".pdf":
        import pymupdf

        thumb = ctx.pdir / "work" / ".luma" / "posters" / f"{p.stem}_pdf.png"
        thumb.parent.mkdir(parents=True, exist_ok=True)
        doc = pymupdf.open(str(p))
        doc[0].get_pixmap(dpi=60).save(str(thumb))
        pv = {"kind": "image", "url": ctx.url(ctx.rel(thumb)), "pages": len(doc)}
    elif ext in IMG_EXT:
        pv = {"kind": "image", "url": ctx.url(ctx.rel(p))}
    elif ext in VIDEO_EXT:
        pv = {"kind": "video", "url": ctx.url(ctx.rel(p))}
    elif ext in AUDIO_EXT:
        pv = {"kind": "audio", "url": ctx.url(ctx.rel(p))}
    return pv


@tool(
    "present_file",
    "Show a download card for a deliverable or file (type icon, size, and a preview for text/code/JSON/SRT/PDF/zip/media).",
    {"path": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"}, **VG},
    ["path", "title"],
)
async def present_file(ctx: ToolContext, a: dict) -> ToolOutput:
    p = ctx.resolve(a["path"], must_exist=True)
    if p.is_dir():
        raise ToolError("present_file takes a file; zip a folder first")
    rel = ctx.rel(p)
    try:
        pv = await asyncio.to_thread(_file_preview, ctx, p)
    except Exception as e:  # noqa: BLE001
        pv = {"kind": "none", "error": str(e)[:200]}
    meta = {"size_bytes": p.stat().st_size, "ext": p.suffix.lower(), "description": a.get("description") or "", "preview": pv,
            "deliverable": rel.startswith("outputs/")}
    art = artifacts.create(ctx.project_id, ctx.run_id, "file", a["title"], rel, a.get("version_group"), meta)
    _present(ctx, "file", art)
    return _out(art, f"file {rel}")


def _side(ctx: ToolContext, ref: str) -> dict:
    ref = str(ref)
    if ref.startswith("art_"):
        try:
            art = artifacts.get(ref)
        except artifacts.ArtifactError as e:
            raise ToolError(str(e)) from None
        if art["project_id"] != ctx.project_id:
            raise ToolError(f"no artifact {ref}")
        if art["type"] not in ("video", "image") or not art["path"]:
            raise ToolError(f"{ref} is a {art['type']}; compare videos or images")
        return {"artifact_id": ref, "path": art["path"], "url": art["url"], "kind": art["type"], "title": f"{art['title']} v{art['version']}",
                "poster": (art.get("meta") or {}).get("poster_url")}
    p = ctx.resolve(ref, must_exist=True)
    ext = p.suffix.lower()
    kind = "video" if ext in VIDEO_EXT else "image" if ext in IMG_EXT else None
    if kind is None:
        raise ToolError(f"{ref} is neither a video nor an image")
    return {"path": ctx.rel(p), "url": ctx.url(ctx.rel(p)), "kind": kind, "title": ctx.rel(p)}


@tool(
    "present_comparison",
    """A/B two versions of a video or image (artifact ids like art_… or project paths): side_by_side, slider (wipe) or
    toggle. Videos play in sync.""",
    {"a": {"type": "string"}, "b": {"type": "string"}, "mode": {"type": "string", "enum": ["side_by_side", "slider", "toggle"], "default": "slider"},
     "labels": {"type": "array", "items": {"type": "string"}, "maxItems": 2}, "title": {"type": "string"}, **VG},
    ["a", "b"],
)
async def present_comparison(ctx: ToolContext, a: dict) -> ToolOutput:
    A_, B_ = _side(ctx, a["a"]), _side(ctx, a["b"])
    if A_["kind"] != B_["kind"]:
        raise ToolError("compare two videos or two images, not one of each")
    labels = list(a.get("labels") or [A_["title"], B_["title"]])[:2]
    title = a.get("title") or f"{labels[0]} vs {labels[1]}"
    art = artifacts.create(ctx.project_id, ctx.run_id, "comparison", title, None, a.get("version_group"),
                           {"a": A_, "b": B_, "mode": a.get("mode") or "slider", "labels": labels, "kind": A_["kind"]})
    _present(ctx, "comparison", art)
    return _out(art, "comparison")


@tool(
    "present_storyboard",
    """A shot-by-shot strip for sign-off BEFORE the final render: thumbnails, times and notes. Give `frames` (image
    paths), or `scene_path` + `timings` to render the stills from your scene. `timings` = shot start times (s);
    `notes` = one description per shot.""",
    {"frames": {"type": "array", "items": {"type": "string"}}, "scene_path": {"type": "string"}, "timings": {"type": "array", "items": {"type": "number"}},
     "notes": {"type": "array", "items": {"type": "string"}}, "title": {"type": "string", "default": "Storyboard"}, **VG},
    ["timings", "notes"],
)
async def present_storyboard(ctx: ToolContext, a: dict) -> ToolOutput:
    timings = [float(t) for t in a.get("timings") or []]
    notes = [str(n) for n in a.get("notes") or []]
    frames = list(a.get("frames") or [])
    if not timings or len(notes) != len(timings):
        raise ToolError("give one note per timing")
    if frames and len(frames) != len(timings):
        raise ToolError("give one frame per timing")
    if not frames:
        if not a.get("scene_path"):
            raise ToolError("give `frames` or a `scene_path` to render them from")
        scene = ctx.resolve(a["scene_path"], must_exist=True)
        import time

        out_dir = ctx.pdir / "work" / "storyboard" / f"sb_{int(time.time() * 1000)}"
        from .media_tools import _overrides

        rc, text, _ = await run_engine(ctx, ["stills", str(scene), "--times", ",".join(str(t) for t in timings), "--out", str(out_dir),
                                             "--scale", "0.4", *_overrides(ctx)], timeout=600)
        res = parse_json_tail(text) or {}
        if rc != 0 or not res.get("stills"):
            raise ToolError(f"rendering storyboard stills failed:\n{truncate(text, ctx, 'storyboard')}")
        frames = [ctx.rel(out_dir / Path(s["path"]).name) for s in res["stills"]]
    shots = []
    for i, (f, t, n) in enumerate(zip(frames, timings, notes)):
        p, rel = _media(ctx, f, IMG_EXT, "image")
        end = timings[i + 1] if i + 1 < len(timings) else None
        shots.append({"n": i + 1, "t": round(t, 3), "end": end and round(end, 3), "note": n[:500], "path": rel, "url": ctx.url(rel)})
    art = artifacts.create(ctx.project_id, ctx.run_id, "storyboard", a.get("title") or "Storyboard", shots[0]["path"], a.get("version_group"),
                           {"shots": shots, "duration": ctx.settings.get("project", {}).get("duration")})
    _present(ctx, "storyboard", art)
    out = _out(art, f"storyboard with {len(shots)} shots")
    if ctx.vision:
        out.images = [s["path"] for s in shots[:6]]
    return out


@tool(
    "present_timeline",
    """A visual timeline of the event list (picture + audio cues) with a scrubber tied to a video. Give `events_path`
    (Timeline JSON, e.g. work/events.json) or `events` [{t, name, kind, track?}], and optionally `video` (artifact id or path).""",
    {"events": {"type": "array", "items": {"type": "object"}}, "events_path": {"type": "string"}, "video": {"type": "string"},
     "title": {"type": "string", "default": "Timeline"}, **VG},
)
async def present_timeline(ctx: ToolContext, a: dict) -> ToolOutput:
    evs = a.get("events")
    duration = ctx.settings.get("project", {}).get("duration")
    spans = {}
    if a.get("events_path"):
        d = json.loads(ctx.resolve(a["events_path"], must_exist=True).read_text())
        evs = d.get("events", []) if isinstance(d, dict) else d
        duration = d.get("duration", duration) if isinstance(d, dict) else duration
        spans = d.get("spans") or {} if isinstance(d, dict) else {}
    if not evs:
        raise ToolError("give events or events_path")
    audio_kinds = {"boom", "hit", "whoosh", "sfx", "music", "voice", "word", "sync"}
    norm = []
    for e in evs[:500]:
        if "t" not in e:
            raise ToolError("each event needs t (seconds)")
        kind = str(e.get("kind") or "impact")
        norm.append({"t": round(float(e["t"]), 4), "name": str(e.get("name") or kind)[:60], "kind": kind,
                     "track": e.get("track") or ("audio" if kind in audio_kinds else "picture"), "duration": e.get("duration")})
    norm.sort(key=lambda e: e["t"])
    video = None
    if a.get("video"):
        video = _side(ctx, a["video"])
    art = artifacts.create(ctx.project_id, ctx.run_id, "timeline", a.get("title") or "Timeline", None, a.get("version_group"),
                           {"events": norm, "duration": duration, "spans": spans, "video": video})
    _present(ctx, "timeline", art)
    return _out(art, f"timeline with {len(norm)} events")


@tool(
    "present_code",
    "Show a file with syntax highlighting (for users who want to learn or edit the scene). `highlight_lines` = [n, …] or [[start, end], …].",
    {"path": {"type": "string"}, "highlight_lines": {"type": "array", "items": {}}, "title": {"type": "string"}, **VG},
    ["path"],
)
async def present_code(ctx: ToolContext, a: dict) -> ToolOutput:
    p = ctx.resolve(a["path"], must_exist=True)
    if p.stat().st_size > 400_000:
        raise ToolError("file too large to present (max 400 KB)")
    text = p.read_text(errors="replace")
    hl: list[int] = []
    for h in a.get("highlight_lines") or []:
        if isinstance(h, list) and len(h) == 2:
            hl += list(range(int(h[0]), int(h[1]) + 1))
        else:
            hl.append(int(h))
    rel = ctx.rel(p)
    art = artifacts.create(ctx.project_id, ctx.run_id, "code", a.get("title") or rel, rel, a.get("version_group"),
                           {"language": LANG.get(p.suffix.lower(), "plaintext"), "lines": text.count("\n") + 1, "highlight": sorted(set(hl))[:2000],
                            "content": text[:120_000], "truncated": len(text) > 120_000})
    _present(ctx, "code", art)
    return _out(art, f"code {rel}")


@tool(
    "present_table",
    "Show a table (specs, palettes, QC results). rows = list of lists (same order as columns).",
    {"title": {"type": "string"}, "columns": {"type": "array", "items": {"type": "string"}}, "rows": {"type": "array", "items": {"type": "array", "items": {}}}, **VG},
    ["title", "columns", "rows"],
)
async def present_table(ctx: ToolContext, a: dict) -> ToolOutput:
    cols = [str(c)[:80] for c in a["columns"]][:20]
    if not cols:
        raise ToolError("columns is empty")
    rows = []
    for r in a["rows"][:500]:
        if not isinstance(r, list) or len(r) != len(cols):
            raise ToolError(f"each row needs {len(cols)} cells")
        rows.append([c if isinstance(c, (int, float, bool)) or c is None else str(c)[:500] for c in r])
    art = artifacts.create(ctx.project_id, ctx.run_id, "table", a["title"], None, a.get("version_group"), {"columns": cols, "rows": rows})
    _present(ctx, "table", art)
    return _out(art, f"table ({len(rows)} rows)")


def _contrast(hex1: str, hex2: str) -> float:
    def lum(h):
        c = [int(h[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

    a, b = sorted((lum(hex1), lum(hex2)), reverse=True)
    return round((a + 0.05) / (b + 0.05), 2)


@tool(
    "present_palette",
    "Show brand swatches: colors = [{hex, name, usage}]. Contrast ratios vs white/black are added.",
    {"colors": {"type": "array", "items": {"type": "object", "properties": {"hex": {"type": "string"}, "name": {"type": "string"}, "usage": {"type": "string"}},
                                                   "required": ["hex"]}},
     "title": {"type": "string", "default": "Palette"}, **VG},
    ["colors"],
)
async def present_palette(ctx: ToolContext, a: dict) -> ToolOutput:
    from luma_engine.brand import Color

    out = []
    for c in a["colors"][:48]:
        try:
            hx = Color.of(c["hex"]).hex.upper()
        except Exception:  # noqa: BLE001
            raise ToolError(f"{c.get('hex')!r} is not a colour") from None
        out.append({"hex": hx, "name": str(c.get("name") or "")[:60], "usage": str(c.get("usage") or "")[:200],
                    "contrast_white": _contrast(hx, "#FFFFFF"), "contrast_black": _contrast(hx, "#000000")})
    if not out:
        raise ToolError("colors is empty")
    art = artifacts.create(ctx.project_id, ctx.run_id, "palette", a.get("title") or "Palette", None, a.get("version_group"), {"colors": out})
    _present(ctx, "palette", art)
    return _out(art, f"palette ({len(out)} colours)")
