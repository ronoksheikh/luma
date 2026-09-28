"""Automatic asset analysis (stored as JSON by Luma Studio and shown to the director).

* SVG  — paths, bounding boxes, fills, gradients (incl. objectBoundingBox), viewBox,
         symbol/wordmark grouping, pivot and symmetry.
* image — size, dominant palette (k-means), transparency.
* PDF  — page count, text, hex colours and font names (brand guidelines), page renders.
* audio — duration, integrated loudness (LUFS), peak.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from .brand import dominant_palette, hex_codes_in_text

MAGIC = [
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"%PDF-", "pdf"),
    (b"ID3", "mp3"),
    (b"\xff\xfb", "mp3"),
    (b"\xff\xf3", "mp3"),
    (b"\xff\xf2", "mp3"),
]


def sniff(data: bytes) -> str | None:
    """Detect a file type from magic bytes (never from the extension)."""
    head = data[:64]
    for sig, kind in MAGIC:
        if head.startswith(sig):
            return kind
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    txt = data[:4096].lstrip(b"\xef\xbb\xbf").lstrip()
    if txt.startswith(b"<"):
        low = txt.lower()
        if b"<svg" in low[:4096] and (low.startswith(b"<?xml") or low.startswith(b"<svg") or low.startswith(b"<!--") or low.startswith(b"<!doctype svg")):
            return "svg"
    return None


def analyze_svg(path: str) -> dict:
    from .svg import SVGDocument

    doc = SVGDocument.load(path)
    return doc.analysis()


def analyze_image(path: str) -> dict:
    from PIL import Image

    im = Image.open(path)
    w, h = im.size
    mode = im.mode
    im = im.convert("RGBA")
    arr = np.asarray(im).astype(np.float32) / 255.0
    alpha = arr[..., 3]
    transparent = bool((alpha < 0.999).any())
    small = arr
    if max(w, h) > 512:
        s = 512 / max(w, h)
        small = np.asarray(im.resize((max(1, int(w * s)), max(1, int(h * s))))).astype(np.float32) / 255.0
    pal = dominant_palette(small[..., :3], 6, small[..., 3] if transparent else None)
    out = {
        "type": "image",
        "width": w,
        "height": h,
        "mode": mode,
        "has_transparency": transparent,
        "transparent_fraction": round(float((alpha < 0.5).mean()), 4),
        "palette": pal,
    }
    if transparent:
        ys, xs = np.nonzero(alpha > 0.5)
        if len(xs):
            out["content_bbox"] = [int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)]
    return out


def analyze_pdf(path: str, render_dir: str | None = None, max_pages: int = 20, dpi: int = 110) -> dict:
    import pymupdf

    doc = pymupdf.open(path)
    pages = []
    all_text = []
    fonts: list[str] = []
    draw_colors: list[str] = []
    for i, page in enumerate(doc):
        if i >= max_pages:
            break
        text = page.get_text()
        all_text.append(text)
        for f in page.get_fonts(full=True):
            name = f[3] or ""
            name = re.sub(r"^[A-Z]{6}\+", "", name)  # strip subset prefix
            if name and name not in fonts:
                fonts.append(name)
        try:
            for d in page.get_drawings():
                for key in ("fill", "color"):
                    c = d.get(key)
                    if c:
                        hx = "#" + "".join(f"{round(v * 255):02X}" for v in c[:3])
                        if hx not in draw_colors:
                            draw_colors.append(hx)
        except Exception:
            pass
        entry = {"index": i, "width": page.rect.width, "height": page.rect.height, "text_excerpt": text[:1200]}
        if render_dir:
            Path(render_dir).mkdir(parents=True, exist_ok=True)
            pix = page.get_pixmap(dpi=dpi)
            p = str(Path(render_dir) / f"page_{i + 1:03d}.png")
            pix.save(p)
            entry["image"] = p
        pages.append(entry)
    text = "\n".join(all_text)
    font_mentions = sorted(set(re.findall(r"\b(Inter|Roboto|Helvetica(?: Neue)?|Arial|Montserrat|Poppins|Lato|Open Sans|Source Sans(?: Pro)?|Noto Sans|SF Pro|Futura|Gotham|Avenir(?: Next)?|Proxima Nova|Playfair Display|Georgia|Garamond|IBM Plex Sans|Manrope|DM Sans|Space Grotesk)\b(?:\s+(?:Thin|Light|Regular|Medium|SemiBold|Semibold|Bold|ExtraBold|Black))?", text)))
    return {
        "type": "pdf",
        "page_count": doc.page_count,
        "pages": pages,
        "hex_colors_in_text": hex_codes_in_text(text),
        "drawing_colors": draw_colors[:40],
        "embedded_fonts": fonts,
        "font_mentions": font_mentions,
        "text": text[:20000],
    }


def analyze_audio(path: str) -> dict:
    from . import audio as A

    x = A.read_audio(path)
    return {
        "type": "audio",
        "duration_s": round(len(x) / A.SR, 3),
        "lufs": round(A.lufs(x), 2) if len(x) > A.SR * 0.4 else None,
        "sample_peak_db": round(A.gain_to_db(float(np.max(np.abs(x))) if len(x) else 0.0), 2),
        "true_peak_dbtp": round(A.true_peak_db(x), 2) if len(x) else None,
    }


def analyze_file(path: str, render_dir: str | None = None) -> dict:
    data = Path(path).read_bytes()[:8192]
    kind = sniff(data)
    if kind == "svg":
        return analyze_svg(path)
    if kind in ("png", "jpeg", "webp"):
        return analyze_image(path)
    if kind == "pdf":
        return analyze_pdf(path, render_dir)
    if kind in ("mp3", "wav"):
        return analyze_audio(path)
    return {"type": "unknown"}


__all__ = ["analyze_audio", "analyze_file", "analyze_image", "analyze_pdf", "analyze_svg", "sniff"]
