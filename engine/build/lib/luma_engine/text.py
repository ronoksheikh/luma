"""Text shaping: HarfBuzz-shaped glyph outlines from any TTF/OTF, as skia paths.

>>> run = shape_text("Aurora", font="Inter", weight=600, size=120, tracking=20)
>>> run.path              # combined skia.Path, baseline at y=0, starts at x=0
>>> run.glyphs[0].path    # per-glyph path (already positioned) for letter animation
"""
from __future__ import annotations

import functools
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import skia
import uharfbuzz as hb
from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont

FONT_DIRS = [
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    os.path.expanduser("~/.fonts"),
    os.path.expanduser("~/.local/share/fonts"),
]


@dataclass
class FontInfo:
    path: str
    family: str
    style: str
    weight: int
    italic: bool
    index: int = 0


@functools.lru_cache(maxsize=1)
def font_index() -> list[FontInfo]:
    """Scan font directories (plus LUMA_FONT_DIRS) and read family/weight metadata."""
    dirs = list(FONT_DIRS) + [d for d in os.environ.get("LUMA_FONT_DIRS", "").split(":") if d]
    out: list[FontInfo] = []
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for root, _, files in os.walk(d):
            for fn in files:
                if not fn.lower().endswith((".ttf", ".otf", ".ttc")):
                    continue
                p = os.path.join(root, fn)
                try:
                    out.extend(_describe_font(p))
                except Exception:
                    continue
    return out


def _describe_font(path: str) -> list[FontInfo]:
    infos = []
    n = 1
    if path.lower().endswith(".ttc"):
        from fontTools.ttLib import TTCollection

        n = len(TTCollection(path, lazy=True).fonts)
    for i in range(min(n, 16)):
        f = TTFont(path, fontNumber=i, lazy=True)
        name = f["name"]
        fam = name.getDebugName(16) or name.getDebugName(1) or Path(path).stem
        sub = name.getDebugName(17) or name.getDebugName(2) or "Regular"
        weight = f["OS/2"].usWeightClass if "OS/2" in f else 400
        italic = bool(f["OS/2"].fsSelection & 1) if "OS/2" in f else "italic" in sub.lower()
        infos.append(FontInfo(path, fam, sub, int(weight), italic, i))
        f.close()
    return infos


def find_font(family: str = "Inter", weight: int = 400, italic: bool = False) -> FontInfo:
    """Best match by family name (case-insensitive, prefix allowed) then weight distance.

    ``family`` may also be a path to a font file.
    """
    if os.path.isfile(family):
        return _describe_font(family)[0]
    fam = family.lower().replace(" ", "")
    cands = [f for f in font_index() if f.family.lower().replace(" ", "") == fam]
    if not cands:
        cands = [f for f in font_index() if f.family.lower().replace(" ", "").startswith(fam)]
    if not cands:
        try:  # fontconfig fallback (handles aliases like "sans-serif")
            path = subprocess.run(["fc-match", "-f", "%{file}", family], capture_output=True, text=True, timeout=10).stdout.strip()
            if path and os.path.isfile(path):
                return _describe_font(path)[0]
        except Exception:
            pass
        cands = font_index()
        if not cands:
            raise FileNotFoundError("no fonts installed")
    return min(cands, key=lambda f: (f.italic != italic, abs(f.weight - weight), len(f.style)))


def list_families() -> list[str]:
    return sorted({f.family for f in font_index()})


class _SkiaPen(BasePen):
    def __init__(self, glyphset, path: skia.Path, scale: float, dx: float, dy: float):
        super().__init__(glyphset)
        self.p, self.s, self.dx, self.dy = path, scale, dx, dy

    def _xy(self, pt):
        return (self.dx + pt[0] * self.s, self.dy - pt[1] * self.s)  # font y-up → screen y-down

    def _moveTo(self, pt):
        self.p.moveTo(*self._xy(pt))

    def _lineTo(self, pt):
        self.p.lineTo(*self._xy(pt))

    def _curveToOne(self, p1, p2, p3):
        a, b, c = self._xy(p1), self._xy(p2), self._xy(p3)
        self.p.cubicTo(a[0], a[1], b[0], b[1], c[0], c[1])

    def _qCurveToOne(self, p1, p2):
        a, b = self._xy(p1), self._xy(p2)
        self.p.quadTo(a[0], a[1], b[0], b[1])

    def _closePath(self):
        self.p.close()

    def _endPath(self):
        pass


