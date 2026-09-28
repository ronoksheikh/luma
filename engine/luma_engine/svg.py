"""SVG geometry: parsing, transforms, flattening, skia conversion, splitting and analysis.

Everything is normalised to *document user space* (viewBox units) with element
transforms already applied.  Path segments are reduced to lines and cubic Béziers
(quadratics are elevated exactly, arcs are converted per SVG 1.1 §F.6).

Typical use::

    doc = SVGDocument.load("assets/logo.svg")
    for shape in doc.shapes:
        path = shape.skia_path()          # exact curves
        poly = shape.polygon()            # shapely, flattened (for splitting/analysis)
    pieces = split_polygon(poly, [((x0, y0), (x1, y1))])   # area-checked
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import skia
from defusedxml import ElementTree as ET
from shapely import affinity
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import split as shp_split
from shapely.ops import unary_union

from .brand import Color, Gradient, GradientStop, parse_color, skia_matrix

SVG_NS = "{http://www.w3.org/2000/svg}"
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"

# ======================================================================================
# path data
# ======================================================================================


@dataclass
class Subpath:
    """A contour: start point, then segments as ('L', p1) or ('C', c1, c2, p1)."""

    start: np.ndarray
    segments: list = field(default_factory=list)
    closed: bool = False

    def points(self) -> np.ndarray:
        pts = [self.start]
        for seg in self.segments:
            pts.extend(seg[1:])
        return np.array(pts)

    @property
    def end(self) -> np.ndarray:
        return self.segments[-1][-1] if self.segments else self.start


class PathGeom:
    """A list of subpaths in some coordinate space."""

    def __init__(self, subpaths: list[Subpath] | None = None):
        self.subpaths: list[Subpath] = subpaths or []

    # -- construction ------------------------------------------------------------------
    @classmethod
    def from_d(cls, d: str) -> "PathGeom":
        return parse_path_d(d)

    def copy(self) -> "PathGeom":
        return PathGeom(
            [Subpath(sp.start.copy(), [(s[0], *[p.copy() for p in s[1:]]) for s in sp.segments], sp.closed) for sp in self.subpaths]
        )

    def transformed(self, m: np.ndarray) -> "PathGeom":
        m = np.asarray(m, dtype=np.float64)

        def tp(p):
            v = m @ np.array([p[0], p[1], 1.0])
            return v[:2] / v[2]

        out = []
        for sp in self.subpaths:
            out.append(Subpath(tp(sp.start), [(s[0], *[tp(p) for p in s[1:]]) for s in sp.segments], sp.closed))
        return PathGeom(out)

    # -- conversions -------------------------------------------------------------------
    def to_skia(self, fill_rule: str = "nonzero") -> skia.Path:
        p = skia.Path()
        for sp in self.subpaths:
            p.moveTo(float(sp.start[0]), float(sp.start[1]))
            for seg in sp.segments:
                if seg[0] == "L":
                    p.lineTo(float(seg[1][0]), float(seg[1][1]))
                else:
                    c1, c2, e = seg[1], seg[2], seg[3]
                    p.cubicTo(float(c1[0]), float(c1[1]), float(c2[0]), float(c2[1]), float(e[0]), float(e[1]))
            if sp.closed:
                p.close()
        p.setFillType(skia.PathFillType.kEvenOdd if fill_rule == "evenodd" else skia.PathFillType.kWinding)
        return p

    def to_d(self, precision: int = 4) -> str:
        f = lambda v: f"{v:.{precision}f}".rstrip("0").rstrip(".")  # noqa: E731
        out = []
        for sp in self.subpaths:
            out.append(f"M{f(sp.start[0])} {f(sp.start[1])}")
            for s in sp.segments:
                if s[0] == "L":
                    out.append(f"L{f(s[1][0])} {f(s[1][1])}")
                else:
                    out.append("C" + " ".join(f"{f(p[0])} {f(p[1])}" for p in s[1:]))
            if sp.closed:
                out.append("Z")
        return "".join(out)

    def flatten(self, tol: float = 0.05) -> list[tuple[np.ndarray, bool]]:
        """Polyline approximation of each subpath: [(Nx2 array, closed)].

        Cubic subdivision count uses Wang's formula so the chord error is ≤ ``tol``.
        """
        rings = []
        for sp in self.subpaths:
            pts = [sp.start]
            cur = sp.start
            for seg in sp.segments:
                if seg[0] == "L":
                    pts.append(seg[1])
                    cur = seg[1]
                else:
                    p0, p1, p2, p3 = cur, seg[1], seg[2], seg[3]
                    dd = max(np.linalg.norm(p0 - 2 * p1 + p2), np.linalg.norm(p1 - 2 * p2 + p3))
                    n = int(min(max(math.ceil(math.sqrt(0.75 * dd / max(tol, 1e-9))), 1), 1024))
                    t = np.linspace(0, 1, n + 1)[1:, None]
                    mt = 1 - t
                    pts.extend(mt**3 * p0 + 3 * mt * mt * t * p1 + 3 * mt * t * t * p2 + t**3 * p3)
                    cur = p3
            arr = np.array(pts, dtype=np.float64)
            if sp.closed and len(arr) > 1 and np.allclose(arr[0], arr[-1]):
                arr = arr[:-1]
            rings.append((arr, sp.closed))
        return rings

    def bounds(self) -> tuple[float, float, float, float]:
        """Tight bounds (x0, y0, x1, y1) using skia's exact curve bounds."""
        r = self.to_skia().computeTightBounds()
        return (r.left(), r.top(), r.right(), r.bottom())

    def length(self) -> float:
        m = skia.PathMeasure(self.to_skia(), False)
        total = 0.0
        while True:
            total += m.getLength()
            if not m.nextContour():
                break
        return total


_NUM_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_CMD_RE = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])|([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)")
_ARGC = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}


def _tokenize_path(d: str):
    """Yield commands and numbers; handles '1.5.5', '-1-2' and compact arc flags."""
    i, n = 0, len(d)
    arg_index = 0
    cmd = None
    while i < n:
        ch = d[i]
        if ch in " \t\r\n,":
            i += 1
            continue
        if ch.isalpha():
            if ch.upper() not in _ARGC:
                raise ValueError(f"unknown path command {ch!r}")
            cmd = ch.upper()
            arg_index = 0
            yield ch
            i += 1
            continue
        # arc flags (4th and 5th args) may be written without separators: "a1 1 0 00 1 1"
        if cmd == "A" and (arg_index % 7) in (3, 4) and ch in "01":
            yield float(ch)
            arg_index += 1
            i += 1
            continue
        m = _NUM_RE.match(d, i)
        if not m:
            raise ValueError(f"bad path data near {d[i:i+20]!r}")
        yield float(m.group(0))
        arg_index += 1
        i = m.end()


