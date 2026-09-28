"""Brand colour tokens, colour-space conversion and gradients.

Conventions
-----------
* Colours are stored as **sRGB-encoded floats in [0, 1]** (what designers type as hex).
* Light maths happens in **linear** light: use :func:`srgb_to_linear` / :func:`linear_to_srgb`.
* Gradients interpolate in **sRGB** (matching SVG renderers, Figma, Illustrator).
* skia gradient shaders quantise stop colours to 8 bit, so they can never carry HDR (>1)
  values.  Use :meth:`Gradient.render` (float LUT) when you need precision or HDR gain.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np
import skia
from PIL import ImageColor

# --------------------------------------------------------------------------------------
# transfer functions
# --------------------------------------------------------------------------------------


def srgb_to_linear(x):
    """IEC 61966-2-1 sRGB EOTF (works on floats or numpy arrays)."""
    if np.isscalar(x):
        x = float(x)
        return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4
    x = np.asarray(x, dtype=np.float32)
    hi = _pow((np.maximum(x, np.float32(0.04045)) + np.float32(0.055)) * np.float32(1 / 1.055), 2.4)
    return np.where(x <= np.float32(0.04045), x * np.float32(1 / 12.92), hi).astype(np.float32, copy=False)


def linear_to_srgb(x):
    """Inverse sRGB transfer. Values above 1 are extrapolated (clip afterwards)."""
    if np.isscalar(x):
        x = float(x)
        return x * 12.92 if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055
    x = np.asarray(x, dtype=np.float32)
    hi = np.float32(1.055) * _pow(np.maximum(x, np.float32(0.0031308)), 1 / 2.4) - np.float32(0.055)
    return np.where(x <= np.float32(0.0031308), x * np.float32(12.92), hi).astype(np.float32, copy=False)


def _pow(x: np.ndarray, e: float) -> np.ndarray:
    """Fast float32 power (OpenCV SIMD) for non-negative inputs."""
    import cv2

    x = np.ascontiguousarray(x, dtype=np.float32)
    if x.ndim <= 3 and x.size > 4096:
        shp = x.shape
        return cv2.pow(x.reshape(shp[0], -1) if x.ndim > 1 else x.reshape(1, -1), e).reshape(shp)
    return np.power(x, np.float32(e))


# --------------------------------------------------------------------------------------
# colours
# --------------------------------------------------------------------------------------


def parse_color(value) -> tuple[float, float, float, float]:
    """Parse ``#rgb``, ``#rrggbb``, ``#rrggbbaa``, ``rgb()``/``rgba()``, CSS names or tuples.

    Returns sRGB floats (r, g, b, a) in [0, 1].
    """
    if isinstance(value, Color):
        return value.rgba
    if isinstance(value, (tuple, list, np.ndarray)):
        vals = [float(v) for v in value]
        if max(vals[:3]) > 1.0:
            vals = [v / 255.0 for v in vals[:3]] + ([vals[3]] if len(vals) > 3 else [])
        if len(vals) == 3:
            vals.append(1.0)
        return tuple(vals)  # type: ignore[return-value]
    s = str(value).strip().lower()
    if s in ("none", "transparent", ""):
        return (0.0, 0.0, 0.0, 0.0)
    if s.startswith("#"):
        h = s[1:]
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        if len(h) not in (6, 8):
            raise ValueError(f"bad hex colour {value!r}")
        r, g, b = (int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
        a = int(h[6:8], 16) / 255.0 if len(h) == 8 else 1.0
        return (r, g, b, a)
    if s.startswith("rgb"):
        inner = s[s.index("(") + 1 : s.rindex(")")].replace("/", ",")
        parts = [p.strip() for p in inner.replace(" ", ",").split(",") if p.strip()]
        rgb = []
        for p in parts[:3]:
            rgb.append(float(p[:-1]) / 100.0 if p.endswith("%") else float(p) / 255.0)
        a = 1.0
        if len(parts) > 3:
            p = parts[3]
            a = float(p[:-1]) / 100.0 if p.endswith("%") else float(p)
        return (rgb[0], rgb[1], rgb[2], a)
    r, g, b = ImageColor.getrgb(s)[:3]
    return (r / 255.0, g / 255.0, b / 255.0, 1.0)


@dataclass(frozen=True)
class Color:
    """An sRGB colour token."""

    r: float
    g: float
    b: float
    a: float = 1.0
    name: str = ""

    @classmethod
    def of(cls, value, name: str = "") -> "Color":
        if isinstance(value, Color):
            return value
        r, g, b, a = parse_color(value)
        return cls(r, g, b, a, name)

    @property
    def rgba(self) -> tuple[float, float, float, float]:
        return (self.r, self.g, self.b, self.a)

    @property
    def rgb(self) -> np.ndarray:
        return np.array([self.r, self.g, self.b], dtype=np.float32)

    @property
    def linear(self) -> np.ndarray:
        return srgb_to_linear(self.rgb)

    @property
    def hex(self) -> str:
        h = "#" + "".join(f"{round(c * 255):02X}" for c in (self.r, self.g, self.b))
        if self.a < 1.0:
            h += f"{round(self.a * 255):02X}"
        return h

    def skia4f(self, alpha: float | None = None) -> skia.Color4f:
        return skia.Color4f(self.r, self.g, self.b, self.a if alpha is None else alpha)

    def skia(self, alpha: float | None = None) -> int:
        return self.skia4f(alpha).toColor()

    def with_alpha(self, a: float) -> "Color":
        return Color(self.r, self.g, self.b, a, self.name)

    def mix(self, other: "Color", t: float) -> "Color":
        o = Color.of(other)
        return Color(*(a + (b - a) * t for a, b in zip(self.rgba, o.rgba)), name=self.name)

    def lab(self) -> np.ndarray:
        return rgb_to_lab(self.rgb)

    def delta_e(self, other) -> float:
        return float(np.linalg.norm(self.lab() - Color.of(other).lab()))

    def luminance(self) -> float:
        lin = self.linear
        return float(0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2])

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.hex


def rgb_to_lab(rgb) -> np.ndarray:
    """sRGB (0..1) → CIE L*a*b* (D65)."""
    lin = srgb_to_linear(np.asarray(rgb, dtype=np.float32))
    m = np.array(
        [[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041]],
        dtype=np.float64,
    )
    xyz = lin.astype(np.float64) @ m.T
    xyz = xyz / np.array([0.95047, 1.0, 1.08883])
    eps = 216 / 24389
    k = 24389 / 27
    f = np.where(xyz > eps, np.cbrt(xyz), (k * xyz + 16) / 116)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def contrast_ratio(c1, c2) -> float:
    l1, l2 = sorted([Color.of(c1).luminance(), Color.of(c2).luminance()], reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


@dataclass
class BrandKit:
    """The brand's single source of truth: named colour tokens, fonts, colours to avoid."""

    colors: dict[str, Color] = field(default_factory=dict)
    fonts: list[str] = field(default_factory=list)
    avoid: list[Color] = field(default_factory=list)
    background: Color | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "BrandKit":
        colors = {k: Color.of(v, k) for k, v in (d.get("colors") or {}).items()}
        bg = d.get("background")
        return cls(
            colors=colors,
            fonts=list(d.get("fonts") or []),
            avoid=[Color.of(c) for c in d.get("avoid") or []],
            background=Color.of(bg) if bg else None,
        )

    def to_dict(self) -> dict:
        return {
            "colors": {k: v.hex for k, v in self.colors.items()},
            "fonts": self.fonts,
            "avoid": [c.hex for c in self.avoid],
            "background": self.background.hex if self.background else None,
        }

    def violates_avoid(self, color, threshold: float = 12.0) -> Color | None:
        """Return the avoided colour that ``color`` is perceptually close to (ΔE76), if any."""
        c = Color.of(color)
        for a in self.avoid:
            if c.delta_e(a) < threshold:
                return a
        return None

    def closest(self, color) -> tuple[str, float]:
        c = Color.of(color)
        best = min(self.colors.items(), key=lambda kv: c.delta_e(kv[1]))
        return best[0], c.delta_e(best[1])


