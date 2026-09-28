"""Production utilities: probing, frame extraction, GIF/WebP previews, thumbnails, stills and
end cards, aspect-ratio re-layout (reframe), raster → vector tracing, palettes, font
detection, multitrack mixing and caption files.

Everything here is usable from Python, from the CLI (``python -m luma_engine <cmd>``) and
through the director's tools.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

import numpy as np

ASPECTS = {"16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1), "4:5": (4, 5), "4:3": (4, 3), "21:9": (21, 9)}


class ProductionError(Exception):
    pass


def _ffmpeg(args: list[str], timeout: float = 600) -> None:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise ProductionError(f"ffmpeg failed: {r.stderr.strip()[-1200:]}")


def _fraction(s: str | None) -> float | None:
    if not s or s in ("0/0", "N/A"):
        return None
    if "/" in s:
        a, b = s.split("/")
        return float(a) / float(b) if float(b) else None
    return float(s)


# ======================================================================================
# probing & frames
# ======================================================================================


def media_probe(path: str) -> dict:
    """ffprobe as clean JSON: container, duration, size, and per-stream essentials."""
    if not os.path.exists(path):
        raise ProductionError(f"{path} does not exist")
    r = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", path], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise ProductionError(f"ffprobe failed: {r.stderr.strip()[-500:]}")
    raw = json.loads(r.stdout or "{}")
    fmt = raw.get("format", {})
    out: dict = {"path": path, "container": fmt.get("format_name"), "duration": float(fmt.get("duration") or 0) or None,
                 "size_bytes": int(fmt.get("size") or os.path.getsize(path)), "bit_rate": int(fmt.get("bit_rate") or 0) or None,
                 "video": [], "audio": [], "subtitles": [], "tags": fmt.get("tags") or {}}
    for s in raw.get("streams", []):
        t = s.get("codec_type")
        if t == "video":
            fps = _fraction(s.get("avg_frame_rate")) or _fraction(s.get("r_frame_rate"))
            dur = float(s.get("duration") or out["duration"] or 0)
            frames = int(s["nb_frames"]) if str(s.get("nb_frames", "")).isdigit() else (int(round(dur * fps)) if fps and dur else None)
            still = s.get("codec_name") in ("png", "mjpeg", "webp") and (frames or 1) <= 1
            out["video"].append({"codec": s.get("codec_name"), "profile": s.get("profile"), "width": s.get("width"), "height": s.get("height"),
                                 "fps": round(fps, 4) if fps else None, "frames": frames, "duration": dur or None, "pix_fmt": s.get("pix_fmt"),
                                 "color_primaries": s.get("color_primaries"), "color_transfer": s.get("color_transfer"),
                                 "bit_rate": int(s.get("bit_rate") or 0) or None, "still_image": still})
        elif t == "audio":
            out["audio"].append({"codec": s.get("codec_name"), "sample_rate": int(s.get("sample_rate") or 0), "channels": s.get("channels"),
                                 "channel_layout": s.get("channel_layout"), "duration": float(s.get("duration") or 0) or None,
                                 "bit_rate": int(s.get("bit_rate") or 0) or None})
        elif t == "subtitle":
            out["subtitles"].append({"codec": s.get("codec_name"), "language": (s.get("tags") or {}).get("language")})
    return out


def _label(img, text: str):
    import cv2

    img = img.copy()
    h = max(18, img.shape[0] // 18)
    cv2.rectangle(img, (0, 0), (img.shape[1], h + 6), (0, 0, 0), -1)
    cv2.putText(img, text, (6, h), cv2.FONT_HERSHEY_SIMPLEX, h / 30, (255, 255, 255), 1, cv2.LINE_AA)
    return img


def contact_sheet_from_images(paths: Sequence[str], out: str, labels: Sequence[str] | None = None, cols: int | None = None,
                              tile_width: int = 480, gap: int = 6) -> str:
    import cv2

    tiles = []
    for i, p in enumerate(paths):
        im = cv2.imread(p, cv2.IMREAD_COLOR)
        if im is None:
            continue
        s = tile_width / im.shape[1]
        im = cv2.resize(im, (tile_width, max(1, int(round(im.shape[0] * s)))), interpolation=cv2.INTER_AREA)
        tiles.append(_label(im, labels[i]) if labels else im)
    if not tiles:
        raise ProductionError("no readable images for the contact sheet")
    th = max(t.shape[0] for t in tiles)
    n = len(tiles)
    cols = cols or (1 if n == 1 else 2 if n <= 4 else 3 if n <= 9 else 4)
    rows = math.ceil(n / cols)
    sheet = np.full((rows * th + (rows + 1) * gap, cols * tile_width + (cols + 1) * gap, 3), 24, np.uint8)
    for i, t in enumerate(tiles):
        r, c = divmod(i, cols)
        y, x = gap + r * (th + gap), gap + c * (tile_width + gap)
        sheet[y : y + t.shape[0], x : x + tile_width] = t
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(out, sheet)
    return out


def extract_frames(path: str, out_dir: str, times: Sequence[float] | None = None, every_n: int | None = None, max_frames: int = 48,
                   sheet: bool = True) -> dict:
    """Frames at `times` (seconds) or every N-th frame, as PNGs, plus a labelled contact sheet."""
    info = media_probe(path)
    if not info["video"]:
        raise ProductionError("no video stream")
    v = info["video"][0]
    fps = v["fps"] or 30.0
    dur = v["duration"] or info["duration"] or 0.0
    os.makedirs(out_dir, exist_ok=True)
    if times is None:
        n = int(every_n or max(1, int((v["frames"] or 1) / 8)))
        total = v["frames"] or int(dur * fps)
        times = [i / fps for i in range(0, max(1, total), n)][:max_frames]
    times = [min(max(0.0, float(t)), max(0.0, dur - 0.5 / fps)) for t in times][:max_frames]
    frames = []
    for t in times:
        fi = int(round(t * fps))
        p = os.path.join(out_dir, f"frame_{fi:06d}.png")
        _ffmpeg(["-ss", f"{t:.4f}", "-i", path, "-frames:v", "1", "-update", "1", p])
        if os.path.exists(p):
            frames.append({"t": round(t, 4), "frame": fi, "path": p})
    res = {"frames": frames, "fps": fps, "duration": dur}
    if sheet and frames:
        res["contact_sheet"] = contact_sheet_from_images([f["path"] for f in frames], os.path.join(out_dir, "contact_sheet.png"),
                                                         [f"t={f['t']:.3f}s f{f['frame']}" for f in frames])
    return res


def make_thumbnail(path: str, out: str, time: float | None = None, width: int | None = None) -> str:
    info = media_probe(path)
    dur = (info["video"][0]["duration"] if info["video"] else None) or info["duration"] or 0
    t = dur - 0.05 if time is None else min(max(0.0, float(time)), max(0.0, dur - 0.02))
    vf = ["-vf", f"scale={int(width)}:-2:flags=lanczos"] if width else []
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    _ffmpeg(["-ss", f"{t:.4f}", "-i", path, "-frames:v", "1", "-update", "1", *vf, out])
    if not os.path.exists(out):
        raise ProductionError("thumbnail was not written")
    return out


def make_gif_or_webp(path: str, out: str, start: float = 0.0, end: float | None = None, width: int = 640, fps: float = 15) -> dict:
    """Optimised preview: GIF with a per-clip palette (palettegen/paletteuse, sierra dither) or animated WebP."""
    info = media_probe(path)
    dur = (info["video"][0]["duration"] if info["video"] else None) or info["duration"] or 0
    end = dur if end is None else min(float(end), dur)
    if end <= start:
        raise ProductionError("end must be after start")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    base = f"fps={fps},scale={int(width)}:-2:flags=lanczos"
    seg = ["-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", path]
    if out.lower().endswith(".gif"):
        _ffmpeg([*seg, "-filter_complex", f"[0:v]{base},split[a][b];[a]palettegen=stats_mode=diff:max_colors=256[p];"
                                          f"[b][p]paletteuse=dither=sierra2_4a:diff_mode=rectangle", "-loop", "0", out])
    elif out.lower().endswith(".webp"):
        _ffmpeg([*seg, "-vf", base, "-c:v", "libwebp_anim", "-lossless", "0", "-q:v", "75", "-compression_level", "6", "-loop", "0", "-an", out])
    else:
        raise ProductionError("out must end in .gif or .webp")
    return {"path": out, "bytes": os.path.getsize(out), "width": int(width), "fps": fps, "start": start, "end": end}


# ======================================================================================
# scene stills, end cards, reframing, variants
# ======================================================================================


def parse_size(s: str) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d{2,5})\s*[x×]\s*(\d{2,5})\s*", str(s))
    if not m:
        raise ProductionError(f"size {s!r} must look like 1920x1080")
    return int(m.group(1)), int(m.group(2))


def aspect_size(aspect: str, long_side: int = 1920) -> tuple[int, int]:
    """16:9 → 1920×1080, 9:16 → 1080×1920, 1:1 → 1080×1080, 4:5 → 1080×1350 (long side for landscape,
    short side 1080 for portrait/square, even dimensions)."""
    if aspect not in ASPECTS:
        raise ProductionError(f"aspect must be one of {list(ASPECTS)}")
    a, b = ASPECTS[aspect]
    if a >= b and a / b > 1.2:
        w = long_side
        h = w * b / a
    else:
        short = round(long_side * 9 / 16)
        w = short
        h = w * b / a
    return int(round(w / 2) * 2), int(round(h / 2) * 2)


def render_stills(scene_path: str, times: Sequence[float], out_dir: str, scale: float = 1.0, overrides: dict | None = None,
                  blur: bool = False, prefix: str = "still") -> list[dict]:
    from .encode import to_uint8
    from .render import load_scene
    import cv2

    sc = load_scene(scene_path, **(overrides or {}))
    sc.ensure_setup()
    os.makedirs(out_dir, exist_ok=True)
    out = []
    for t in times:
        t = min(max(float(t), 0.0), sc.duration)
        img = to_uint8(sc.render(t, scale, blur))
        p = os.path.join(out_dir, f"{prefix}_{int(round(t * 1000)):06d}ms.png")
        cv2.imwrite(p, cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
        out.append({"t": round(t, 4), "path": p, "width": img.shape[1], "height": img.shape[0]})
    return out


def export_end_card(scene_path: str, out_dir: str, sizes: Sequence[str] = ("1920x1080",), time: float | None = None, name: str = "end_card") -> list[dict]:
    """The final design at several sizes, each rendered (not scaled) at that size so the layout re-flows."""
    from .render import load_scene

    res = []
    for s in sizes:
        w, h = parse_size(s)
        sc = load_scene(scene_path, width=w, height=h)
        t = sc.time_of(sc.frame_count - 1) if time is None else float(time)
        st = render_stills(scene_path, [t], out_dir, 1.0, {"width": w, "height": h}, blur=False, prefix=f"{name}_{w}x{h}")
        res += st
    return res


def content_box(img: np.ndarray, background: Sequence[int] | None = None, tol: int = 12) -> tuple[int, int, int, int] | None:
    """Bounding box (x0, y0, x1, y1) of pixels that differ from the background (corner colour)."""
    bg = np.array(background if background is not None else np.median(np.stack([img[0, 0], img[0, -1], img[-1, 0], img[-1, -1]]), axis=0))
    mask = np.abs(img.astype(np.int16) - bg.astype(np.int16)).max(axis=2) > tol
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def reframe_check(scene_path: str, aspects: Sequence[str], out_dir: str, long_side: int = 1920, time: float | None = None) -> list[dict]:
    """Render the end card at each aspect (the scene re-lays itself out for the new size) and check the
    content sits inside the title-safe area — a re-layout, not a crop."""
    import cv2

    from .layout import safe_area

    out = []
    for a in aspects:
        w, h = aspect_size(a, long_side)
        cards = export_end_card(scene_path, out_dir, [f"{w}x{h}"], time, name=f"reframe_{a.replace(':', 'x')}")
        img = cv2.cvtColor(cv2.imread(cards[0]["path"]), cv2.COLOR_BGR2RGB)
        box = content_box(img)
        safe = safe_area(w, h, "action")
        inside = box is not None and box[0] >= safe.x - 1 and box[1] >= safe.y - 1 and box[2] <= safe.x + safe.w + 1 and box[3] <= safe.y + safe.h + 1
        out.append({"aspect": a, "size": [w, h], "end_card": cards[0]["path"], "content_box": box,
                    "safe_box": [round(safe.x), round(safe.y), round(safe.x + safe.w), round(safe.y + safe.h)], "inside_safe_area": bool(inside)})
    return out


# ======================================================================================
# asset preparation
# ======================================================================================


def _foreground_mask(rgba: np.ndarray, tol: int = 40) -> np.ndarray:
    if rgba.shape[2] == 4 and rgba[..., 3].min() < 250:
        return rgba[..., 3] > 127
    rgb = rgba[..., :3].astype(np.int16)
    corners = np.stack([rgb[0, 0], rgb[0, -1], rgb[-1, 0], rgb[-1, -1]])
    bg = np.median(corners, axis=0)
    return np.abs(rgb - bg).max(axis=2) > tol


def vectorize_raster(path: str, out_svg: str, mode: str = "color", filter_speckle: int = 4, color_precision: int = 6,
                     corner_threshold: int = 60) -> dict:
    """Trace a PNG/JPG logo to SVG (vtracer) and score the result: IoU of the foreground mask of the
    original vs the rasterised SVG. Always tell the user the SVG is AUTO-TRACED."""
    import cairosvg
    import vtracer
    from PIL import Image

    src = Image.open(path).convert("RGBA")
    w, h = src.size
    max_side = 1600
    tmp_png = out_svg + ".src.png"
    im = src
    if max(w, h) > max_side:  # tracing time grows with pixels
        s = max_side / max(w, h)
        im = src.resize((round(w * s), round(h * s)), Image.LANCZOS)
    arr = np.array(im)
    if arr[..., 3].min() >= 250:  # opaque: make the background transparent so the SVG has no box
        m = _foreground_mask(arr)
        arr[..., 3] = np.where(m, 255, 0).astype(np.uint8)
    Image.fromarray(arr).save(tmp_png)
    Path(out_svg).parent.mkdir(parents=True, exist_ok=True)
    vtracer.convert_image_to_svg_py(tmp_png, out_svg, colormode="color" if mode == "color" else "binary", hierarchical="stacked",
                                    mode="spline", filter_speckle=filter_speckle, color_precision=color_precision,
                                    layer_difference=16, corner_threshold=corner_threshold, length_threshold=4.0, max_iterations=10,
                                    splice_threshold=45, path_precision=3)
    os.remove(tmp_png)
    tw, th = arr.shape[1], arr.shape[0]
    png = cairosvg.svg2png(url=out_svg, output_width=tw, output_height=th)
    import io

    rend = np.array(Image.open(io.BytesIO(png)).convert("RGBA"))
    a, b = _foreground_mask(arr), rend[..., 3] > 127
    union = np.logical_or(a, b).sum()
    iou = float(np.logical_and(a, b).sum() / union) if union else 0.0
    both = np.logical_and(a, b)
    color_err = float(np.abs(arr[..., :3][both].astype(np.int16) - rend[..., :3][both].astype(np.int16)).mean()) if both.any() else None
    svg_text = Path(out_svg).read_text()
    return {"svg": out_svg, "iou": round(iou, 4), "mean_color_error": None if color_err is None else round(color_err, 2),
            "paths": svg_text.count("<path"), "colors": sorted(set(re.findall(r'fill="(#[0-9A-Fa-f]{6})"', svg_text)))[:32],
            "size": [tw, th], "auto_traced": True,
            "note": "AUTO-TRACED from a raster image — not the original vector artwork. Ask the user for the real SVG if fidelity matters."}


def extract_palette(path: str, k: int = 8) -> dict:
    """Dominant colours (k-means in Lab over non-transparent pixels) with their share, plus exact
    colours declared in SVG/PDF text when the file has them."""
    import cv2
    from PIL import Image

    ext = Path(path).suffix.lower()
    declared: list[str] = []
    if ext == ".svg":
        import cairosvg

        txt = Path(path).read_text(errors="replace")
        declared = sorted({c.upper() for c in re.findall(r"#[0-9A-Fa-f]{6}\b", txt)})
        import io

        img = np.array(Image.open(io.BytesIO(cairosvg.svg2png(url=path, output_width=800))).convert("RGBA"))
    elif ext == ".pdf":
        import pymupdf

        doc = pymupdf.open(path)
        text = " ".join(p.get_text() for p in doc)
        declared = sorted({c.upper() for c in re.findall(r"#[0-9A-Fa-f]{6}\b", text)})
        pix = doc[0].get_pixmap(dpi=80)
        img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
        img = np.concatenate([img[..., :3], np.full(img.shape[:2] + (1,), 255, np.uint8)], axis=2)
    else:
        im = Image.open(path).convert("RGBA")
        im.thumbnail((600, 600))
        img = np.array(im)
    px = img[img[..., 3] > 127][:, :3]
    if not len(px):
        raise ProductionError("image has no opaque pixels")
    if len(px) > 60000:
        px = px[np.random.default_rng(0).choice(len(px), 60000, replace=False)]
    lab = cv2.cvtColor(px.reshape(-1, 1, 3).astype(np.uint8), cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
    k = max(1, min(k, len(np.unique(px, axis=0))))
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 50, 0.5)
    _, labels, centers = cv2.kmeans(lab, k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    counts = np.bincount(labels.ravel(), minlength=k)
    colors = []
    for i in np.argsort(-counts):
        rgb = cv2.cvtColor(centers[i].reshape(1, 1, 3).astype(np.uint8), cv2.COLOR_LAB2RGB).reshape(3)
        colors.append({"hex": "#{:02X}{:02X}{:02X}".format(*map(int, rgb)), "share": round(float(counts[i] / counts.sum()), 4)})
    return {"colors": colors, "declared": declared}


def _pdf_fonts(path: str) -> list[dict]:
    import pymupdf

    seen = {}
    for page in pymupdf.open(path):
        for f in page.get_fonts(full=True):
            name = re.sub(r"^[A-Z]{6}\+", "", f[3] or "")
            if name and name not in seen:
                seen[name] = {"name": name, "type": f[1], "embedded": bool(f[4] if len(f) > 4 else True), "source": "pdf", "guess": False}
    return list(seen.values())


def detect_fonts(path: str, candidates: Sequence[str] | None = None) -> dict:
    """PDF: the exact font names in the file. Images (or PDFs without fonts): OCR the text and rank
    installed families by how closely they reproduce it — reported as GUESSES with a confidence."""
    ext = Path(path).suffix.lower()
    out: dict = {"fonts": [], "ocr_text": None, "method": None}
    if ext == ".pdf":
        out["fonts"] = _pdf_fonts(path)
        out["method"] = "pdf font table (exact)"
        if out["fonts"]:
            return out
        import pymupdf

        pix = pymupdf.open(path)[0].get_pixmap(dpi=150)
        img_path = path + ".page1.png"
        pix.save(img_path)
        path = img_path
    try:
        import pytesseract
        from PIL import Image

        if not shutil.which("tesseract"):
            raise ImportError
    except ImportError:
        out["method"] = "unavailable"
        out["note"] = "OCR (tesseract) is not installed; font guessing from images is unavailable."
        return out
    im = Image.open(path).convert("L")
    if max(im.size) < 1200:
        s = 1200 / max(im.size)
        im = im.resize((int(im.width * s), int(im.height * s)))
    arr = np.array(im)
    if arr.mean() < 128:  # light text on dark
        arr = 255 - arr
        im = Image.fromarray(arr)
    data = pytesseract.image_to_data(im, output_type=pytesseract.Output.DICT)
    words = [(data["text"][i], data["left"][i], data["top"][i], data["width"][i], data["height"][i], float(data["conf"][i]))
             for i in range(len(data["text"])) if data["text"][i].strip() and float(data["conf"][i]) > 60 and len(data["text"][i].strip()) >= 3]
    out["ocr_text"] = " ".join(w[0] for w in words)[:2000]
    out["method"] = "ocr + glyph comparison (GUESS)"
    if not words:
        out["note"] = "no legible text found"
        return out
    w0 = max(words, key=lambda w: w[3] * w[4])  # the largest word
    crop = arr[w0[2] : w0[2] + w0[4], w0[1] : w0[1] + w0[3]]
    out["fonts"] = _rank_fonts(w0[0].strip(), crop, candidates)
    return out


def _rank_fonts(text: str, crop: np.ndarray, candidates: Sequence[str] | None, top: int = 5) -> list[dict]:
    import cv2
    from PIL import Image, ImageDraw, ImageFont

    from .text import font_index

    fams: dict[str, str] = {}
    for fi in font_index():
        if fi.italic or fi.index:
            continue
        if candidates and fi.family not in candidates:
            continue
        cur = fams.get(fi.family)
        if cur is None or abs(fi.weight - 500) < abs(cur[1] - 500):  # prefer regular-ish weights
            fams[fi.family] = (fi.path, fi.weight)
    target = cv2.resize((crop < 128).astype(np.float32), (256, 64), interpolation=cv2.INTER_AREA)
    scores = []
    for fam, (fp, _) in list(fams.items())[:200]:
        try:
            font = ImageFont.truetype(fp, 96)
        except Exception:
            continue
        box = font.getbbox(text)
        if box[2] - box[0] <= 0:
            continue
        img = Image.new("L", (box[2] - box[0] + 4, box[3] - box[1] + 4), 255)
        ImageDraw.Draw(img).text((2 - box[0], 2 - box[1]), text, font=font, fill=0)
        r = cv2.resize((np.array(img) < 128).astype(np.float32), (256, 64), interpolation=cv2.INTER_AREA)
        inter = float(np.minimum(r, target).sum())
        union = float(np.maximum(r, target).sum()) or 1.0
        scores.append((inter / union, fam))
    scores.sort(reverse=True)
    return [{"name": f, "confidence": round(s, 3), "source": "ocr", "guess": True} for s, f in scores[:top]]


# ======================================================================================
# audio
# ======================================================================================


def audio_mix(tracks: Sequence[dict], out: str, target_lufs: float = -14.0, true_peak: float = -1.0, duration: float | None = None,
              sr: int = 48000) -> dict:
    """Multitrack mix. Each track: {path, gain_db=0, start=0, duck_under=<index or label>, label, fade_in, fade_out, pan}.
    Tracks with `duck_under` are side-chain ducked under that track (e.g. music under voice). The sum is
    loudness-normalised to `target_lufs` and true-peak limited."""
    from . import audio as A

    loaded = []
    for i, t in enumerate(tracks):
        if not t.get("path"):
            raise ProductionError(f"track {i} has no path")
        x = A.to_stereo(A.read_audio(t["path"], sr))
        if t.get("fade_in") or t.get("fade_out"):
            x = A.fade(x, float(t.get("fade_in") or 0), float(t.get("fade_out") or 0), sr)
        if t.get("pan") is not None:
            x = A.pan_stereo(A.to_mono(x), float(t["pan"]))
        x = x * A.db_to_gain(float(t.get("gain_db") or 0))
        loaded.append({"label": t.get("label") or Path(t["path"]).stem, "x": x, "start": float(t.get("start") or 0), "duck": t.get("duck_under")})
    end = max(tr["start"] + len(tr["x"]) / sr for tr in loaded) if loaded else 0
    total = float(duration) if duration else end
    n = int(round(total * sr))

    def placed(tr):
        buf = np.zeros((n, 2), np.float32)
        s = int(round(tr["start"] * sr))
        if s < n:
            seg = tr["x"][: n - s]
            buf[s : s + len(seg)] = seg
        return buf

    bufs = [placed(tr) for tr in loaded]
    labels = [tr["label"] for tr in loaded]
    ducked = []
    for i, tr in enumerate(loaded):
        d = tr["duck"]
        if d is None or d == "":
            continue
        j = int(d) if isinstance(d, (int, float)) or str(d).isdigit() else (labels.index(d) if d in labels else None)
        if j is None or j == i or not (0 <= j < len(bufs)):
            raise ProductionError(f"duck_under {d!r} of track {i} does not name another track")
        bufs[i] = A.sidechain_duck(bufs[i], bufs[j], sr=sr)
        ducked.append(f"{tr['label']} under {labels[j]}")
    mix = np.sum(bufs, axis=0) if bufs else np.zeros((n, 2), np.float32)
    mix = A.master(mix, target_lufs, true_peak, sr)
    mix = A.fit_length(mix, total, sr)
    A.write_wav(out, mix, sr)
    return {"path": out, "duration": round(len(mix) / sr, 4), "lufs": round(A.lufs(mix, sr), 2), "true_peak_dbtp": round(A.true_peak_db(mix), 2),
            "tracks": labels, "ducked": ducked}


def waveform_peaks(path: str, buckets: int = 600) -> dict:
    from . import audio as A

    x = A.to_mono(A.read_audio(path))
    n = len(x)
    if not n:
        return {"peaks": [], "duration": 0}
    edges = np.linspace(0, n, buckets + 1).astype(int)
    peaks = [float(np.abs(x[edges[i] : max(edges[i] + 1, edges[i + 1])]).max()) for i in range(buckets)]
    m = max(peaks) or 1.0
    return {"peaks": [round(p / m, 4) for p in peaks], "duration": round(n / A.SR, 4)}


# ======================================================================================
# captions
# ======================================================================================


def vtt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def captions_build(words: Sequence[dict] | str, out_base: str, style: dict | None = None) -> dict:
    """Word timings ({text,start,end} list or a *.words.json path) → SRT + WebVTT + a kinetic-caption
    layer spec (cues with per-word timings and style) for ``fx.kinetic_captions``."""
    from .encode import words_to_cues, write_srt

    if isinstance(words, str):
        data = json.loads(Path(words).read_text())
        words = data.get("words", data) if isinstance(data, dict) else data
    words = [w for w in words if str(w.get("text", "")).strip()]
    if not words:
        raise ProductionError("no words")
    st = {"max_chars": 42, "max_lines": 2, "max_duration": 6.0, "font": "Inter", "weight": 600, "size": 54, "color": "#FFFFFF",
          "highlight": "#5DAEFF", "position": "bottom", "animation": "rise", **(style or {})}
    cues = words_to_cues(words, max_chars=int(st["max_chars"]), max_lines=int(st["max_lines"]), max_dur=float(st["max_duration"]))
    srt = out_base + ".srt"
    Path(srt).parent.mkdir(parents=True, exist_ok=True)
    write_srt(cues, srt)
    vtt = out_base + ".vtt"
    lines = ["WEBVTT", ""]
    for i, c in enumerate(cues, 1):
        lines += [str(i), f"{vtt_time(c.start)} --> {vtt_time(c.end)}", c.text, ""]
    Path(vtt).write_text("\n".join(lines))
    layers = []
    wi = 0
    for c in cues:
        ws = []
        while wi < len(words) and words[wi]["start"] < c.end - 1e-6:
            if words[wi]["start"] >= c.start - 1e-6:
                ws.append({"text": words[wi]["text"], "start": round(words[wi]["start"], 3), "end": round(words[wi]["end"], 3)})
            wi += 1
        layers.append({"start": round(c.start, 3), "end": round(c.end, 3), "lines": c.text.split("\n"), "words": ws})
    kin = out_base + ".kinetic.json"
    Path(kin).write_text(json.dumps({"style": st, "cues": layers}, indent=1))
    return {"srt": srt, "vtt": vtt, "kinetic": kin, "cues": len(cues), "words": len(words)}