def parse_path_d(d: str) -> PathGeom:
    """Parse SVG path data (M/L/H/V/C/S/Q/T/A/Z, absolute and relative)."""
    toks = list(_tokenize_path(d or ""))
    subpaths: list[Subpath] = []
    cur = np.zeros(2)
    sp: Subpath | None = None
    last_cmd = ""
    last_ctrl: np.ndarray | None = None  # for S/T reflection
    i = 0
    cmd = None

    def ensure_sp():
        nonlocal sp
        if sp is None:
            sp = Subpath(cur.copy())
            subpaths.append(sp)
        return sp

    while i < len(toks):
        t = toks[i]
        if isinstance(t, str):
            cmd = t
            i += 1
            if cmd in "Zz":
                if sp is not None:
                    if not np.allclose(cur, sp.start):
                        sp.segments.append(("L", sp.start.copy()))
                    sp.closed = True
                    cur = sp.start.copy()
                sp = None
                last_cmd = "Z"
                last_ctrl = None
                continue
        elif cmd is None:
            raise ValueError("path data must start with a command")
        if cmd is None:
            break
        U = cmd.upper()
        if U == "Z":
            raise ValueError("unexpected number after Z")
        rel = cmd.islower()
        argc = _ARGC[U]
        args = toks[i : i + argc]
        if len(args) < argc or any(isinstance(a, str) for a in args):
            raise ValueError(f"not enough arguments for {cmd}")
        i += argc
        a = np.array(args, dtype=np.float64)
        base = cur if rel else np.zeros(2)
        if U == "M":
            p = base + a[:2]
            sp = Subpath(p.copy())
            subpaths.append(sp)
            cur = p
            cmd = "l" if rel else "L"  # implicit lineto for subsequent pairs
            last_ctrl = None
            last_cmd = "M"
            continue
        s = ensure_sp()
        if U == "L":
            p = base + a[:2]
            s.segments.append(("L", p))
            cur = p
            last_ctrl = None
        elif U == "H":
            p = np.array([(cur[0] if rel else 0) + a[0], cur[1]])
            s.segments.append(("L", p))
            cur = p
            last_ctrl = None
        elif U == "V":
            p = np.array([cur[0], (cur[1] if rel else 0) + a[0]])
            s.segments.append(("L", p))
            cur = p
            last_ctrl = None
        elif U == "C":
            c1, c2, p = base + a[0:2], base + a[2:4], base + a[4:6]
            s.segments.append(("C", c1, c2, p))
            cur, last_ctrl = p, c2
        elif U == "S":
            c1 = 2 * cur - last_ctrl if last_cmd in "CS" and last_ctrl is not None else cur.copy()
            c2, p = base + a[0:2], base + a[2:4]
            s.segments.append(("C", c1, c2, p))
            cur, last_ctrl = p, c2
        elif U == "Q":
            q, p = base + a[0:2], base + a[2:4]
            s.segments.append(("C", cur + 2 / 3 * (q - cur), p + 2 / 3 * (q - p), p))
            cur, last_ctrl = p, q
        elif U == "T":
            q = 2 * cur - last_ctrl if last_cmd in "QT" and last_ctrl is not None else cur.copy()
            p = base + a[0:2]
            s.segments.append(("C", cur + 2 / 3 * (q - cur), p + 2 / 3 * (q - p), p))
            cur, last_ctrl = p, q
        elif U == "A":
            rx, ry, phi, fa, fs = a[0], a[1], a[2], a[3], a[4]
            p = base + a[5:7]
            for seg in arc_to_cubics(cur, p, rx, ry, phi, bool(fa), bool(fs)):
                s.segments.append(seg)
            cur = p
            last_ctrl = None
        last_cmd = U
    return PathGeom(subpaths)


def arc_to_cubics(p0, p1, rx, ry, phi_deg, large_arc, sweep):
    """Endpoint → centre parameterisation (SVG 1.1 F.6.5) then ≤90° cubic pieces."""
    p0 = np.asarray(p0, dtype=np.float64)
    p1 = np.asarray(p1, dtype=np.float64)
    if np.allclose(p0, p1):
        return []
    rx, ry = abs(rx), abs(ry)
    if rx < 1e-12 or ry < 1e-12:
        return [("L", p1.copy())]
    phi = math.radians(phi_deg % 360)
    cp, sp_ = math.cos(phi), math.sin(phi)
    dx2, dy2 = (p0 - p1) / 2
    x1p = cp * dx2 + sp_ * dy2
    y1p = -sp_ * dx2 + cp * dy2
    lam = (x1p**2) / rx**2 + (y1p**2) / ry**2
    if lam > 1:
        s = math.sqrt(lam)
        rx, ry = rx * s, ry * s
    num = rx**2 * ry**2 - rx**2 * y1p**2 - ry**2 * x1p**2
    den = rx**2 * y1p**2 + ry**2 * x1p**2
    coef = math.sqrt(max(num / den, 0.0)) if den else 0.0
    if large_arc == sweep:
        coef = -coef
    cxp = coef * rx * y1p / ry
    cyp = -coef * ry * x1p / rx
    cx = cp * cxp - sp_ * cyp + (p0[0] + p1[0]) / 2
    cy = sp_ * cxp + cp * cyp + (p0[1] + p1[1]) / 2

    def ang(u, v):
        a = math.atan2(u[0] * v[1] - u[1] * v[0], u[0] * v[0] + u[1] * v[1])
        return a

    u = ((x1p - cxp) / rx, (y1p - cyp) / ry)
    v = ((-x1p - cxp) / rx, (-y1p - cyp) / ry)
    th1 = ang((1, 0), u)
    dth = ang(u, v)
    if not sweep and dth > 0:
        dth -= 2 * math.pi
    elif sweep and dth < 0:
        dth += 2 * math.pi
    n = max(1, int(math.ceil(abs(dth) / (math.pi / 2) - 1e-9)))
    step = dth / n
    k = 4 / 3 * math.tan(step / 4)
    segs = []
    t = th1

    def pt(theta):
        x, y = rx * math.cos(theta), ry * math.sin(theta)
        return np.array([cp * x - sp_ * y + cx, sp_ * x + cp * y + cy])

    def dpt(theta):
        x, y = -rx * math.sin(theta), ry * math.cos(theta)
        return np.array([cp * x - sp_ * y, sp_ * x + cp * y])

    for i in range(n):
        t2 = t + step
        a0, a1 = pt(t), pt(t2)
        c1 = a0 + k * dpt(t)
        c2 = a1 - k * dpt(t2)
        segs.append(("C", c1, c2, p1.copy() if i == n - 1 else a1))
        t = t2
    return segs


# ======================================================================================
# transforms
# ======================================================================================