# --------------------------------------------------------------------------------------
# gradients
# --------------------------------------------------------------------------------------


@dataclass
class GradientStop:
    offset: float
    color: Color


def _mat3(m) -> np.ndarray:
    return np.eye(3) if m is None else np.asarray(m, dtype=np.float64)


def skia_matrix(m: np.ndarray) -> skia.Matrix:
    m = np.asarray(m, dtype=np.float64)
    return skia.Matrix.MakeAll(
        float(m[0, 0]), float(m[0, 1]), float(m[0, 2]),
        float(m[1, 0]), float(m[1, 1]), float(m[1, 2]),
        float(m[2, 0]), float(m[2, 1]), float(m[2, 2]),
    )


@dataclass
class Gradient:
    """SVG-style linear or radial gradient.

    Geometry is in *gradient space*: the unit bounding box for ``objectBoundingBox``
    units or user space for ``userSpaceOnUse``; ``transform`` is ``gradientTransform``.
    """

    kind: str = "linear"  # "linear" | "radial"
    stops: list[GradientStop] = field(default_factory=list)
    units: str = "objectBoundingBox"
    x1: float = 0.0
    y1: float = 0.0
    x2: float = 1.0
    y2: float = 0.0
    cx: float = 0.5
    cy: float = 0.5
    r: float = 0.5
    fx: float | None = None
    fy: float | None = None
    transform: np.ndarray | None = None
    spread: str = "pad"
    id: str = ""

    @classmethod
    def linear(cls, stops: Sequence, p0=(0.0, 0.0), p1=(1.0, 0.0), units="objectBoundingBox", **kw) -> "Gradient":
        return cls("linear", _stops(stops), units, p0[0], p0[1], p1[0], p1[1], **kw)

    @classmethod
    def radial(cls, stops: Sequence, center=(0.5, 0.5), r=0.5, focal=None, units="objectBoundingBox", **kw) -> "Gradient":
        g = cls("radial", _stops(stops), units, cx=center[0], cy=center[1], r=r, **kw)
        if focal is not None:
            g.fx, g.fy = focal
        return g

    # -- colour lookup ---------------------------------------------------------------
    def lut(self, n: int = 1024) -> np.ndarray:
        """(n, 4) sRGB float LUT interpolated in sRGB, unpremultiplied (like SVG)."""
        stops = sorted(self.stops, key=lambda s: s.offset)
        offs = np.array([min(max(s.offset, 0.0), 1.0) for s in stops], dtype=np.float64)
        offs = np.maximum.accumulate(offs)  # SVG: offsets clamp to previous
        cols = np.array([s.color.rgba for s in stops], dtype=np.float64)
        t = np.linspace(0.0, 1.0, n)
        out = np.empty((n, 4), dtype=np.float32)
        for c in range(4):
            out[:, c] = _interp_stops(t, offs, cols[:, c])
        return out

    def color_at(self, t: float) -> np.ndarray:
        stops = sorted(self.stops, key=lambda s: s.offset)
        offs = np.maximum.accumulate(np.array([min(max(s.offset, 0), 1) for s in stops]))
        cols = np.array([s.color.rgba for s in stops])
        t = _spread(np.array([t]), self.spread)
        return np.array([_interp_stops(t, offs, cols[:, c])[0] for c in range(4)], dtype=np.float32)

    # -- geometry ---------------------------------------------------------------------
    def space_matrix(self, bbox: tuple[float, float, float, float] | None) -> np.ndarray:
        """Matrix mapping gradient space → user space (bbox = x, y, w, h)."""
        m = _mat3(self.transform)
        if self.units == "objectBoundingBox":
            if bbox is None:
                raise ValueError("objectBoundingBox gradient needs a bbox")
            x, y, w, h = bbox
            obb = np.array([[w, 0, x], [0, h, y], [0, 0, 1]], dtype=np.float64)
            return obb @ m
        return m

    def param(self, pts: np.ndarray) -> np.ndarray:
        """Gradient parameter t for points given in gradient space (before spread)."""
        if self.kind == "linear":
            d = np.array([self.x2 - self.x1, self.y2 - self.y1])
            dd = float(d @ d) or 1e-12
            return ((pts[:, 0] - self.x1) * d[0] + (pts[:, 1] - self.y1) * d[1]) / dd
        fx = self.cx if self.fx is None else self.fx
        fy = self.cy if self.fy is None else self.fy
        r = max(self.r, 1e-12)
        px = pts[:, 0] - fx
        py = pts[:, 1] - fy
        dx, dy = self.cx - fx, self.cy - fy
        if abs(dx) < 1e-12 and abs(dy) < 1e-12:
            return np.sqrt(px * px + py * py) / r
        a = dx * dx + dy * dy - r * r
        B = px * dx + py * dy
        C = px * px + py * py
        if abs(a) < 1e-12:
            return C / np.maximum(2 * B, 1e-12)
        disc = np.maximum(B * B - a * C, 0.0)
        return (B - np.sqrt(disc)) / a

    def render(self, width: int, height: int, bbox=None, matrix=None, lut_size: int = 4096) -> np.ndarray:
        """Float32 (H, W, 4) sRGB, unpremultiplied, sampled at pixel centres.

        ``matrix`` maps user space → pixel space (e.g. the SVG viewBox transform).
        """
        M = _mat3(matrix) @ self.space_matrix(bbox)
        inv = np.linalg.inv(M)
        ys, xs = np.mgrid[0:height, 0:width].astype(np.float64)
        pts = np.stack([xs.ravel() + 0.5, ys.ravel() + 0.5, np.ones(xs.size)], axis=0)
        g = inv @ pts
        g = (g[:2] / g[2:3]).T
        t = _spread(self.param(g), self.spread)
        lut = self.lut(lut_size)
        idx = np.clip(np.rint(t * (lut_size - 1)).astype(np.int64), 0, lut_size - 1)
        return lut[idx].reshape(height, width, 4)

    def shader(self, bbox=None, matrix=None) -> skia.Shader:
        """skia shader (8-bit stops — see module docstring)."""
        stops = sorted(self.stops, key=lambda s: s.offset)
        offs = list(np.maximum.accumulate([min(max(s.offset, 0.0), 1.0) for s in stops]))
        colors = [s.color.skia() for s in stops]
        if len(colors) == 1:
            return skia.Shaders.Color(colors[0])
        mode = {"pad": skia.TileMode.kClamp, "reflect": skia.TileMode.kMirror, "repeat": skia.TileMode.kRepeat}[self.spread]
        local = skia_matrix(_mat3(matrix) @ self.space_matrix(bbox))
        if self.kind == "linear":
            return skia.GradientShader.MakeLinear(
                [skia.Point(self.x1, self.y1), skia.Point(self.x2, self.y2)], colors, offs, mode, 0, local
            )
        fx = self.cx if self.fx is None else self.fx
        fy = self.cy if self.fy is None else self.fy
        if abs(fx - self.cx) < 1e-12 and abs(fy - self.cy) < 1e-12:
            return skia.GradientShader.MakeRadial(skia.Point(self.cx, self.cy), self.r, colors, offs, mode, 0, local)
        return skia.GradientShader.MakeTwoPointConical(
            skia.Point(fx, fy), 0.0, skia.Point(self.cx, self.cy), self.r, colors, offs, mode, 0, local
        )

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "kind": self.kind,
            "units": self.units,
            "spread": self.spread,
            "stops": [{"offset": round(s.offset, 6), "color": s.color.hex, "opacity": round(s.color.a, 4)} for s in self.stops],
        }
        if self.kind == "linear":
            d.update(x1=self.x1, y1=self.y1, x2=self.x2, y2=self.y2)
        else:
            d.update(cx=self.cx, cy=self.cy, r=self.r, fx=self.fx, fy=self.fy)
        if self.transform is not None:
            d["transform"] = np.asarray(self.transform).round(6).tolist()
        return d