@dataclass
class Glyph:
    char: str
    cluster: int
    gid: int
    x: float
    y: float
    advance: float
    path: skia.Path

    def bounds(self) -> tuple[float, float, float, float]:
        r = self.path.computeTightBounds()
        return (r.left(), r.top(), r.right(), r.bottom())


@dataclass
class TextRun:
    text: str
    font: FontInfo
    size: float
    glyphs: list[Glyph] = field(default_factory=list)
    advance: float = 0.0
    ascender: float = 0.0
    descender: float = 0.0
    cap_height: float = 0.0
    x_height: float = 0.0

    @property
    def path(self) -> skia.Path:
        p = skia.Path()
        for g in self.glyphs:
            p.addPath(g.path)
        return p

    def bounds(self) -> tuple[float, float, float, float]:
        r = self.path.computeTightBounds()
        return (r.left(), r.top(), r.right(), r.bottom())

    def words(self) -> list[list[Glyph]]:
        out, cur = [], []
        for g in self.glyphs:
            if g.char.isspace():
                if cur:
                    out.append(cur)
                cur = []
            else:
                cur.append(g)
        if cur:
            out.append(cur)
        return out


@functools.lru_cache(maxsize=64)
def _load(path: str, index: int):
    blob = hb.Blob.from_file_path(path)
    face = hb.Face(blob, index)
    font = hb.Font(face)
    tt = TTFont(path, fontNumber=index, lazy=True)
    return face, font, tt, tt.getGlyphSet(), tt.getGlyphOrder()


def shape_text(text: str, font: str = "Inter", weight: int = 400, size: float = 100.0, tracking: float = 0.0,
               italic: bool = False, features: dict | None = None, direction: str | None = None,
               language: str | None = None, script: str | None = None) -> TextRun:
    """Shape ``text`` with HarfBuzz (kerning and ligatures on by default).

    ``tracking`` is in 1/1000 em (like design tools).  Baseline at y = 0, origin x = 0.
    """
    fi = find_font(font, weight, italic)
    face, hbfont, tt, gs, order = _load(fi.path, fi.index)
    upem = face.upem
    hbfont.scale = (upem, upem)
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    if direction:
        buf.direction = direction
    if language:
        buf.language = language
    if script:
        buf.script = script
    feats = {"kern": True, "liga": True}
    feats.update(features or {})
    hb.shape(hbfont, buf, feats)
    s = size / upem
    track = tracking / 1000.0 * size
    run = TextRun(text, fi, size)
    x = 0.0
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
        gname = order[info.codepoint] if info.codepoint < len(order) else ".notdef"
        path = skia.Path()
        pen = _SkiaPen(gs, path, s, x + pos.x_offset * s, -pos.y_offset * s)
        try:
            gs[gname].draw(pen)
        except KeyError:
            pass
        adv = pos.x_advance * s
        ch = text[info.cluster] if info.cluster < len(text) else ""
        run.glyphs.append(Glyph(ch, info.cluster, info.codepoint, x, 0.0, adv, path))
        x += adv + track
    run.advance = x - (track if run.glyphs else 0.0)
    os2 = tt["OS/2"] if "OS/2" in tt else None
    hhea = tt["hhea"]
    run.ascender = hhea.ascent * s
    run.descender = hhea.descent * s
    run.cap_height = (getattr(os2, "sCapHeight", 0) or 0.7 * upem) * s if os2 else 0.7 * size
    run.x_height = (getattr(os2, "sxHeight", 0) or 0.5 * upem) * s if os2 else 0.5 * size
    return run


def wrap_words(words: list[str], font: str, weight: int, size: float, max_width: float, tracking: float = 0.0) -> list[list[int]]:
    """Greedy line breaking; returns word indices per line."""
    space = shape_text(" ", font, weight, size, tracking).advance
    lines, cur, w = [], [], 0.0
    for i, word in enumerate(words):
        ww = shape_text(word, font, weight, size, tracking).advance
        if cur and w + space + ww > max_width:
            lines.append(cur)
            cur, w = [], 0.0
        w = ww if not cur else w + space + ww
        cur.append(i)
    if cur:
        lines.append(cur)
    return lines


def draw_run(canvas: skia.Canvas, run: TextRun, x: float, y: float, paint: skia.Paint) -> None:
    canvas.save()
    canvas.translate(x, y)
    canvas.drawPath(run.path, paint)
    canvas.restore()


__all__ = ["FontInfo", "Glyph", "TextRun", "draw_run", "find_font", "font_index", "list_families", "shape_text", "wrap_words"]

_ = np  # numpy is part of the public contract for users combining glyph data with arrays
