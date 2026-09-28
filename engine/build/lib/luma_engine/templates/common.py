"""Shared building blocks for the bundled templates: a logo "kit" that works with any
SVG (symbol/wordmark separation, parts, pivot, lockup) and reusable sound design."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import skia
from shapely.geometry import Point

from .. import audio as A
from ..brand import Color, contrast_ratio, skia_matrix
from ..layers import Frame, rasterize_mask
from ..layout import build_lockup
from ..svg import (
    SVGDocument, Shape, detect_pivot, part_angle, polygon_to_skia, split_radial, split_symbol_wordmark, union_polygons,
)
from ..svg import rotate as m_rotate
from ..svg import scale as m_scale
from ..text import TextRun, shape_text


@dataclass
class Part:
    """One constructible piece of the symbol, in SVG document space."""

    path: skia.Path
    polygon: object
    shape: Shape  # the source shape (paint, gradients)
    centroid: np.ndarray
    angle: float = 0.0  # degrees from pivot to centroid (screen convention)
    radius: float = 0.0  # max distance from pivot
    is_hub: bool = False  # small compact part at the pivot (rivet/dot)
    is_piece: bool = False  # produced by splitting (final frame uses the original shape)
    order: int = 0

    def fill_paint(self, alpha: float = 1.0) -> skia.Paint:
        p = self.shape.fill_paint(alpha)
        if p is None:
            p = self.shape.stroke_paint(alpha)
        return p if p is not None else skia.Paint(AntiAlias=True, Color4f=skia.Color4f(1, 1, 1, alpha))


@dataclass
class LogoKit:
    logo: str
    width: int
    height: int
    wordmark: str | None = None
    font: str = "Inter"
    weight: int = 600
    tracking: float = 40.0
    symbol_ids: list | None = None
    wordmark_color: str | None = None
    background: str = "#0B0F2A"
    split_single: int = 5
    arrangement: str = "horizontal"
    symbol_height: float | None = None
    stage_height: float | None = None
    doc: SVGDocument = field(init=False)

    def __post_init__(self):
        self.doc = SVGDocument.load(self.logo)
        shapes = [s for s in self.doc.shapes if s.fill is not None or s.stroke is not None]
        if self.symbol_ids:
            ids = set(map(str, self.symbol_ids))
            sym = [s for s in shapes if s.id in ids or str(s.index) in ids]
            wm = [s for s in shapes if s not in sym]
        else:
            sym, wm = split_symbol_wordmark(shapes)
        self.symbol_shapes: list[Shape] = sym
        self.wordmark_shapes: list[Shape] = wm if not self.wordmark else []
        self.run: TextRun | None = shape_text(self.wordmark, self.font, self.weight, 100.0, self.tracking) if self.wordmark else None
        self.symbol_bounds = self.doc.bounds(self.symbol_shapes)
        if self.run is not None:
            x0, y0, x1, y1 = self.run.bounds()
            # lay out on cap height so the wordmark centres optically on the symbol
            self.wordmark_bounds = (min(0.0, x0), -self.run.cap_height, max(self.run.advance, x1), 0.0)
        elif self.wordmark_shapes:
            self.wordmark_bounds = self.doc.bounds(self.wordmark_shapes)
        else:
            self.wordmark_bounds = None
        W, H = self.width, self.height
        self.lockup = build_lockup(self.symbol_bounds, W, H, self.wordmark_bounds, arrangement=self.arrangement,
                                   symbol_height=self.symbol_height)
        sh = self.stage_height or H * (0.46 if self.wordmark_bounds is not None else 0.44)
        self.stage = build_lockup(self.symbol_bounds, W, H, None, symbol_height=sh)
        self._build_parts()
        self._palette()

    # -- structure ------------------------------------------------------------------------
    def _build_parts(self):
        polys = [s.polygon() for s in self.symbol_shapes]
        areas = [p.area for p in polys]
        big = max(areas) if areas else 1.0
        if len(self.symbol_shapes) >= 2:
            self.pivot = detect_pivot(polys)
            parts = []
            for s, poly in zip(self.symbol_shapes, polys):
                c = np.array([poly.centroid.x, poly.centroid.y])
                parts.append(Part(s.outline_path(), poly, s, c))
        else:
            s = self.symbol_shapes[0]
            poly = polys[0]
            self.pivot = detect_pivot([poly])
            c = poly.centroid
            if np.linalg.norm(self.pivot - np.array([c.x, c.y])) < 1e-6 or not poly.buffer(1e-6).contains(poly.centroid):
                self.pivot = np.array([c.x, c.y])
            n = max(2, self.split_single)
            angles = [-90 + 360 * k / n for k in range(n)]
            pieces = split_radial(poly, self.pivot, angles)
            parts = [Part(polygon_to_skia(pc), pc, s, np.array([pc.centroid.x, pc.centroid.y]), is_piece=True) for pc in pieces]
        size = max(self.symbol_bounds[2] - self.symbol_bounds[0], self.symbol_bounds[3] - self.symbol_bounds[1])
        for p in parts:
            p.angle = part_angle(p.polygon, self.pivot)
            pts = np.asarray(p.polygon.exterior.coords) if hasattr(p.polygon, "exterior") else np.concatenate(
                [np.asarray(g.exterior.coords) for g in p.polygon.geoms])
            p.radius = float(np.max(np.linalg.norm(pts - self.pivot, axis=1)))
            near = p.polygon.distance(Point(*self.pivot)) < 0.04 * size
            p.is_hub = p.polygon.area < 0.2 * big and near and np.linalg.norm(p.centroid - self.pivot) < 0.08 * size
        order = sorted([p for p in parts if not p.is_hub], key=lambda p: p.angle)
        for i, p in enumerate(order):
            p.order = i
        self.parts = parts
        self.blades = order
        self.hubs = [p for p in parts if p.is_hub]
        self.union = union_polygons([p.polygon for p in parts])

    def _palette(self):
        cols: list[Color] = []
        for s in self.symbol_shapes:
            for p in (s.fill, s.stroke):
                if isinstance(p, Color):
                    cols.append(p)
                elif p is not None:
                    cols.extend(st.color for st in p.stops)
        bg = Color.of(self.background)
        if not cols:
            cols = [Color.of("#FFFFFF")]
        self.colors = cols
        # light colour: the most saturated bright brand colour
        def score(c: Color):
            mx, mn = max(c.rgb), min(c.rgb)
            return (mx - mn) * 0.6 + c.luminance() * 0.4
        self.light_color = max(cols, key=score)
        self.accent = max(cols, key=lambda c: c.luminance())
        if self.wordmark_color:
            self.text_color = Color.of(self.wordmark_color)
        else:
            cand = [c for c in cols if contrast_ratio(c, bg) >= 4.5]
            self.text_color = max(cand, key=lambda c: c.luminance()) if cand else (Color.of("#F5F1EA") if bg.luminance() < 0.4 else Color.of("#111111"))

    # -- geometry helpers ----------------------------------------------------------------
    def to_frame(self, M: np.ndarray, p) -> np.ndarray:
        v = M @ np.array([p[0], p[1], 1.0])
        return v[:2] / v[2]

    def glide_matrix(self, u: float) -> np.ndarray:
        """Interpolate stage → lockup symbol matrices (both are scale + translate)."""
        a, b = self.stage.symbol, self.lockup.symbol
        return a + (b - a) * u

    def part_matrix(self, part: Part, M: np.ndarray, rot_deg: float = 0.0, scale: float = 1.0) -> np.ndarray:
        R = m_rotate(rot_deg, *self.pivot) if rot_deg else np.eye(3)
        S = m_scale(scale, scale, *self.pivot) if scale != 1.0 else np.eye(3)
        return M @ S @ R

    # -- drawing -------------------------------------------------------------------------
    def draw_symbol(self, frame: Frame, M: np.ndarray, alpha: float = 1.0) -> None:
        with frame.draw(M) as c:
            for s in self.symbol_shapes:
                s.draw(c, alpha)

    def wordmark_origin(self) -> tuple[float, float, float]:
        """(x, y, size) of the text baseline origin in frame space at the lockup."""
        Mw = self.lockup.wordmark
        o = self.to_frame(Mw, (0.0, 0.0))
        return float(o[0]), float(o[1]), 100.0 * float(Mw[0, 0])

    def draw_wordmark(self, frame: Frame, alpha: float = 1.0) -> None:
        if self.lockup.wordmark is None:
            return
        if self.run is not None:
            x, y, size = self.wordmark_origin()
            run = shape_text(self.wordmark, self.font, self.weight, size, self.tracking)
            with frame.draw() as c:
                c.save()
                c.translate(x, y)
                c.drawPath(run.path, skia.Paint(AntiAlias=True, Color4f=self.text_color.skia4f(self.text_color.a * alpha)))
                c.restore()
        else:
            with frame.draw(self.lockup.wordmark) as c:
                for s in self.wordmark_shapes:
                    s.draw(c, alpha)

    def sized_run(self) -> TextRun | None:
        if self.run is None or self.lockup.wordmark is None:
            return None
        _, _, size = self.wordmark_origin()
        return shape_text(self.wordmark, self.font, self.weight, size, self.tracking)

    def draw_reference(self, frame: Frame) -> None:
        frame.fill(self.background)
        self.draw_symbol(frame, self.lockup.symbol)
        self.draw_wordmark(frame)

    def lockup_mask(self, frame: Frame) -> np.ndarray:
        def draw(c):
            c.save()
            c.concat(skia_matrix(self.lockup.symbol))
            for s in self.symbol_shapes:
                c.drawPath(s.outline_path(), skia.Paint(AntiAlias=True, Color4f=skia.Color4f(1, 1, 1, 1)))
            c.restore()
            if self.lockup.wordmark is not None:
                if self.run is not None:
                    x, y, size = self.wordmark_origin()
                    run = shape_text(self.wordmark, self.font, self.weight, size, self.tracking)
                    c.save()
                    c.translate(x, y)
                    c.drawPath(run.path, skia.Paint(AntiAlias=True, Color4f=skia.Color4f(1, 1, 1, 1)))
                    c.restore()
                else:
                    c.save()
                    c.concat(skia_matrix(self.lockup.wordmark))
                    for s in self.wordmark_shapes:
                        c.drawPath(s.outline_path(), skia.Paint(AntiAlias=True, Color4f=skia.Color4f(1, 1, 1, 1)))
                    c.restore()

        return rasterize_mask(frame.w, frame.h, draw, frame.scale)


# ======================================================================================
# sound design helpers
# ======================================================================================

PENTATONIC = [1318.51, 1479.98, 1661.22, 1975.53, 2217.46, 2637.02, 2959.96]


def pan_for_x(x: float, width: float) -> float:
    return float(np.clip((x / width) * 2 - 1, -1, 1)) * 0.7


def seat_hit(freq: float, seed: int = 0) -> np.ndarray:
    """Clack + glass ping: a crisp 'part seats into place' sound (stereo)."""
    cl = A.to_stereo(A.clack(0.14, 700 + (seed % 3) * 90, seed)) * 0.9
    gp = A.glass_ping(freq, 1.6, 0.5, seed=seed) * 0.45
    n = max(len(cl), len(gp))
    out = np.zeros((n, 2))
    out[: len(cl)] += cl
    out[: len(gp)] += gp
    return out


def ease_time_scale(duration: float, natural: float, hold_min: float = 0.45) -> float:
    """Time-scale factor so a choreography of ``natural`` seconds fits ``duration``."""
    avail = max(duration - hold_min, 0.5)
    if avail < natural:
        return avail / natural
    return min(avail / natural, 1.6)


__all__ = ["LogoKit", "PENTATONIC", "Part", "ease_time_scale", "pan_for_x", "seat_hit"]