def _stops(stops: Iterable) -> list[GradientStop]:
    out = []
    items = list(stops)
    for i, s in enumerate(items):
        if isinstance(s, GradientStop):
            out.append(s)
        elif isinstance(s, (tuple, list)) and len(s) == 2 and not isinstance(s[0], str):
            out.append(GradientStop(float(s[0]), Color.of(s[1])))
        else:
            off = i / max(len(items) - 1, 1)
            out.append(GradientStop(off, Color.of(s)))
    return out


def _spread(t: np.ndarray, spread: str) -> np.ndarray:
    if spread == "repeat":
        return t - np.floor(t)
    if spread == "reflect":
        m = np.mod(t, 2.0)
        return np.where(m > 1.0, 2.0 - m, m)
    return np.clip(t, 0.0, 1.0)


def _interp_stops(t: np.ndarray, offs: np.ndarray, vals: np.ndarray) -> np.ndarray:
    """Piecewise-linear interpolation that honours coincident stops (hard edges)."""
    if len(offs) == 1:
        return np.full_like(t, vals[0], dtype=np.float64)
    out = np.empty_like(t, dtype=np.float64)
    out[t <= offs[0]] = vals[0]
    out[t >= offs[-1]] = vals[-1]
    for i in range(len(offs) - 1):
        a, b = offs[i], offs[i + 1]
        if b <= a:
            continue
        m = (t > a) & (t < b)
        u = (t[m] - a) / (b - a)
        out[m] = vals[i] + (vals[i + 1] - vals[i]) * u
        out[t == b] = vals[i + 1]
    return out