def parse_transform(s: str | None) -> np.ndarray:
    m = np.eye(3)
    if not s:
        return m
    for name, args in re.findall(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)", s):
        v = [float(x) for x in _NUM_RE.findall(args)]
        if name == "matrix" and len(v) == 6:
            t = np.array([[v[0], v[2], v[4]], [v[1], v[3], v[5]], [0, 0, 1]])
        elif name == "translate":
            t = np.array([[1, 0, v[0]], [0, 1, v[1] if len(v) > 1 else 0], [0, 0, 1]])
        elif name == "scale":
            sx = v[0]
            sy = v[1] if len(v) > 1 else sx
            t = np.diag([sx, sy, 1.0])
        elif name == "rotate":
            a = math.radians(v[0])
            r = np.array([[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]])
            if len(v) >= 3:
                tr = np.array([[1, 0, v[1]], [0, 1, v[2]], [0, 0, 1]])
                itr = np.array([[1, 0, -v[1]], [0, 1, -v[2]], [0, 0, 1]])
                r = tr @ r @ itr
            t = r
        elif name == "skewX":
            t = np.array([[1, math.tan(math.radians(v[0])), 0], [0, 1, 0], [0, 0, 1]])
        elif name == "skewY":
            t = np.array([[1, 0, 0], [math.tan(math.radians(v[0])), 1, 0], [0, 0, 1]])
        else:
            continue
        m = m @ t
    return m


def translate(dx: float, dy: float) -> np.ndarray:
    return np.array([[1, 0, dx], [0, 1, dy], [0, 0, 1]], dtype=np.float64)


def scale(sx: float, sy: float | None = None, cx: float = 0.0, cy: float = 0.0) -> np.ndarray:
    sy = sx if sy is None else sy
    return translate(cx, cy) @ np.diag([sx, sy, 1.0]) @ translate(-cx, -cy)


def rotate(deg: float, cx: float = 0.0, cy: float = 0.0) -> np.ndarray:
    a = math.radians(deg)
    r = np.array([[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]])
    return translate(cx, cy) @ r @ translate(-cx, -cy)


# ======================================================================================
# document model
# ======================================================================================


@dataclass
class Shape:
    """A renderable element flattened to a path in document user space."""

    id: str
    tag: str
    geom: PathGeom
    fill: Color | Gradient | None = None
    fill_opacity: float = 1.0
    stroke: Color | Gradient | None = None
    stroke_width: float = 1.0
    stroke_opacity: float = 1.0
    stroke_linecap: str = "butt"
    stroke_linejoin: str = "miter"
    stroke_miterlimit: float = 4.0
    opacity: float = 1.0
    fill_rule: str = "nonzero"
    classes: list[str] = field(default_factory=list)
    index: int = 0

    # -- geometry ---------------------------------------------------------------------
    def skia_path(self) -> skia.Path:
        return self.geom.to_skia(self.fill_rule)

    def bbox(self) -> tuple[float, float, float, float]:
        """(x, y, w, h) exact fill bounds (used for objectBoundingBox gradients)."""
        x0, y0, x1, y1 = self.geom.bounds()
        return (x0, y0, x1 - x0, y1 - y0)

    def polygon(self, tol: float = 0.02) -> Polygon | MultiPolygon:
        return rings_to_polygon(self.geom.flatten(tol), self.fill_rule)

    def centroid(self) -> np.ndarray:
        c = self.polygon().centroid
        return np.array([c.x, c.y])

    def area(self) -> float:
        return float(self.polygon().area)

    # -- painting ---------------------------------------------------------------------
    def fill_paint(self, alpha: float = 1.0, matrix=None) -> skia.Paint | None:
        return _paint_for(self.fill, self.fill_opacity * self.opacity * alpha, self.bbox(), matrix)

    def stroke_paint(self, alpha: float = 1.0, matrix=None) -> skia.Paint | None:
        p = _paint_for(self.stroke, self.stroke_opacity * self.opacity * alpha, self.bbox(), matrix)
        if p is None or self.stroke_width <= 0:
            return None
        p.setStyle(skia.Paint.kStroke_Style)
        p.setStrokeWidth(self.stroke_width)
        p.setStrokeCap({"round": skia.Paint.kRound_Cap, "square": skia.Paint.kSquare_Cap}.get(self.stroke_linecap, skia.Paint.kButt_Cap))
        p.setStrokeJoin({"round": skia.Paint.kRound_Join, "bevel": skia.Paint.kBevel_Join}.get(self.stroke_linejoin, skia.Paint.kMiter_Join))
        p.setStrokeMiter(self.stroke_miterlimit)
        return p

    def draw(self, canvas: skia.Canvas, alpha: float = 1.0) -> None:
        """Draw with the element's own paint in the canvas's current (user-space) matrix."""
        path = self.skia_path()
        fp = self.fill_paint(alpha)
        if fp is not None:
            canvas.drawPath(path, fp)
        sp = self.stroke_paint(alpha)
        if sp is not None:
            canvas.drawPath(path, sp)

    def outline_path(self) -> skia.Path:
        """Filled region covered by this shape (fill ∪ stroke outline)."""
        path = self.skia_path()
        if self.stroke is not None and self.stroke_width > 0:
            sp = self.stroke_paint()
            out = skia.Path()
            sp.getFillPath(path, out)
            if self.fill is not None:
                return skia.Op(path, out, skia.PathOp.kUnion_PathOp)
            return out
        return path

    def primary_color(self) -> Color | None:
        f = self.fill if self.fill is not None else self.stroke
        if isinstance(f, Color):
            return f
        if isinstance(f, Gradient) and f.stops:
            return f.stops[0].color
        return None

    def to_dict(self) -> dict:
        x, y, w, h = self.bbox()
        d = {
            "id": self.id,
            "index": self.index,
            "tag": self.tag,
            "bbox": [round(x, 4), round(y, 4), round(w, 4), round(h, 4)],
            "area": round(self.area(), 4),
            "fill_rule": self.fill_rule,
            "opacity": self.opacity,
            "subpaths": len(self.geom.subpaths),
            "d": self.geom.to_d(3),
        }
        d["fill"] = _paint_desc(self.fill, self.fill_opacity)
        if self.stroke is not None:
            d["stroke"] = _paint_desc(self.stroke, self.stroke_opacity)
            d["stroke_width"] = self.stroke_width
        if self.classes:
            d["classes"] = self.classes
        return d


def _paint_desc(p, opacity):
    if p is None:
        return None
    if isinstance(p, Color):
        return {"type": "solid", "color": p.hex, "opacity": round(opacity * p.a, 4)}
    return {"type": "gradient", "ref": p.id, "opacity": round(opacity, 4)}


def _paint_for(fill, alpha: float, bbox, matrix=None) -> skia.Paint | None:
    if fill is None or alpha <= 0:
        return None
    p = skia.Paint(AntiAlias=True)
    if isinstance(fill, Color):
        p.setColor4f(skia.Color4f(fill.r, fill.g, fill.b, fill.a * alpha))
    else:
        p.setShader(fill.shader(bbox=bbox, matrix=matrix))
        p.setAlphaf(max(0.0, min(1.0, alpha)))
    return p


_INHERITED = {
    "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-opacity",
    "stroke-linecap", "stroke-linejoin", "stroke-miterlimit", "color", "display", "visibility",
}


