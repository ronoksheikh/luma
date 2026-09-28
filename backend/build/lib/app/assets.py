"""Upload validation (magic bytes, limits, SVG sanitising), analysis and thumbnails."""
from __future__ import annotations

import io
import json
import re
import time
from pathlib import Path

from luma_engine.analysis import analyze_audio, analyze_image, analyze_pdf, analyze_svg, sniff
from luma_engine.svg import sanitize_svg

from .config import config

ALLOWED = {"png", "jpeg", "webp", "svg", "pdf", "mp3", "wav"}
EXT = {"png": ".png", "jpeg": ".jpg", "webp": ".webp", "svg": ".svg", "pdf": ".pdf", "mp3": ".mp3", "wav": ".wav"}
MIME = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp", "svg": "image/svg+xml", "pdf": "application/pdf",
        "mp3": "audio/mpeg", "wav": "audio/wav"}


class UploadError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def safe_filename(name: str, kind: str) -> str:
    stem = Path(name or "upload").stem
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip(".-")[:80] or "asset"
    return stem + EXT[kind]


def validate(data: bytes, filename: str) -> tuple[str, bytes]:
    """Return (kind, bytes to store). Raises UploadError."""
    if len(data) == 0:
        raise UploadError(f"{filename}: empty file")
    if len(data) > config.max_file_bytes:
        raise UploadError(f"{filename}: {len(data) / 1e6:.1f} MB exceeds the 25 MB limit", 413)
    kind = sniff(data)
    if kind is None or kind not in ALLOWED:
        raise UploadError(f"{filename}: unsupported file type (allowed: PNG, JPEG, WebP, SVG, PDF, MP3, WAV — detected by content, not extension)", 415)
    if kind == "svg":
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise UploadError(f"{filename}: SVG must be UTF-8") from None
        try:
            data = sanitize_svg(text).encode("utf-8")
        except Exception as e:
            raise UploadError(f"{filename}: invalid or unsafe SVG ({type(e).__name__})") from None
    if kind in ("png", "jpeg", "webp"):
        from PIL import Image

        try:
            with Image.open(io.BytesIO(data)) as im:
                im.verify()
                if im.size[0] * im.size[1] > 80_000_000:
                    raise UploadError(f"{filename}: image too large ({im.size[0]}×{im.size[1]})")
        except UploadError:
            raise
        except Exception:
            raise UploadError(f"{filename}: corrupt image") from None
    return kind, data


def unique_path(dir_: Path, name: str) -> Path:
    p = dir_ / name
    i = 1
    while p.exists():
        p = dir_ / f"{Path(name).stem}-{i}{Path(name).suffix}"
        i += 1
    return p


def analyze(kind: str, path: Path, project_dir: Path) -> dict:
    t0 = time.time()
    try:
        if kind == "svg":
            a = analyze_svg(str(path))
            # the full path data can be large: keep it, but cap per-shape d strings
            for s in a.get("shapes", []):
                if len(s.get("d", "")) > 4000:
                    s["d"] = s["d"][:4000] + "…"
        elif kind in ("png", "jpeg", "webp"):
            a = analyze_image(str(path))
        elif kind == "pdf":
            pages_dir = project_dir / "assets" / ".pages" / path.stem
            a = analyze_pdf(str(path), str(pages_dir))
            for pg in a.get("pages", []):
                if pg.get("image"):
                    pg["image"] = str(Path(pg["image"]).relative_to(project_dir))
        elif kind in ("mp3", "wav"):
            a = analyze_audio(str(path))
        else:
            a = {"type": "unknown"}
    except Exception as e:
        a = {"type": kind, "error": f"analysis failed: {type(e).__name__}: {e}"}
    a["analysis_seconds"] = round(time.time() - t0, 3)
    return a


def thumbnail(kind: str, path: Path, project_dir: Path, size: int = 320) -> str | None:
    tdir = project_dir / "assets" / ".thumbs"
    tdir.mkdir(parents=True, exist_ok=True)
    out = tdir / (path.stem + ".png")
    try:
        from PIL import Image

        if kind == "svg":
            import cairosvg

            png = cairosvg.svg2png(url=str(path), output_width=size, unsafe=False)
            im = Image.open(io.BytesIO(png))
        elif kind in ("png", "jpeg", "webp"):
            im = Image.open(path)
        elif kind == "pdf":
            import pymupdf

            doc = pymupdf.open(str(path))
            pix = doc[0].get_pixmap(dpi=60)
            im = Image.open(io.BytesIO(pix.tobytes("png")))
        else:
            return None
        im = im.convert("RGBA")
        im.thumbnail((size, size))
        im.save(out)
        return str(out.relative_to(project_dir))
    except Exception:
        return None


def summarize_for_llm(asset: dict) -> str:
    """Compact analysis text for the director's context."""
    a = dict(asset.get("analysis") or {})
    kind = asset["kind"]
    if kind == "svg":
        a = {k: a.get(k) for k in ("viewBox", "bounds", "shape_count", "colors", "gradients", "groups", "pivot", "symmetry", "warnings")} | {
            "shapes": [{k: s.get(k) for k in ("id", "index", "tag", "bbox", "area", "fill", "stroke", "fill_rule")} for s in (asset.get("analysis") or {}).get("shapes", [])[:40]]
        }
    elif kind == "pdf":
        a = {k: a.get(k) for k in ("page_count", "hex_colors_in_text", "drawing_colors", "embedded_fonts", "font_mentions")} | {"text": (a.get("text") or "")[:3000]}
    return json.dumps(a, separators=(",", ":"), default=str)[:8000]