def dominant_palette(rgb: np.ndarray, k: int = 6, alpha: np.ndarray | None = None, max_samples: int = 40000) -> list[dict]:
    """k-means palette of an sRGB float image. Returns [{hex, fraction}] sorted by share."""
    import cv2

    px = rgb.reshape(-1, 3).astype(np.float32)
    if alpha is not None:
        px = px[alpha.reshape(-1) > 0.5]
    if len(px) == 0:
        return []
    if len(px) > max_samples:
        rng = np.random.default_rng(0)
        px = px[rng.choice(len(px), max_samples, replace=False)]
    k = int(min(k, len(np.unique(np.round(px * 255), axis=0))))
    if k <= 0:
        return []
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1e-3)
    cv2.setRNGSeed(0)
    _, labels, centers = cv2.kmeans(px, k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    counts = np.bincount(labels.ravel(), minlength=k)
    order = np.argsort(-counts)
    return [
        {"hex": Color(*np.clip(centers[i], 0, 1)).hex, "fraction": round(float(counts[i]) / len(px), 4)}
        for i in order
        if counts[i] > 0
    ]


def hex_codes_in_text(text: str) -> list[str]:
    import re

    found = []
    for m in re.finditer(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b", text):
        h = Color.of(m.group(0)).hex
        if h not in found:
            found.append(h)
    return found


__all__ = [
    "BrandKit",
    "Color",
    "Gradient",
    "GradientStop",
    "contrast_ratio",
    "dominant_palette",
    "hex_codes_in_text",
    "linear_to_srgb",
    "parse_color",
    "rgb_to_lab",
    "skia_matrix",
    "srgb_to_linear",
]