def _parse_style(s: str | None) -> dict:
    out = {}
    for part in (s or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _parse_css(text: str) -> list[tuple[str, dict]]:
    """Minimal CSS for embedded <style>: simple selectors (.cls, #id, tag, tag.cls) only."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    rules = []
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", text):
        decl = _parse_style(body)
        for s in sel.split(","):
            s = s.strip()
            if s:
                rules.append((s, decl))
    return rules


def _css_matches(sel: str, tag: str, el_id: str, classes: list[str]) -> bool:
    m = re.fullmatch(r"([a-zA-Z][\w-]*)?((?:[.#][\w-]+)*)", sel)
    if not m:
        return False
    if m.group(1) and m.group(1) != tag:
        return False
    for kind, name in re.findall(r"([.#])([\w-]+)", m.group(2) or ""):
        if kind == "." and name not in classes:
            return False
        if kind == "#" and name != el_id:
            return False
    return bool(m.group(1) or m.group(2))


def _length(v, ref: float = 1.0, default: float = 0.0) -> float:
    if v is None:
        return default
    s = str(v).strip()
    if not s:
        return default
    if s.endswith("%"):
        return float(s[:-1]) / 100.0 * ref
    m = _NUM_RE.match(s)
    if not m:
        return default
    num = float(m.group(0))
    unit = s[m.end():].strip()
    return num * {"px": 1, "": 1, "pt": 4 / 3, "pc": 16, "mm": 96 / 25.4, "cm": 96 / 2.54, "in": 96}.get(unit, 1)


def _frac(v, default: float) -> float:
    """Gradient coordinate: '50%' → 0.5 (used for objectBoundingBox units)."""
    if v is None:
        return default
    s = str(v).strip()
    if s.endswith("%"):
        return float(s[:-1]) / 100.0
    return float(_NUM_RE.match(s).group(0))


class SVGDocument:
    """Parsed SVG with shapes in viewBox user space."""

    def __init__(self):
        self.width: float = 0
        self.height: float = 0
        self.view_box: tuple[float, float, float, float] = (0, 0, 0, 0)
        self.preserve_aspect_ratio: str = "xMidYMid meet"
        self.shapes: list[Shape] = []
        self.gradients: dict[str, Gradient] = {}
        self.warnings: list[str] = []
        self.source_path: str | None = None

    # -- loading ------------------------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path) -> "SVGDocument":
        doc = cls.parse(Path(path).read_text(encoding="utf-8", errors="replace"))
        doc.source_path = str(path)
        return doc

    @classmethod
    def parse(cls, text: str) -> "SVGDocument":
        doc = cls()
        root = ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
        if _local(root.tag) != "svg":
            raise ValueError("not an SVG document")
        vb = root.get("viewBox")
        w_attr, h_attr = root.get("width"), root.get("height")
        if vb:
            nums = [float(x) for x in _NUM_RE.findall(vb)]
            doc.view_box = tuple(nums[:4])  # type: ignore[assignment]
        doc.width = _length(w_attr, doc.view_box[2] or 100, default=doc.view_box[2] or 0) if w_attr and not w_attr.endswith("%") else (doc.view_box[2] or 0)
        doc.height = _length(h_attr, doc.view_box[3] or 100, default=doc.view_box[3] or 0) if h_attr and not h_attr.endswith("%") else (doc.view_box[3] or 0)
        if not vb:
            doc.view_box = (0, 0, doc.width or 100, doc.height or 100)
            doc.width = doc.width or 100
            doc.height = doc.height or 100
        doc.preserve_aspect_ratio = root.get("preserveAspectRatio", "xMidYMid meet")
        # index ids, css, gradients
        ids = {}
        css = []
        for el in root.iter():
            if el.get("id"):
                ids[el.get("id")] = el
            if _local(el.tag) == "style" and el.text:
                css.extend(_parse_css(el.text))
        doc._css = css
        doc._ids = ids
        for gid, el in ids.items():
            if _local(el.tag) in ("linearGradient", "radialGradient"):
                try:
                    doc.gradients[gid] = doc._parse_gradient(el)
                except Exception as e:  # pragma: no cover - defensive
                    doc.warnings.append(f"gradient {gid}: {e}")
        doc._walk(root, np.eye(3), {}, 1.0, in_defs=False)
        for i, s in enumerate(doc.shapes):
            s.index = i
            if not s.id:
                s.id = f"shape{i}"
        return doc

    def _resolved_attrs(self, el, inherited: dict) -> dict:
        tag = _local(el.tag)
        attrs = {k: v for k, v in inherited.items()}
        for k, v in el.attrib.items():
            k = _local(k)
            if k in _INHERITED or k in ("opacity",):
                attrs[k] = v
        classes = (el.get("class") or "").split()
        for sel, decl in self._css:
            if _css_matches(sel, tag, el.get("id", ""), classes):
                attrs.update(decl)
        attrs.update(_parse_style(el.get("style")))
        return attrs

    def _walk(self, el, m: np.ndarray, inherited: dict, opacity: float, in_defs: bool, depth: int = 0):
        if depth > 64:
            return
        tag = _local(el.tag)
        if tag in ("defs", "clipPath", "mask", "symbol", "marker", "pattern", "linearGradient", "radialGradient", "style", "metadata", "title", "desc", "script", "foreignObject"):
            if tag in ("clipPath", "mask") and not in_defs:
                self.warnings.append(f"<{tag}> is not rendered by luma_engine; check the analysis")
            return
        attrs = self._resolved_attrs(el, inherited)
        if attrs.get("display") == "none" or attrs.get("visibility") == "hidden":
            return
        m = m @ parse_transform(el.get("transform"))
        own_opacity = float(attrs.pop("opacity", 1.0) or 1.0)
        child_inherited = {k: v for k, v in attrs.items() if k in _INHERITED}
        if tag in ("svg", "g", "a"):
            if tag == "svg" and depth > 0:
                x, y = _length(el.get("x")), _length(el.get("y"))
                m = m @ translate(x, y)
            for ch in el:
                self._walk(ch, m, child_inherited, opacity * own_opacity, in_defs, depth + 1)
            return
        if tag == "use":
            href = el.get("href") or el.get(XLINK_HREF) or ""
            target = self._ids.get(href[1:]) if href.startswith("#") else None
            if target is not None:
                mm = m @ translate(_length(el.get("x")), _length(el.get("y")))
                self._walk(target, mm, child_inherited, opacity * own_opacity, in_defs, depth + 1)
            return
        geom = self._element_geom(el, tag)
        if geom is None or not geom.subpaths:
            return
        geom = geom.transformed(m)
        shape = Shape(id=el.get("id", ""), tag=tag, geom=geom, classes=(el.get("class") or "").split())
        shape.opacity = opacity * own_opacity
        fill = attrs.get("fill", "black")
        shape.fill = self._paint(fill, attrs)
        shape.fill_opacity = float(attrs.get("fill-opacity", 1) or 1)
        shape.fill_rule = attrs.get("fill-rule", "nonzero")
        shape.stroke = self._paint(attrs.get("stroke", "none"), attrs)
        shape.stroke_opacity = float(attrs.get("stroke-opacity", 1) or 1)
        det = abs(np.linalg.det(m[:2, :2])) ** 0.5
        shape.stroke_width = _length(attrs.get("stroke-width", 1), default=1.0) * det
        shape.stroke_linecap = attrs.get("stroke-linecap", "butt")
        shape.stroke_linejoin = attrs.get("stroke-linejoin", "miter")
        shape.stroke_miterlimit = float(attrs.get("stroke-miterlimit", 4) or 4)
        if tag in ("line", "polyline"):
            shape.fill = None if tag == "line" else shape.fill
        # gradients in userSpaceOnUse need the element's transform too
        for attr in ("fill", "stroke"):
            p = getattr(shape, attr)
            if isinstance(p, Gradient) and p.units == "userSpaceOnUse" and not np.allclose(m, np.eye(3)):
                g = Gradient(**{**p.__dict__})
                g.transform = m @ (p.transform if p.transform is not None else np.eye(3))
                setattr(shape, attr, g)
        self.shapes.append(shape)

    def _paint(self, value: str | None, attrs: dict):
        if value is None:
            return None
        v = value.strip()
        if v in ("none", "transparent", ""):
            return None
        if v == "currentColor":
            v = attrs.get("color", "black")
        m = re.match(r"url\(\s*['\"]?#([^'\")]+)['\"]?\s*\)\s*(.*)", v)
        if m:
            g = self.gradients.get(m.group(1))
            if g is not None:
                return g
            fb = m.group(2).strip()
            return Color.of(fb) if fb and fb != "none" else None
        try:
            return Color.of(v)
        except Exception:
            self.warnings.append(f"unknown paint {value!r}; using black")
            return Color.of("black")

    def _parse_gradient(self, el, depth: int = 0) -> Gradient:
        tag = _local(el.tag)
        href = el.get("href") or el.get(XLINK_HREF)
        base = None
        if href and href.startswith("#") and depth < 16 and href[1:] in self._ids:
            base = self._parse_gradient(self._ids[href[1:]], depth + 1)
        g = Gradient(kind="linear" if tag == "linearGradient" else "radial", id=el.get("id", ""))
        if base is not None:
            g = Gradient(**{**base.__dict__})
            g.kind = "linear" if tag == "linearGradient" else "radial"
            g.id = el.get("id", "")
        g.units = el.get("gradientUnits", g.units if base else "objectBoundingBox")
        if el.get("gradientTransform"):
            g.transform = parse_transform(el.get("gradientTransform"))
        g.spread = el.get("spreadMethod", g.spread)
        obb = g.units == "objectBoundingBox"
        vw, vh = self.view_box[2] or 1, self.view_box[3] or 1
        diag = math.hypot(vw, vh) / math.sqrt(2)

        def coord(name, default, ref):
            v = el.get(name)
            if v is None:
                return default
            return _frac(v, default) if obb else _length(v, ref)

        if g.kind == "linear":
            g.x1 = coord("x1", g.x1 if base else 0.0, vw)
            g.y1 = coord("y1", g.y1 if base else 0.0, vh)
            g.x2 = coord("x2", g.x2 if base else (1.0 if obb else vw), vw)
            g.y2 = coord("y2", g.y2 if base else 0.0, vh)
        else:
            g.cx = coord("cx", g.cx if base else (0.5 if obb else vw / 2), vw)
            g.cy = coord("cy", g.cy if base else (0.5 if obb else vh / 2), vh)
            g.r = coord("r", g.r if base else (0.5 if obb else diag / 2), diag)
            g.fx = coord("fx", g.fx if base else None, vw)
            g.fy = coord("fy", g.fy if base else None, vh)
        stops = []
        for st in el:
            if _local(st.tag) != "stop":
                continue
            sty = _parse_style(st.get("style"))
            off = st.get("offset", "0")
            off = float(off[:-1]) / 100 if off.strip().endswith("%") else float(off)
            col = sty.get("stop-color", st.get("stop-color", "black"))
            op = float(sty.get("stop-opacity", st.get("stop-opacity", 1)))
            r, gg, b, a = parse_color(col)
            stops.append(GradientStop(min(max(off, 0), 1), Color(r, gg, b, a * op)))
        if stops:
            g.stops = stops
        return g

    def _element_geom(self, el, tag: str) -> PathGeom | None:
        vw, vh = self.view_box[2] or 1, self.view_box[3] or 1
        if tag == "path":
            try:
                return parse_path_d(el.get("d", ""))
            except ValueError as e:
                self.warnings.append(f"path {el.get('id','')}: {e}")
                return None
        if tag == "rect":
            x, y = _length(el.get("x"), vw), _length(el.get("y"), vh)
            w, h = _length(el.get("width"), vw), _length(el.get("height"), vh)
            if w <= 0 or h <= 0:
                return None
            rx_a, ry_a = el.get("rx"), el.get("ry")
            rx = _length(rx_a, vw) if rx_a is not None else None
            ry = _length(ry_a, vh) if ry_a is not None else None
            if rx is None and ry is None:
                rx = ry = 0.0
            rx = ry if rx is None else rx
            ry = rx if ry is None else ry
            rx, ry = min(rx, w / 2), min(ry, h / 2)
            if rx <= 0 or ry <= 0:
                return parse_path_d(f"M{x} {y}H{x+w}V{y+h}H{x}Z")
            return parse_path_d(
                f"M{x+rx} {y}H{x+w-rx}A{rx} {ry} 0 0 1 {x+w} {y+ry}V{y+h-ry}A{rx} {ry} 0 0 1 {x+w-rx} {y+h}"
                f"H{x+rx}A{rx} {ry} 0 0 1 {x} {y+h-ry}V{y+ry}A{rx} {ry} 0 0 1 {x+rx} {y}Z"
            )
        if tag in ("circle", "ellipse"):
            cx, cy = _length(el.get("cx"), vw), _length(el.get("cy"), vh)
            if tag == "circle":
                rx = ry = _length(el.get("r"), math.hypot(vw, vh) / math.sqrt(2))
            else:
                rx, ry = _length(el.get("rx"), vw), _length(el.get("ry"), vh)
            if rx <= 0 or ry <= 0:
                return None
            return parse_path_d(
                f"M{cx+rx} {cy}A{rx} {ry} 0 0 1 {cx} {cy+ry}A{rx} {ry} 0 0 1 {cx-rx} {cy}"
                f"A{rx} {ry} 0 0 1 {cx} {cy-ry}A{rx} {ry} 0 0 1 {cx+rx} {cy}Z"
            )
        if tag in ("polygon", "polyline"):
            nums = [float(v) for v in _NUM_RE.findall(el.get("points", ""))]
            if len(nums) < 4:
                return None
            pts = list(zip(nums[0::2], nums[1::2]))
            d = f"M{pts[0][0]} {pts[0][1]}" + "".join(f"L{x} {y}" for x, y in pts[1:])
            return parse_path_d(d + ("Z" if tag == "polygon" else ""))
        if tag == "line":
            x1, y1, x2, y2 = (_length(el.get(k)) for k in ("x1", "y1", "x2", "y2"))
            return parse_path_d(f"M{x1} {y1}L{x2} {y2}")
        if tag == "text":
            self.warnings.append("<text> element found: convert text to outlines, or pass the font to luma_engine.text")
        return None

    # -- rendering ---------------------------------------------------------------------
    def viewbox_matrix(self, width: float, height: float, fit: str | None = None) -> np.ndarray:
        """user space → a ``width``×``height`` viewport (honours preserveAspectRatio)."""
        vx, vy, vw, vh = self.view_box
        par = (fit or self.preserve_aspect_ratio or "xMidYMid meet").split()
        align = par[0]
        slice_ = len(par) > 1 and par[1] == "slice"
        sx, sy = width / vw, height / vh
        if align != "none":
            s = max(sx, sy) if slice_ else min(sx, sy)
            sx = sy = s
        tx, ty = -vx * sx, -vy * sy
        if align != "none":
            ax = {"xMin": 0.0, "xMid": 0.5, "xMax": 1.0}.get(align[:4], 0.5)
            ay = {"YMin": 0.0, "YMid": 0.5, "YMax": 1.0}.get(align[4:], 0.5)
            tx += (width - vw * sx) * ax
            ty += (height - vh * sy) * ay
        return np.array([[sx, 0, tx], [0, sy, ty], [0, 0, 1]], dtype=np.float64)

    def draw(self, canvas: skia.Canvas, matrix: np.ndarray | None = None, alpha: float = 1.0, shapes: Iterable[Shape] | None = None):
        canvas.save()
        if matrix is not None:
            canvas.concat(skia_matrix(matrix))
        for s in shapes if shapes is not None else self.shapes:
            s.draw(canvas, alpha)
        canvas.restore()

    def render(self, width: int, height: int, background=None, matrix: np.ndarray | None = None) -> np.ndarray:
        """Rasterise to float32 (H, W, 4) premultiplied sRGB."""
        from .layers import read_surface

        surf = skia.Surface(int(width), int(height))
        c = surf.getCanvas()
        c.clear(Color.of(background).skia() if background is not None else skia.ColorTRANSPARENT)
        self.draw(c, self.viewbox_matrix(width, height) if matrix is None else matrix)
        return read_surface(surf)

    def bounds(self, shapes: Iterable[Shape] | None = None) -> tuple[float, float, float, float]:
        """Union (x0, y0, x1, y1) of shape bounds (incl. stroke outlines)."""
        xs0, ys0, xs1, ys1 = [], [], [], []
        for s in shapes if shapes is not None else self.shapes:
            r = s.outline_path().computeTightBounds()
            xs0.append(r.left())
            ys0.append(r.top())
            xs1.append(r.right())
            ys1.append(r.bottom())
        if not xs0:
            return (0, 0, 0, 0)
        return (min(xs0), min(ys0), max(xs1), max(ys1))

    def union_path(self, shapes: Iterable[Shape] | None = None) -> skia.Path:
        return union_skia([s.outline_path() for s in (shapes if shapes is not None else self.shapes)])

    def analysis(self) -> dict:
        shapes = [s.to_dict() for s in self.shapes]
        colors = []
        for s in self.shapes:
            for p in (s.fill, s.stroke):
                if isinstance(p, Color) and p.hex not in colors:
                    colors.append(p.hex)
                elif isinstance(p, Gradient):
                    for st in p.stops:
                        if st.color.hex not in colors:
                            colors.append(st.color.hex)
        x0, y0, x1, y1 = self.bounds()
        out = {
            "type": "svg",
            "width": self.width,
            "height": self.height,
            "viewBox": list(self.view_box),
            "preserveAspectRatio": self.preserve_aspect_ratio,
            "bounds": [round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4)],
            "shape_count": len(self.shapes),
            "shapes": shapes,
            "gradients": {k: g.to_dict() for k, g in self.gradients.items()},
            "colors": colors,
            "warnings": sorted(set(self.warnings)),
        }
        try:
            groups = split_symbol_wordmark(self.shapes)
            out["groups"] = {"symbol": [s.index for s in groups[0]], "wordmark": [s.index for s in groups[1]]}
            if len(groups[0]) >= 2:
                piv = detect_pivot([s.polygon() for s in groups[0]])
                out["pivot"] = [round(float(piv[0]), 3), round(float(piv[1]), 3)]
            sym = detect_symmetry(unary_union([s.polygon() for s in groups[0]]))
            out["symmetry"] = sym
        except Exception as e:  # pragma: no cover - analysis is best effort
            out["warnings"].append(f"structure analysis failed: {e}")
        return out


def _local(tag) -> str:
    return tag.split("}", 1)[1] if isinstance(tag, str) and "}" in tag else str(tag)


# ======================================================================================
# polygons, splitting, booleans
# ======================================================================================


def signed_area(ring: np.ndarray) -> float:
    x, y = ring[:, 0], ring[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def rings_to_polygon(rings: list[tuple[np.ndarray, bool]], fill_rule: str = "nonzero") -> Polygon | MultiPolygon:
    """Build a shapely region from flattened rings honouring the fill rule.

    evenodd → XOR of rings.  nonzero → union of rings wound like the dominant ring,
    minus rings wound the other way (exact for the nested/opposite-wound case that
    font and logo tools produce).
    """
    polys = []
    for ring, _closed in rings:
        if len(ring) < 3:
            continue
        p = Polygon(ring)
        if not p.is_valid:
            p = p.buffer(0)
        if p.is_empty:
            continue
        polys.append((p, signed_area(ring)))
    if not polys:
        return Polygon()
    if fill_rule == "evenodd":
        acc = polys[0][0]
        for p, _ in polys[1:]:
            acc = acc.symmetric_difference(p)
        return acc
    dominant = max(polys, key=lambda pa: abs(pa[1]))[1]
    pos = [p for p, a in polys if (a > 0) == (dominant > 0)]
    neg = [p for p, a in polys if (a > 0) != (dominant > 0)]
    region = unary_union(pos)
    if neg:
        # a counter-wound ring only punches a hole where it sits inside a positive ring
        region = region.difference(unary_union(neg))
    return region


def polygon_to_skia(poly) -> skia.Path:
    path = skia.Path()
    geoms = poly.geoms if hasattr(poly, "geoms") else [poly]
    for g in geoms:
        if g.is_empty or not isinstance(g, Polygon):
            continue
        for ring in [g.exterior, *g.interiors]:
            pts = np.asarray(ring.coords)
            path.moveTo(float(pts[0, 0]), float(pts[0, 1]))
            for x, y in pts[1:]:
                path.lineTo(float(x), float(y))
            path.close()
    path.setFillType(skia.PathFillType.kEvenOdd)
    return path


class SplitError(ValueError):
    pass


def _extend_cut(cut, bounds, margin=1.0) -> LineString:
    if isinstance(cut, LineString):
        pts = np.asarray(cut.coords)
        if len(pts) > 2:
            return cut
        a, b = pts[0], pts[-1]
    else:
        a, b = np.asarray(cut[0], dtype=float), np.asarray(cut[1], dtype=float)
    d = b - a
    n = np.linalg.norm(d)
    if n == 0:
        raise SplitError("degenerate cut")
    d /= n
    x0, y0, x1, y1 = bounds
    L = math.hypot(x1 - x0, y1 - y0) * 2 + margin
    return LineString([a - d * L, b + d * L])


def split_polygon(poly, cuts: Sequence, extend: bool = True, tol: float = 1e-9) -> list[Polygon]:
    """Split ``poly`` along cut lines/segments. Raises :class:`SplitError` if the pieces'
    total area differs from the original by more than ``tol`` (relative).

    ``cuts`` are ``((x0, y0), (x1, y1))`` segments (extended across the shape when
    ``extend``) or shapely LineStrings (used as-is when they have > 2 points).
    """
    if poly.is_empty:
        return []
    pieces = list(poly.geoms) if hasattr(poly, "geoms") else [poly]
    for cut in cuts:
        line = _extend_cut(cut, poly.bounds) if extend else (cut if isinstance(cut, LineString) else LineString(cut))
        nxt = []
        for p in pieces:
            res = shp_split(p, line)
            nxt.extend(g for g in res.geoms if isinstance(g, Polygon) and not g.is_empty)
        pieces = nxt
    total = sum(p.area for p in pieces)
    err = abs(total - poly.area) / max(poly.area, 1e-300)
    if err > tol:
        raise SplitError(f"split area error {err:.3e} exceeds {tol:.0e}")
    pieces.sort(key=lambda p: (round(p.centroid.y, 6), round(p.centroid.x, 6)))
    return pieces


def split_radial(poly, center, angles_deg: Sequence[float], tol: float = 1e-9) -> list[Polygon]:
    """Cut into wedges around ``center`` with rays at ``angles_deg`` (0° = +x, clockwise on screen)."""
    c = np.asarray(center, dtype=float)
    x0, y0, x1, y1 = poly.bounds
    R = math.hypot(x1 - x0, y1 - y0) * 2 + 10
    angs = sorted(a % 360 for a in angles_deg)
    if len(angs) < 2:
        raise SplitError("need at least two ray angles")
    pieces = []
    for i, a0 in enumerate(angs):
        a1 = angs[(i + 1) % len(angs)]
        if a1 <= a0:
            a1 += 360
        steps = max(2, int((a1 - a0) / 5) + 1)
        arc = [c + R * np.array([math.cos(math.radians(a)), math.sin(math.radians(a))]) for a in np.linspace(a0, a1, steps)]
        wedge = Polygon([tuple(c), *map(tuple, arc)])
        part = poly.intersection(wedge)
        for g in getattr(part, "geoms", [part]):
            if isinstance(g, Polygon) and not g.is_empty and g.area > 0:
                pieces.append(g)
    total = sum(p.area for p in pieces)
    err = abs(total - poly.area) / max(poly.area, 1e-300)
    if err > tol:
        raise SplitError(f"radial split area error {err:.3e} exceeds {tol:.0e}")
    return pieces


def union_polygons(polys: Iterable) -> Polygon | MultiPolygon:
    return unary_union(list(polys))


def union_skia(paths: Iterable[skia.Path]) -> skia.Path:
    """Exact (curve-preserving) boolean union with skia PathOps."""
    paths = list(paths)
    if not paths:
        return skia.Path()
    b = skia.OpBuilder()
    for p in paths:
        b.add(p, skia.PathOp.kUnion_PathOp)
    out = b.resolve()
    return out if out is not None else paths[0]


# ======================================================================================
# structure detection
# ======================================================================================


def detect_notches(poly: Polygon, min_turn_deg: float = 25.0, tol: float = 1e-6) -> list[dict]:
    """Reflex (concave) vertices of the exterior, strongest first.

    Returns ``[{point: [x, y], turn_deg}]``.
    """
    if isinstance(poly, MultiPolygon):
        out = []
        for g in poly.geoms:
            out.extend(detect_notches(g, min_turn_deg, tol))
        return sorted(out, key=lambda d: -d["turn_deg"])
    ring = np.asarray(poly.exterior.coords)[:-1]
    if len(ring) < 4:
        return []
    ccw = signed_area(ring) > 0
    out = []
    n = len(ring)
    for i in range(n):
        a, b, c = ring[i - 1], ring[i], ring[(i + 1) % n]
        v1, v2 = b - a, c - b
        if np.linalg.norm(v1) < tol or np.linalg.norm(v2) < tol:
            continue
        cross = v1[0] * v2[1] - v1[1] * v2[0]
        turn = math.degrees(math.atan2(cross, float(v1 @ v2)))
        reflex = turn < 0 if ccw else turn > 0
        if reflex and abs(turn) >= min_turn_deg:
            out.append({"point": [float(b[0]), float(b[1])], "turn_deg": abs(turn)})
    return sorted(out, key=lambda d: -d["turn_deg"])


def _ring_moments(xy: np.ndarray) -> np.ndarray:
    """[A, Sx, Sy, Sxx, Syy, Sxy] of a closed ring via Green's theorem (signed)."""
    x, y = xy[:-1, 0], xy[:-1, 1]
    x1, y1 = xy[1:, 0], xy[1:, 1]
    cr = x * y1 - x1 * y
    A = cr.sum() / 2
    Sx = ((x + x1) * cr).sum() / 6
    Sy = ((y + y1) * cr).sum() / 6
    Sxx = ((x * x + x * x1 + x1 * x1) * cr).sum() / 12
    Syy = ((y * y + y * y1 + y1 * y1) * cr).sum() / 12
    Sxy = ((x * y1 + 2 * x * y + 2 * x1 * y1 + x1 * y) * cr).sum() / 24
    return np.array([A, Sx, Sy, Sxx, Syy, Sxy])


def principal_axis(poly) -> tuple[np.ndarray, np.ndarray, float]:
    """(centroid, unit major-axis direction, elongation ratio) from the exact area
    second moments of the (flattened) polygon, holes included."""
    from shapely.geometry.polygon import orient

    geoms = poly.geoms if hasattr(poly, "geoms") else [poly]
    m = np.zeros(6)
    for g in geoms:
        if not isinstance(g, Polygon) or g.is_empty:
            continue
        g = orient(g, 1.0)  # exterior CCW (+), holes CW (−)
        m += _ring_moments(np.asarray(g.exterior.coords))
        for r in g.interiors:
            m += _ring_moments(np.asarray(r.coords))
    A, Sx, Sy, Sxx, Syy, Sxy = m
    c = np.array([Sx / A, Sy / A])
    cxx = Sxx / A - c[0] ** 2
    cyy = Syy / A - c[1] ** 2
    cxy = Sxy / A - c[0] * c[1]
    w, v = np.linalg.eigh(np.array([[cxx, cxy], [cxy, cyy]]))
    d = v[:, 1]
    ratio = float(np.sqrt(max(w[1], 1e-18) / max(w[0], 1e-18)))
    return c, d / np.linalg.norm(d), ratio


def detect_pivot(parts: Sequence, iters: int = 200) -> np.ndarray:
    """Hinge point where several parts converge (e.g. the rivet of a fan).

    1. Elongated parts: least-squares intersection of their principal axes (exact for
       fan blades / rays / petals).  A small compact part (hub, rivet) at that point
       snaps the pivot to its centroid.
    2. Otherwise: fixed-point iteration minimising squared distance to every part.
    3. A single part falls back to its deepest notch, then its centroid.
    """
    parts = [p for p in parts if not p.is_empty]
    if not parts:
        raise ValueError("no parts")
    if len(parts) == 1:
        notches = detect_notches(parts[0])
        if notches:
            return np.array(notches[0]["point"])
        c = parts[0].centroid
        return np.array([c.x, c.y])
    u = unary_union(parts)
    x0, y0, x1, y1 = u.bounds
    size = max(x1 - x0, y1 - y0, 1e-9)
    axes = [principal_axis(p) for p in parts]
    elong = [(c, d) for (c, d, r) in axes if r > 1.8]
    if len(elong) >= 2:
        A = np.zeros((2, 2))
        b = np.zeros(2)
        for c, d in elong:
            P = np.eye(2) - np.outer(d, d)
            A += P
            b += P @ c
        if abs(np.linalg.det(A)) > 1e-9:
            p = np.linalg.solve(A, b)
            resid = np.sqrt(np.mean([float(np.sum(((np.eye(2) - np.outer(d, d)) @ (p - c)) ** 2)) for c, d in elong]))
            inside_range = (x0 - size) <= p[0] <= (x1 + size) and (y0 - size) <= p[1] <= (y1 + size)
            if resid < 0.08 * size and inside_range:
                areas = [q.area for q in parts]
                for q, (c, d, r) in zip(parts, axes):
                    if r <= 1.8 and q.area < 0.25 * max(areas) and q.distance(Point(*p)) < 0.05 * size:
                        return np.array([q.centroid.x, q.centroid.y])
                return p
    p = np.array([u.centroid.x, u.centroid.y])
    for _ in range(iters):
        pt = Point(*p)
        nearest = []
        for part in parts:
            if part.contains(pt):
                nearest.append(p.copy())
            else:
                from shapely.ops import nearest_points

                q = nearest_points(part, pt)[0]
                nearest.append(np.array([q.x, q.y]))
        newp = np.mean(nearest, axis=0)
        if np.linalg.norm(newp - p) < 1e-9:
            break
        p = newp
    return p


def detect_symmetry(poly, step_deg: float = 1.0) -> dict:
    """Best mirror axis through the centroid; ``score`` is IoU with the mirrored shape."""
    if poly.is_empty:
        return {"score": 0.0}
    c = poly.centroid
    best = (0.0, 0.0)
    for a in np.arange(0.0, 180.0, step_deg):
        # reflect across line through c at angle a
        r = affinity.rotate(poly, -a, origin=c)
        r = affinity.scale(r, xfact=1.0, yfact=-1.0, origin=c)
        r = affinity.rotate(r, a, origin=c)
        inter = poly.intersection(r).area
        uni = poly.union(r).area or 1
        iou = inter / uni
        if iou > best[0]:
            best = (iou, float(a))
    return {"score": round(best[0], 5), "axis_deg": best[1], "center": [round(c.x, 4), round(c.y, 4)], "symmetric": best[0] > 0.97}


def part_angle(part, pivot) -> float:
    """Direction (degrees, screen coords: 0 = +x, 90 = +y/down) from pivot to part centroid."""
    c = part.centroid
    return math.degrees(math.atan2(c.y - pivot[1], c.x - pivot[0]))


def split_symbol_wordmark(shapes: Sequence[Shape], gap_ratio: float = 0.12) -> tuple[list[Shape], list[Shape]]:
    """Heuristic: in a horizontal lockup the symbol is the left cluster separated by the
    largest horizontal gap (≥ ``gap_ratio`` × height).  Returns (symbol, wordmark)."""
    if len(shapes) <= 1:
        return list(shapes), []
    boxes = []
    for s in shapes:
        x, y, w, h = s.bbox()
        boxes.append((x, x + w, y, y + h, s))
    boxes.sort(key=lambda b: b[0])
    H = max(b[3] for b in boxes) - min(b[2] for b in boxes)
    best_gap, best_i = 0.0, -1
    reach = boxes[0][1]
    for i in range(1, len(boxes)):
        gap = boxes[i][0] - reach
        if gap > best_gap:
            best_gap, best_i = gap, i
        reach = max(reach, boxes[i][1])
    if best_i > 0 and best_gap >= gap_ratio * H:
        return [b[4] for b in boxes[:best_i]], [b[4] for b in boxes[best_i:]]
    return list(shapes), []


def sanitize_svg(text: str) -> str:
    """Remove scripts, event handlers, foreignObject and external references.

    Raises ValueError on DTD/entity tricks (defusedxml) or non-SVG roots.
    """
    root = ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
    if _local(root.tag) != "svg":
        raise ValueError("root element is not <svg>")
    bad_tags = {"script", "foreignObject", "iframe", "embed", "object", "audio", "video", "handler", "listener"}

    def clean(el):
        for ch in list(el):
            if _local(ch.tag) in bad_tags:
                el.remove(ch)
            else:
                clean(ch)
        for k in list(el.attrib):
            lk = _local(k).lower()
            v = el.attrib[k].strip()
            vl = v.lower().replace(" ", "")
            if lk.startswith("on"):
                del el.attrib[k]
            elif lk in ("href",) and not (vl.startswith("#") or vl.startswith("data:image/png") or vl.startswith("data:image/jpeg") or vl.startswith("data:image/webp")):
                del el.attrib[k]
            elif "javascript:" in vl or "url(http" in vl or "url(//" in vl or "url('http" in vl or 'url("http' in vl or "@import" in vl:
                del el.attrib[k]
        if _local(el.tag) == "style" and el.text:
            t = el.text
            t = re.sub(r"@import[^;]*;?", "", t, flags=re.I)
            t = re.sub(r"url\(\s*['\"]?(?:https?:|//|javascript:)[^)]*\)", "none", t, flags=re.I)
            el.text = t

    clean(root)
    import xml.etree.ElementTree as StdET

    StdET.register_namespace("", "http://www.w3.org/2000/svg")
    StdET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
    return StdET.tostring(root, encoding="unicode")


__all__ = [
    "PathGeom",
    "SVGDocument",
    "Shape",
    "SplitError",
    "Subpath",
    "arc_to_cubics",
    "detect_notches",
    "detect_pivot",
    "detect_symmetry",
    "parse_path_d",
    "parse_transform",
    "part_angle",
    "principal_axis",
    "polygon_to_skia",
    "rings_to_polygon",
    "rotate",
    "sanitize_svg",
    "scale",
    "split_polygon",
    "split_radial",
    "split_symbol_wordmark",
    "translate",
    "union_polygons",
    "union_skia",
]
