"""Layout: lockup builder, least-squares fitting to a reference image, safe areas."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import optimize

from .svg import scale as m_scale
from .svg import translate as m_translate


@dataclass
class Box:
    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def x1(self) -> float:
        return self.x + self.w

    @property
    def y1(self) -> float:
        return self.y + self.h

    def inset(self, fx: float, fy: float | None = None) -> "Box":
        fy = fx if fy is None else fy
        return Box(self.x + self.w * fx, self.y + self.h * fy, self.w * (1 - 2 * fx), self.h * (1 - 2 * fy))

    def contains(self, other: "Box", eps: float = 1e-6) -> bool:
        return other.x >= self.x - eps and other.y >= self.y - eps and other.x1 <= self.x1 + eps and other.y1 <= self.y1 + eps

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.x, self.y, self.w, self.h)

    @classmethod
    def from_bounds(cls, b) -> "Box":
        x0, y0, x1, y1 = b
        return cls(x0, y0, x1 - x0, y1 - y0)


# Safe areas (fractions of the frame).  Broadcast convention: action-safe 93 %, title-safe
# 90 %.  Vertical social formats additionally reserve UI zones top/bottom.
SAFE_AREAS = {
    "16:9": {"action": (0.035, 0.035, 0.035, 0.035), "title": (0.05, 0.05, 0.05, 0.05)},
    "9:16": {"action": (0.05, 0.08, 0.05, 0.14), "title": (0.08, 0.12, 0.08, 0.20)},
    "1:1": {"action": (0.05, 0.05, 0.05, 0.05), "title": (0.08, 0.08, 0.08, 0.08)},
    "4:5": {"action": (0.05, 0.05, 0.05, 0.08), "title": (0.08, 0.08, 0.08, 0.12)},
}


def aspect_key(width: int, height: int) -> str:
    r = width / height
    best = min(SAFE_AREAS, key=lambda k: abs(r - _ratio(k)))
    return best


def _ratio(k: str) -> float:
    a, b = k.split(":")
    return float(a) / float(b)


def safe_area(width: int, height: int, kind: str = "title") -> Box:
    """(left, top, right, bottom) margins applied → the safe Box in pixels."""
    l, t, r, b = SAFE_AREAS[aspect_key(width, height)][kind]
    return Box(width * l, height * t, width * (1 - l - r), height * (1 - t - b))


@dataclass
class LockupResult:
    symbol: np.ndarray  # 3×3 matrix: symbol source space → frame
    wordmark: np.ndarray | None  # 3×3 matrix: wordmark source space → frame
    symbol_box: Box
    wordmark_box: Box | None
    box: Box
    extras: dict = field(default_factory=dict)
    arrangement: str = "horizontal"


def build_lockup(symbol_bounds, frame_w: int, frame_h: int, wordmark_bounds=None, *, arrangement: str = "horizontal",
                 symbol_height: float | None = None, gap: float = 0.35, wordmark_cap_height: float | None = None,
                 wordmark_scale: float | None = None, center=None, optical_offset: float = 0.0, fit_safe: bool = True) -> LockupResult:
    """Place a symbol (and optional wordmark) as a centred lockup.

    ``symbol_bounds`` / ``wordmark_bounds`` are (x0, y0, x1, y1) in their own source spaces.
    ``gap`` is a fraction of the symbol height.  For horizontal lockups the wordmark is
    scaled so its *cap height* equals ``wordmark_cap_height`` (default 42 % of the symbol
    height) and vertically centred on the symbol, which reads optically centred.

    Per-aspect rules (so the same scene re-lays out for 16:9, 9:16, 1:1 and 4:5 instead of
    being cropped): with ``arrangement="auto"`` a horizontal lockup that would have to shrink
    below 75 % to fit the title-safe area becomes vertical (symbol above wordmark); with
    ``fit_safe`` the lockup is scaled down to fit the title-safe area and centred in it.
    """
    safe = safe_area(frame_w, frame_h, "title")
    if center is None:
        center = (safe.x + safe.w / 2, safe.y + safe.h / 2) if fit_safe else (frame_w / 2, frame_h / 2)
    target_h = symbol_height if symbol_height is not None else frame_h * (0.28 if wordmark_bounds is not None else 0.36)

    def place(arr, sym_h):
        k = sym_h / target_h  # explicit wordmark scales / cap heights shrink with the lockup
        return _place(symbol_bounds, wordmark_bounds, arr, sym_h, gap, None if wordmark_cap_height is None else wordmark_cap_height * k,
                      None if wordmark_scale is None else wordmark_scale * k, center, optical_offset)

    def fit(res):
        return min(1.0, safe.w / max(res.box.w, 1e-9), safe.h / max(res.box.h, 1e-9)) if fit_safe else 1.0

    arr = "horizontal" if arrangement == "auto" else arrangement
    res = place(arr, target_h)
    f = fit(res)
    if arrangement == "auto" and wordmark_bounds is not None and f < 0.75:
        alt = place("vertical", target_h)
        fa = fit(alt)
        if fa > f:
            arr, res, f = "vertical", alt, fa
    if f < 1.0:
        res = place(arr, target_h * f * 0.999)
    res.arrangement = arr
    return res


def _place(symbol_bounds, wordmark_bounds, arrangement, target_h, gap, wordmark_cap_height, wordmark_scale, center, optical_offset) -> LockupResult:
    sx0, sy0, sx1, sy1 = symbol_bounds
    sw, sh = sx1 - sx0, sy1 - sy0
    s_sym = target_h / sh
    sym_w, sym_h = sw * s_sym, sh * s_sym
    cx, cy = center
    if wordmark_bounds is None:
        x = cx - sym_w / 2
        y = cy - sym_h / 2
        M = m_translate(x, y) @ m_scale(s_sym) @ m_translate(-sx0, -sy0)
        b = Box(x, y, sym_w, sym_h)
        return LockupResult(M, None, b, None, b)
    wx0, wy0, wx1, wy1 = wordmark_bounds
    ww, wh = wx1 - wx0, wy1 - wy0
    if wordmark_scale is None:
        cap = wordmark_cap_height if wordmark_cap_height is not None else 0.42 * sym_h
        wscale = cap / wh
    else:
        wscale = wordmark_scale
    wW, wH = ww * wscale, wh * wscale
    g = gap * sym_h
    if arrangement == "vertical":
        total_h = sym_h + g + wH
        top = cy - total_h / 2
        sym_x, sym_y = cx - sym_w / 2, top
        wm_x, wm_y = cx - wW / 2, top + sym_h + g
        box = Box(min(sym_x, wm_x), top, max(sym_w, wW), total_h)
    else:
        total_w = sym_w + g + wW
        left = cx - total_w / 2 + optical_offset * sym_w
        sym_x, sym_y = left, cy - sym_h / 2
        wm_x, wm_y = left + sym_w + g, cy - wH / 2
        box = Box(left, min(sym_y, wm_y), total_w, max(sym_h, wH))
    Ms = m_translate(sym_x, sym_y) @ m_scale(s_sym) @ m_translate(-sx0, -sy0)
    Mw = m_translate(wm_x, wm_y) @ m_scale(wscale) @ m_translate(-wx0, -wy0)
    return LockupResult(Ms, Mw, Box(sym_x, sym_y, sym_w, sym_h), Box(wm_x, wm_y, wW, wH), box)


def fit_to_reference(render_alpha, reference_alpha: np.ndarray, x0=(1.0, 0.0, 0.0), refine: bool = True) -> dict:
    """Find the similarity transform (scale s, tx, ty) that best matches a reference.

    ``render_alpha(s, tx, ty) -> HxW float`` renders your mark with that transform;
    ``reference_alpha`` is the target coverage (e.g. alpha of the designer's PNG).
    Initialised from image moments (area → scale, centroid → translation), refined with
    least squares on the pixel residual.  Returns {s, tx, ty, rms}.
    """
    ref = np.asarray(reference_alpha, dtype=np.float64)

    def moments(a):
        m = a.sum()
        if m <= 0:
            return 0.0, 0.0, 0.0
        ys, xs = np.mgrid[0 : a.shape[0], 0 : a.shape[1]]
        return m, (xs * a).sum() / m, (ys * a).sum() / m

    s0, tx0, ty0 = x0
    cur = np.asarray(render_alpha(s0, tx0, ty0), dtype=np.float64)
    m_r, cx_r, cy_r = moments(ref)
    m_c, cx_c, cy_c = moments(cur)
    if m_c > 0 and m_r > 0:
        k = np.sqrt(m_r / m_c)
        s0 = s0 * k
        # scaling about the origin moves the centroid: c' = k * c + t*(1-k)... recompute
        cur = np.asarray(render_alpha(s0, tx0, ty0), dtype=np.float64)
        _, cx_c, cy_c = moments(cur)
        tx0 += cx_r - cx_c
        ty0 += cy_r - cy_c
    params = np.array([s0, tx0, ty0])
    if refine:
        def resid(p):
            return (np.asarray(render_alpha(*p), dtype=np.float64) - ref).ravel()

        res = optimize.least_squares(resid, params, diff_step=[1e-4, 1e-3, 1e-3], max_nfev=60)
        params = res.x
    final = np.asarray(render_alpha(*params), dtype=np.float64)
    rms = float(np.sqrt(np.mean((final - ref) ** 2)))
    return {"s": float(params[0]), "tx": float(params[1]), "ty": float(params[2]), "rms": rms}


__all__ = ["Box", "LockupResult", "SAFE_AREAS", "aspect_key", "build_lockup", "fit_to_reference", "safe_area"]
