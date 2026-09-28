"""Layers and compositing.

A :class:`Frame` holds two buffers:

* **base** — a skia half-float surface where solid layers (backgrounds, brand marks,
  type) are composited *in sRGB* with ordinary source-over, exactly like design tools.
* **light** — additive HDR light in **linear** light.  Drawn either with skia onto a
  half-float surface using *gain-scaled* paints (skia's ``kPlus`` clamps at 1.0, so we
  store light / ``LIGHT_GAIN`` and multiply back on read) or added directly as numpy.

``Frame.resolve()`` = linear(base) + light (+ bloom) → highlight roll-off (identity on
[0, 1]) → sRGB.  With no light the result is bit-identical to the base, which is what
makes final frames pixel-exact to the reference lockup.

Pitfalls handled here (see ENGINE_NOTES.md):
* ``Image.toarray()`` returns **unpremultiplied BGRA** by default → always request
  ``kRGBA_F32`` + ``kPremul`` (:func:`read_surface`).
* Paint colours are unpremultiplied: coverage paint is ``Color4f(1, 1, 1, w)``.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Callable

import cv2
import numpy as np
import skia

from .brand import Color, linear_to_srgb, srgb_to_linear

LIGHT_GAIN = 32.0  # max summed HDR intensity representable on the light surface


def new_surface(width: int, height: int, hdr: bool = True) -> skia.Surface:
    ct = skia.ColorType.kRGBA_F16_ColorType if hdr else skia.ColorType.kRGBA_8888_ColorType
    info = skia.ImageInfo.Make(int(width), int(height), ct, skia.AlphaType.kPremul_AlphaType)
    surf = skia.Surface.MakeRaster(info)
    if surf is None:
        raise RuntimeError(f"could not allocate {width}x{height} surface")
    return surf


def read_surface(surf: skia.Surface) -> np.ndarray:
    """(H, W, 4) float32 **premultiplied** RGBA."""
    img = surf.makeImageSnapshot()
    return img.toarray(colorType=skia.ColorType.kRGBA_F32_ColorType, alphaType=skia.AlphaType.kPremul_AlphaType)


def write_surface(surf: skia.Surface, rgba_premul: np.ndarray) -> None:
    arr = np.ascontiguousarray(rgba_premul, dtype=np.float32)
    img = skia.Image.fromarray(arr, colorType=skia.ColorType.kRGBA_F32_ColorType, alphaType=skia.AlphaType.kPremul_AlphaType)
    c = surf.getCanvas()
    c.save()
    c.resetMatrix()
    c.drawImage(img, 0, 0, skia.SamplingOptions(), skia.Paint(BlendMode=skia.BlendMode.kSrc))
    c.restore()


def coverage_paint(weight: float = 1.0) -> skia.Paint:
    """White paint whose *alpha* is the coverage weight (colour stays unpremultiplied)."""
    return skia.Paint(AntiAlias=True, Color4f=skia.Color4f(1.0, 1.0, 1.0, float(max(0.0, min(1.0, weight)))))


def rasterize_mask(width: int, height: int, draw: Callable[[skia.Canvas], None], scale: float = 1.0) -> np.ndarray:
    """Render ``draw(canvas)`` and return its alpha coverage (H, W) float32."""
    info = skia.ImageInfo.MakeA8(int(width), int(height))
    surf = skia.Surface.MakeRaster(info)
    c = surf.getCanvas()
    c.clear(skia.ColorTRANSPARENT)
    c.scale(scale, scale)
    draw(c)
    a = surf.makeImageSnapshot().toarray(colorType=skia.ColorType.kAlpha_8_ColorType)
    return (a.reshape(height, width).astype(np.float32)) / 255.0


def path_mask(width: int, height: int, path: skia.Path, matrix: skia.Matrix | None = None, scale: float = 1.0,
              stroke: float | None = None) -> np.ndarray:
    def draw(c):
        if matrix is not None:
            c.concat(matrix)
        p = coverage_paint()
        if stroke:
            p.setStyle(skia.Paint.kStroke_Style)
            p.setStrokeWidth(stroke)
        c.drawPath(path, p)

    return rasterize_mask(width, height, draw, scale)


def rolloff(lin: np.ndarray, strength: float = 1.0) -> np.ndarray:
    """Highlight roll-off: **identity** for pixels whose channels are all ≤ 1.

    Over-range energy bleeds towards white (like film/sensor saturation) instead of
    hard-clipping to a hue-shifted colour.  Output ≤ 1.
    """
    m = channel_max(lin)
    over = m > 1.0
    if not over.any():
        return lin
    out = lin.copy()
    px = lin[over]  # (k, 3) only the over-range pixels
    mk = m[over][:, None]
    w = 1.0 - np.exp(-strength * (mk - 1.0))  # 0 at m = 1 → 1 as m → ∞
    norm = px / mk  # hue at full brightness
    out[over] = norm + (1.0 - norm) * w
    return out


def channel_max(img: np.ndarray) -> np.ndarray:
    """Per-pixel max over RGB (much faster than ``img.max(axis=-1)``)."""
    return np.maximum(np.maximum(img[..., 0], img[..., 1]), img[..., 2])


class Frame:
    """One frame being built. Coordinates passed to drawing helpers are in *design*
    pixels; ``scale`` maps them to the actual raster (previews render at scale < 1)."""

    def __init__(self, width: int, height: int, scale: float = 1.0, background=None):
        self.design_w, self.design_h = int(width), int(height)
        self.scale = float(scale)
        self.w = max(1, int(round(width * scale)))
        self.h = max(1, int(round(height * scale)))
        self.surface = new_surface(self.w, self.h)
        self._light_surface: skia.Surface | None = None
        self.light: np.ndarray | None = None  # linear HDR (H, W, 3)
        self.post: list[Callable[[np.ndarray], np.ndarray]] = []
        c = self.surface.getCanvas()
        c.clear(Color.of(background).skia4f() if background is not None else skia.Color4f(0, 0, 0, 1))

    # -- base (sRGB) -------------------------------------------------------------------
    @contextmanager
    def draw(self, matrix: np.ndarray | None = None):
        """Canvas for solid layers in design coordinates (sRGB source-over)."""
        from .brand import skia_matrix

        c = self.surface.getCanvas()
        c.save()
        c.scale(self.scale, self.scale)
        if matrix is not None:
            c.concat(skia_matrix(matrix))
        try:
            yield c
        finally:
            c.restore()

    def fill(self, color) -> None:
        self.surface.getCanvas().clear(Color.of(color).skia4f())

    def base(self) -> np.ndarray:
        """(H, W, 4) premultiplied sRGB float."""
        return read_surface(self.surface)

    def set_base(self, rgba_premul: np.ndarray) -> None:
        write_surface(self.surface, rgba_premul)

    def over(self, rgba_premul: np.ndarray, x: int = 0, y: int = 0) -> None:
        """Source-over a premultiplied sRGB image (raster pixels) at (x, y)."""
        base = self.base()
        h, w = rgba_premul.shape[:2]
        x0, y0 = max(x, 0), max(y, 0)
        x1, y1 = min(x + w, self.w), min(y + h, self.h)
        if x1 <= x0 or y1 <= y0:
            return
        src = rgba_premul[y0 - y : y1 - y, x0 - x : x1 - x]
        dst = base[y0:y1, x0:x1]
        base[y0:y1, x0:x1] = src + dst * (1.0 - src[..., 3:4])
        self.set_base(base)

    # -- light (linear HDR) --------------------------------------------------------------
    def _ensure_light(self) -> np.ndarray:
        if self.light is None:
            self.light = np.zeros((self.h, self.w, 3), np.float32)
        return self.light

    @contextmanager
    def light_canvas(self, matrix: np.ndarray | None = None):
        """Canvas for additive light. Use :meth:`light_paint` for the paints."""
        from .brand import skia_matrix

        if self._light_surface is None:
            self._light_surface = new_surface(self.w, self.h)
            self._light_surface.getCanvas().clear(skia.Color4f(0, 0, 0, 0))
        c = self._light_surface.getCanvas()
        c.save()
        c.scale(self.scale, self.scale)
        if matrix is not None:
            c.concat(skia_matrix(matrix))
        try:
            yield c
        finally:
            c.restore()

    def light_paint(self, color, intensity: float = 1.0, blur: float = 0.0, stroke: float | None = None,
                    cap: str = "round") -> skia.Paint:
        """Additive paint for HDR light: ``color`` is sRGB (converted to linear),
        ``intensity`` may exceed 1; ``blur`` is a Gaussian sigma in design pixels."""
        lin = Color.of(color).linear * float(intensity) / LIGHT_GAIN
        p = skia.Paint(AntiAlias=True, BlendMode=skia.BlendMode.kPlus)
        p.setColor4f(skia.Color4f(float(lin[0]), float(lin[1]), float(lin[2]), 1.0))
        if blur > 0:
            p.setMaskFilter(skia.MaskFilter.MakeBlur(skia.BlurStyle.kNormal_BlurStyle, blur))
        if stroke is not None:
            p.setStyle(skia.Paint.kStroke_Style)
            p.setStrokeWidth(stroke)
            p.setStrokeCap(skia.Paint.kRound_Cap if cap == "round" else skia.Paint.kButt_Cap)
            p.setStrokeJoin(skia.Paint.kRound_Join)
        return p

    def add_light(self, rgb_linear: np.ndarray, x: int = 0, y: int = 0) -> None:
        """Add an (h, w, 3) linear HDR patch at raster offset (x, y) (clipped)."""
        L = self._ensure_light()
        h, w = rgb_linear.shape[:2]
        x0, y0 = max(x, 0), max(y, 0)
        x1, y1 = min(x + w, self.w), min(y + h, self.h)
        if x1 <= x0 or y1 <= y0:
            return
        L[y0:y1, x0:x1] += rgb_linear[y0 - y : y1 - y, x0 - x : x1 - x]

    def add_light_mask(self, mask: np.ndarray, color, intensity: float = 1.0, x: int = 0, y: int = 0) -> None:
        lin = Color.of(color).linear * float(intensity)
        self.add_light(mask[..., None] * lin[None, None, :], x, y)

    def light_total(self) -> np.ndarray | None:
        out = None
        if self._light_surface is not None:
            out = read_surface(self._light_surface)[..., :3] * LIGHT_GAIN
        if self.light is not None:
            out = self.light if out is None else out + self.light
        return out

    # -- resolve -------------------------------------------------------------------------
    def resolve_linear(self, bloom: dict | None = None, rolloff_strength: float = 1.0, bloom_base: bool = False) -> np.ndarray:
        base = self.base()
        lin = srgb_to_linear(np.clip(base[..., :3], 0.0, None))
        light = self.light_total()
        if light is not None:
            src = light
            if bloom is not None:
                from .fx import bloom as do_bloom

                bsrc = light + (lin if bloom_base else 0.0)
                src = light + do_bloom(bsrc, scale=self.scale, **bloom)
            lin = lin + src
        for f in self.post:
            lin = f(lin)
        return rolloff(lin, rolloff_strength)

    def resolve(self, **kw) -> np.ndarray:
        """Final sRGB float32 (H, W, 3) in [0, 1]."""
        return np.clip(linear_to_srgb(self.resolve_linear(**kw)), 0.0, 1.0)


# ======================================================================================
# temporal motion blur
# ======================================================================================


def motion_blur(render_linear: Callable[[float], np.ndarray], t: float, fps: float, shutter_deg: float = 180.0,
                max_samples: int = 16, tol: float = 1.5 / 255, compare_scale: int = 4) -> tuple[np.ndarray, int]:
    """Real temporal averaging over the shutter interval, adaptively refined.

    Uses nested trapezoidal sampling across [t − s/2, t + s/2] (s = shutter / 360 / fps):
    endpoints first; if identical the frame is static and one sample is returned
    (so still frames stay bit-exact).  Otherwise the interval count doubles
    (2, 4, 8, …) until successive estimates differ by < ``tol`` at the 99.9th
    percentile, or ``max_samples`` is reached.  Returns (linear image, samples used).
    """
    s = shutter_deg / 360.0 / fps
    if s <= 0 or max_samples <= 1:
        return render_linear(t), 1
    t0, t1 = t - s / 2, t + s / 2
    a, b = render_linear(t0), render_linear(t1)
    if np.array_equal(a, b):
        return a, 2

    def small(img):
        if compare_scale <= 1:
            return img
        return cv2.resize(img, (max(1, img.shape[1] // compare_scale), max(1, img.shape[0] // compare_scale)), interpolation=cv2.INTER_AREA)

    n = 1
    ends = (a + b) * 0.5
    mids = np.zeros_like(a)
    est = ends  # trapezoid with 1 interval
    samples = 2
    while samples < max_samples + 1:
        n2 = n * 2
        new = [t0 + (2 * k + 1) * (t1 - t0) / n2 for k in range(n)]
        for tt in new:
            mids += render_linear(tt)
            samples += 1
        new_est = (ends + mids) / n2
        diff = np.abs(small(new_est) - small(est))
        est = new_est
        n = n2
        if float(np.percentile(diff, 99.5)) < tol:
            break
    return est.astype(np.float32), samples


# ======================================================================================
# depth of field
# ======================================================================================


def dof_composite(layers: list[tuple[np.ndarray, float]], focus: float, aperture: float, max_blur: float = 24.0,
                  bins: int = 6, background: np.ndarray | None = None) -> np.ndarray:
    """Composite premultiplied RGBA layers with depth-dependent blur (DoF bins).

    Each layer has a depth; circle-of-confusion radius = aperture × |1/focus − 1/depth|
    (thin lens, in pixels, clamped to ``max_blur``).  Layers are grouped into ``bins``
    of similar blur, each bin blurred once, then composited back-to-front.
    """
    if not layers:
        raise ValueError("no layers")
    h, w = layers[0][0].shape[:2]
    out = background.copy() if background is not None else np.zeros((h, w, 4), np.float32)
    coc = [min(max_blur, aperture * abs(1.0 / max(focus, 1e-6) - 1.0 / max(d, 1e-6))) for _, d in layers]
    edges = np.linspace(0, max_blur + 1e-6, bins + 1)
    order = sorted(range(len(layers)), key=lambda i: -layers[i][1])  # far → near
    # group consecutive-in-depth layers with the same blur bin
    groups: list[tuple[float, list[int]]] = []
    for i in order:
        b = int(np.searchsorted(edges, coc[i], side="right") - 1)
        if groups and groups[-1][0] == b:
            groups[-1][1].append(i)
        else:
            groups.append((b, [i]))
    for b, idx in groups:
        acc = np.zeros((h, w, 4), np.float32)
        for i in idx:
            src = layers[i][0]
            acc = src + acc * (1.0 - src[..., 3:4])
        radius = float(np.mean([coc[i] for i in idx]))
        if radius > 0.5:
            sigma = radius / 2.0
            acc = cv2.GaussianBlur(acc, (0, 0), sigmaX=sigma, sigmaY=sigma, borderType=cv2.BORDER_CONSTANT)
        out = acc + out * (1.0 - acc[..., 3:4])
    return out


__all__ = [
    "Frame", "LIGHT_GAIN", "coverage_paint", "dof_composite", "motion_blur", "new_surface", "path_mask",
    "rasterize_mask", "read_surface", "rolloff", "write_surface",
]

