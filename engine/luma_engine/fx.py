"""Visual effects.

Coordinates are **design pixels** (the scene's nominal resolution); every helper takes
the :class:`~luma_engine.layers.Frame` and applies ``frame.scale`` itself, so the same
scene renders previews at any scale.

Light effects add to the frame's linear HDR buffer; solid effects draw on the sRGB base.
Every effect is written so that at its end state it contributes *exactly nothing* (light)
or draws exactly the original artwork (solids), keeping final frames pixel-exact.
"""
from __future__ import annotations

import functools
import math
from dataclasses import dataclass
from typing import Callable, Sequence

import cv2
import numpy as np
import skia

from .brand import Color, skia_matrix
from .layers import Frame, coverage_paint, path_mask, rasterize_mask
from .motion import clamp, get_ease, smoothstep

# ======================================================================================
# full-frame light processing
# ======================================================================================


def bloom(img: np.ndarray, threshold: float = 1.0, knee: float = 0.5, intensity: float = 0.6, levels: int = 6,
          scale: float = 1.0, tint=None) -> np.ndarray:
    """Pyramid bloom of a linear HDR image; returns the bloom contribution only.

    Soft-knee threshold (like Unreal/Unity), a Gaussian pyramid down, then an additive
    pyramid up.  ``levels`` is defined at scale 1 and reduced at preview scales so the
    glow has the same size in design pixels.
    """
    from .layers import channel_max

    h, w = img.shape[:2]
    # threshold at half resolution: bloom is low-frequency, this is 4× cheaper
    img = cv2.resize(img, (max(1, w // 2), max(1, h // 2)), interpolation=cv2.INTER_AREA)
    br = channel_max(img)
    eps = 1e-6
    soft = np.clip(br - threshold + knee, 0, 2 * knee)
    soft = soft * soft / (4 * knee + eps)
    contrib = np.maximum(soft, br - threshold) / np.maximum(br, eps)
    src = img * contrib[..., None]
    lv = max(1, levels - 1 + int(round(math.log2(max(scale, 1e-3)))))
    downs = []
    cur = src
    for _ in range(lv):
        if min(cur.shape[:2]) < 4:
            break
        cur = cv2.pyrDown(cur)
        downs.append(cur)
    if not downs:
        return np.zeros_like(img)
    acc = downs[-1]
    for d in reversed(downs[:-1]):
        acc = cv2.pyrUp(acc, dstsize=(d.shape[1], d.shape[0])) + d
    acc = cv2.pyrUp(acc, dstsize=(img.shape[1], img.shape[0]))
    acc = cv2.resize(acc, (w, h), interpolation=cv2.INTER_LINEAR)
    out = acc * (intensity / len(downs))
    if tint is not None:
        out = out * Color.of(tint).linear[None, None, :]
    return out.astype(np.float32)


def light_shafts(light: np.ndarray, center, scale: float = 1.0, length: float = 0.35, samples: int = 24,
                 decay: float = 0.93, intensity: float = 0.8, downsample: int = 4) -> np.ndarray:
    """Volumetric light shafts: radial (zoom) blur of the light towards ``center``."""
    h, w = light.shape[:2]
    ds = max(1, downsample)
    small = cv2.resize(light, (max(1, w // ds), max(1, h // ds)), interpolation=cv2.INTER_AREA)
    cx, cy = center[0] * scale / ds, center[1] * scale / ds
    acc = np.zeros_like(small)
    wsum = 0.0
    for k in range(samples):
        s = 1.0 - length * k / samples
        M = np.array([[s, 0, cx * (1 - s)], [0, s, cy * (1 - s)]], dtype=np.float32)
        wk = decay**k
        acc += cv2.warpAffine(small, M, (small.shape[1], small.shape[0]), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT) * wk
        wsum += wk
    acc /= wsum
    return (cv2.resize(acc, (w, h), interpolation=cv2.INTER_LINEAR) * intensity).astype(np.float32)


# ======================================================================================
# point / line lights
# ======================================================================================


def glow_dot(frame: Frame, pos, radius: float, color="#FFFFFF", intensity: float = 4.0, core: float = 0.6,
             core_ratio: float = 0.18) -> None:
    """A soft point light (pen tips, seeds, sparks).  ``radius`` ≈ Gaussian sigma."""
    if intensity <= 0 or radius <= 0:
        return
    s = frame.scale
    x, y = pos[0] * s, pos[1] * s
    r = radius * s
    R = int(math.ceil(r * 4)) + 2
    x0, y0 = int(math.floor(x)) - R, int(math.floor(y)) - R
    ys, xs = np.mgrid[y0 : y0 + 2 * R + 1, x0 : x0 + 2 * R + 1].astype(np.float32)
    d2 = (xs + 0.5 - x) ** 2 + (ys + 0.5 - y) ** 2
    prof = np.exp(-d2 / (2 * r * r)) + core * np.exp(-d2 / (2 * (r * core_ratio) ** 2 + 1e-6)) * 4.0
    frame.add_light_mask(prof.astype(np.float32), color, intensity, x0, y0)


def beam(frame: Frame, p0, p1, width: float = 3.0, color="#FFFFFF", intensity: float = 3.0, falloff: float = 1.5,
         fade_in: float = 0.05, halo: float = 6.0, halo_intensity: float = 0.25) -> None:
    """A light beam from ``p0`` towards ``p1``: Gaussian cross-section, intensity
    ``(1 − u)^falloff`` along its length, with a wide soft halo."""
    if intensity <= 0:
        return
    s = frame.scale
    a = np.array(p0, dtype=np.float32) * s
    b = np.array(p1, dtype=np.float32) * s
    wv = max(width * s, 0.35)
    hw = max(halo * s, wv)
    pad = int(math.ceil(hw * 3)) + 2
    x0 = int(math.floor(min(a[0], b[0]))) - pad
    y0 = int(math.floor(min(a[1], b[1]))) - pad
    x1 = int(math.ceil(max(a[0], b[0]))) + pad
    y1 = int(math.ceil(max(a[1], b[1]))) + pad
    x0c, y0c, x1c, y1c = max(x0, 0), max(y0, 0), min(x1, frame.w), min(y1, frame.h)
    if x1c <= x0c or y1c <= y0c:
        return
    ys, xs = np.mgrid[y0c:y1c, x0c:x1c].astype(np.float32)
    px, py = xs + 0.5, ys + 0.5
    d = b - a
    L2 = float(d @ d) or 1e-6
    u = np.clip(((px - a[0]) * d[0] + (py - a[1]) * d[1]) / L2, 0.0, 1.0)
    cx, cy = a[0] + u * d[0], a[1] + u * d[1]
    dist2 = (px - cx) ** 2 + (py - cy) ** 2
    along = (1.0 - u) ** falloff * np.clip(u / max(fade_in, 1e-6), 0.0, 1.0) if fade_in > 0 else (1.0 - u) ** falloff
    prof = (np.exp(-dist2 / (2 * wv * wv)) + halo_intensity * np.exp(-dist2 / (2 * hw * hw))) * along
    frame.add_light_mask(prof.astype(np.float32), color, intensity, x0c, y0c)


def glow_path(frame: Frame, path: skia.Path, color="#FFFFFF", intensity: float = 1.5, blur: float = 6.0,
              stroke: float | None = None, matrix: np.ndarray | None = None, core: float = 0.0) -> None:
    """Additive glow of a path (filled, or stroked when ``stroke`` is set)."""
    if intensity <= 0:
        return
    with frame.light_canvas(matrix) as c:
        c.drawPath(path, frame.light_paint(color, intensity, blur=blur, stroke=stroke))
        if core > 0:
            c.drawPath(path, frame.light_paint(color, core, blur=0.0, stroke=stroke))


# ======================================================================================
# write-on
# ======================================================================================


@dataclass
class PenHead:
    pos: tuple[float, float]
    tangent: tuple[float, float]
    contour: int


def contour_lengths(path: skia.Path) -> list[float]:
    m = skia.PathMeasure(path, False)
    out = []
    while True:
        out.append(m.getLength())
        if not m.nextContour():
            break
    return [x for x in out if x > 0]


def trim_path(path: skia.Path, progress: float, mode: str = "parallel", start: float = 0.0) -> tuple[skia.Path, list[PenHead]]:
    """Trim a path to ``progress`` (0..1) like After Effects "Trim Paths".

    ``mode="parallel"``: every contour draws simultaneously; ``"sequential"``: contours
    draw one after another along the total length.  Returns the partial path and the
    pen-head positions/tangents (for glowing pen tips).
    """
    p = float(clamp(progress))
    out = skia.Path()
    heads: list[PenHead] = []
    if p <= 0:
        return out, heads
    lengths = contour_lengths(path)
    total = sum(lengths)
    m = skia.PathMeasure(path, False)
    acc = 0.0
    ci = 0
    while True:
        L = m.getLength()
        if L > 0:
            if mode == "sequential":
                a = total * start
                seg_end = min(max(total * p - acc, 0.0), L)
                seg_start = min(max(a - acc, 0.0), L)
            else:
                seg_start, seg_end = L * start, L * p
            if seg_end > seg_start:
                m.getSegment(seg_start, seg_end, out, True)
                if seg_end < L - 1e-6 and p < 1.0:
                    ok, pos, tan = _postan(m, seg_end)
                    if ok:
                        heads.append(PenHead(pos, tan, ci))
            acc += L
            ci += 1
        if not m.nextContour():
            break
    if p >= 1.0 and start <= 0.0:
        return path, []
    return out, heads


def _postan(m: skia.PathMeasure, d: float):
    try:
        res = m.getPosTan(d)
    except Exception:
        return False, (0, 0), (1, 0)
    if isinstance(res, tuple) and len(res) == 2:
        pos, tan = res
        return True, (pos.x(), pos.y()), (tan.x(), tan.y())
    return False, (0, 0), (1, 0)


def write_on(frame: Frame, path: skia.Path, progress: float, width: float = 3.0, color="#FFFFFF", matrix: np.ndarray | None = None,
             mode: str = "parallel", pen_color=None, pen_intensity: float = 5.0, pen_radius: float = 5.0,
             glow_intensity: float = 1.2, glow_blur: float = 5.0, tail: float = 0.18, draw_base: bool = True) -> list[PenHead]:
    """Stroke write-on with a glowing pen tip and a hot "fresh ink" tail.

    ``matrix`` maps path space → design space (the stroke ``width`` is in design px).
    """
    if progress <= 0:
        return []
    M = np.eye(3) if matrix is None else np.asarray(matrix, dtype=np.float64)
    dp = skia.Path(path)
    dp.transform(skia_matrix(M))
    part, heads = trim_path(dp, progress, mode)
    if draw_base and not part.isEmpty():
        with frame.draw() as c:
            p = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=width, Color4f=Color.of(color).skia4f())
            p.setStrokeCap(skia.Paint.kRound_Cap)
            p.setStrokeJoin(skia.Paint.kRound_Join)
            c.drawPath(part, p)
    if progress < 1.0:
        pc = pen_color if pen_color is not None else color
        if glow_intensity > 0 and tail > 0:
            hot, _ = trim_path(dp, progress, mode, start=max(0.0, progress - tail))
            glow_path(frame, hot, pc, glow_intensity, blur=glow_blur, stroke=width * 1.5)
        for h in heads:
            glow_dot(frame, h.pos, pen_radius, pc, pen_intensity)
    return heads


# ======================================================================================
# fills
# ======================================================================================


def ignite(frame: Frame, path: skia.Path, paint: skia.Paint, progress: float, origin=None, matrix: np.ndarray | None = None,
           flash: float = 0.0, flash_color="#FFFFFF", flash_blur: float = 10.0, softness: float = 0.25) -> None:
    """Fill ignition: the fill spreads radially from ``origin`` (design px) and an HDR
    flash (``flash`` = current intensity) glows over the part.  At progress ≥ 1 the
    path is drawn with its paint exactly."""
    p = float(clamp(progress))
    M = np.eye(3) if matrix is None else np.asarray(matrix, dtype=np.float64)
    if p > 0:
        with frame.draw(M) as c:
            if p >= 1.0:
                c.drawPath(path, paint)
            else:
                r = path.computeTightBounds()
                if origin is None:
                    o = (r.centerX(), r.centerY())
                else:
                    inv = np.linalg.inv(M)
                    v = inv @ np.array([origin[0], origin[1], 1.0])
                    o = (v[0] / v[2], v[1] / v[2])
                corners = [(r.left(), r.top()), (r.right(), r.top()), (r.left(), r.bottom()), (r.right(), r.bottom())]
                maxd = max(math.hypot(x - o[0], y - o[1]) for x, y in corners) or 1.0
                R = maxd * (1 + softness) * p
                c.saveLayer()
                c.drawPath(path, paint)
                inner = max(0.0, 1.0 - softness / (1 + softness))
                mask = skia.GradientShader.MakeRadial(
                    skia.Point(*o), max(R, 1e-3), [skia.ColorWHITE, skia.ColorWHITE, skia.ColorTRANSPARENT], [0.0, inner, 1.0]
                )
                c.drawPaint(skia.Paint(Shader=mask, BlendMode=skia.BlendMode.kDstIn))
                c.restore()
    if flash > 0:
        glow_path(frame, path, flash_color, flash, blur=flash_blur, matrix=M)
        glow_path(frame, path, flash_color, flash * 0.5, blur=0.0, matrix=M)


# ======================================================================================
# shockwave, flood, refraction
# ======================================================================================


def radial_flood(frame: Frame, center, radius: float, color, softness: float = 40.0) -> None:
    """Flood the base with ``color`` inside a growing circle (soft edge).  Once the circle
    covers the whole frame the fill is exact."""
    s = frame.scale
    cx, cy = center
    far = max(math.hypot(cx - x, cy - y) for x in (0, frame.design_w) for y in (0, frame.design_h))
    if radius <= 0:
        return
    if radius - softness >= far:
        frame.fill(color)
        return
    col = Color.of(color)
    with frame.draw() as c:
        r_out = max(radius, 1e-3)
        inner = max(0.0, (radius - softness) / r_out)
        sh = skia.GradientShader.MakeRadial(
            skia.Point(cx, cy), r_out, [col.skia(), col.skia(), col.with_alpha(0).skia()], [0.0, inner, 1.0]
        )
        c.drawCircle(cx, cy, r_out, skia.Paint(AntiAlias=True, Shader=sh))
    _ = s


def shockwave_ring(frame: Frame, center, radius: float, width: float = 18.0, color="#FFFFFF", intensity: float = 2.0,
                   blur: float = 10.0) -> None:
    if radius <= 0 or intensity <= 0:
        return
    with frame.light_canvas() as c:
        c.drawCircle(center[0], center[1], radius, frame.light_paint(color, intensity, blur=blur, stroke=width))
        c.drawCircle(center[0], center[1], radius, frame.light_paint(color, intensity * 0.6, blur=0.0, stroke=max(1.0, width * 0.15)))


@functools.lru_cache(maxsize=4)
def _grid(h: int, w: int):
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    ys.flags.writeable = False
    xs.flags.writeable = False
    return ys, xs


def refraction_map(h: int, w: int, center, radius: float, width: float, strength: float):
    """cv2.remap maps displacing pixels radially near a ring front (raster units)."""
    ys, xs = _grid(h, w)
    dx, dy = xs - center[0], ys - center[1]
    r = np.sqrt(dx * dx + dy * dy) + 1e-6
    u = (r - radius) / max(width, 1e-3)
    disp = strength * np.exp(-u * u) * np.sin(u * math.pi)  # push/pull across the front
    return (xs - disp * dx / r).astype(np.float32), (ys - disp * dy / r).astype(np.float32)


def refract(frame: Frame, center, radius: float, width: float = 30.0, strength: float = 8.0) -> None:
    """Lens-like refraction of the *base* near an expanding ring front."""
    if strength == 0 or radius <= 0:
        return
    s = frame.scale
    base = frame.base()
    mx, my = refraction_map(frame.h, frame.w, (center[0] * s, center[1] * s), radius * s, width * s, strength * s)
    frame.set_base(cv2.remap(base, mx, my, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT))


# ======================================================================================
# silhouette waves and sheen
# ======================================================================================


def outside_distance(mask: np.ndarray) -> np.ndarray:
    """Euclidean distance (px) from each pixel to the silhouette (0 inside)."""
    inside = (mask > 0.5).astype(np.uint8)
    return cv2.distanceTransform(1 - inside, cv2.DIST_L2, 5).astype(np.float32)


def sdf_wave(frame: Frame, mask: np.ndarray, radius: float, width: float = 12.0, color="#FFFFFF", intensity: float = 1.5,
             dist: np.ndarray | None = None) -> np.ndarray:
    """A wave front that follows the silhouette outward (distance-field ring).
    ``mask`` is raster-resolution coverage.  Returns the distance field for reuse."""
    d = outside_distance(mask) if dist is None else dist
    s = frame.scale
    u = (d - radius * s) / max(width * s, 1e-3)
    band = np.exp(-u * u) * (d > 0)
    frame.add_light_mask(band.astype(np.float32), color, intensity)
    return d


def sheen(frame: Frame, mask: np.ndarray, progress: float, angle_deg: float = 20.0, width: float = 80.0,
          color="#FFFFFF", intensity: float = 0.8, bbox=None) -> None:
    """A soft band of light sweeping across the coverage ``mask`` (raster resolution).

    The band travels perpendicular to ``angle_deg`` over the mask's bounding box; it is
    entirely off the mark at progress 0 and 1.
    """
    if progress <= 0 or progress >= 1 or intensity <= 0:
        return
    s = frame.scale
    ys, xs = np.nonzero(mask > 1e-3)
    if len(xs) == 0:
        return
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    a = math.radians(angle_deg)
    nx, ny = math.cos(a), math.sin(a)
    gy, gx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    proj = gx * nx + gy * ny
    lo, hi = float(proj.min()), float(proj.max())
    w = width * s
    pos = lo - 2 * w + (hi - lo + 4 * w) * progress
    band = np.exp(-(((proj - pos) / w) ** 2))
    frame.add_light_mask((band * mask[y0:y1, x0:x1]).astype(np.float32), color, intensity, x0, y0)


# ======================================================================================
# type
# ======================================================================================


def text_rise(frame: Frame, run, x: float, y: float, progress: Sequence[float] | Callable[[int], float], color="#FFFFFF",
              rise: float = 0.9, ease: str = "out_cubic", fade: bool = True, clip: bool = True, paint: skia.Paint | None = None) -> None:
    """Letters rise into place from behind a mask at the baseline (masked text rise).

    ``run`` is a :class:`~luma_engine.text.TextRun` placed with its origin at (x, y)
    (baseline).  ``progress`` gives each glyph's 0..1 progress.  ``rise`` in em.
    """
    f = get_ease(ease)
    em = run.size
    base_paint = paint or skia.Paint(AntiAlias=True, Color4f=Color.of(color).skia4f())
    with frame.draw() as c:
        if clip:
            c.save()
            top = y - em * 2.0
            bottom = y - run.descender + em * 0.05  # hhea descender is negative
            c.clipRect(skia.Rect.MakeLTRB(x - em, top, x + run.advance + em, bottom))
        for i, g in enumerate(run.glyphs):
            p = progress(i) if callable(progress) else progress[i]
            p = float(clamp(p))
            if p <= 0:
                continue
            e = f(p)
            dy = (1 - e) * rise * em
            pt = skia.Paint(base_paint)
            if fade and p < 1:
                pt.setAlphaf(base_paint.getAlphaf() * min(1.0, e * 1.5))
            c.save()
            c.translate(x, y + dy)
            c.drawPath(g.path, pt)
            c.restore()
        if clip:
            c.restore()


def kinetic_captions(frame: Frame, words: Sequence[dict], t: float, font: str = "Inter", weight: int = 700,
                     size: float = 64.0, center_y: float | None = None, max_width: float | None = None,
                     color="#FFFFFF", dim_color=None, highlight=None, max_lines: int = 2, pop: float = 0.12,
                     shadow: bool = True) -> None:
    """Word-synced captions: groups of words appear as short phrases; the spoken word is
    highlighted and pops slightly.  ``words`` = [{text, start, end}] in seconds."""
    from .text import shape_text, wrap_words

    if not words:
        return
    W, H = frame.design_w, frame.design_h
    max_width = max_width or W * 0.8
    center_y = center_y if center_y is not None else H * 0.82
    groups = caption_groups(words, font, weight, size, max_width, max_lines)
    active = None
    for g in groups:
        if g["start"] - 0.05 <= t < g["end"] + 0.25:
            active = g
    if active is None:
        return
    ws = active["words"]
    lines = wrap_words([w["text"] for w in ws], font, weight, size, max_width)
    lh = size * 1.25
    y0 = center_y - (len(lines) - 1) * lh / 2
    base_col = Color.of(color)
    dim = Color.of(dim_color) if dim_color else base_col.with_alpha(0.55)
    hi = Color.of(highlight) if highlight else base_col
    appear = smoothstep(active["start"] - 0.05, active["start"] + 0.12, t) * (1 - smoothstep(active["end"] + 0.1, active["end"] + 0.25, t))
    space = shape_text(" ", font, weight, size).advance
    with frame.draw() as c:
        for li, line in enumerate(lines):
            runs = [shape_text(ws[i]["text"], font, weight, size) for i in line]
            total = sum(r.advance for r in runs) + space * (len(runs) - 1)
            x = W / 2 - total / 2
            y = y0 + li * lh + size * 0.35
            for wi, r in zip(line, runs):
                w = ws[wi]
                spoken = w["start"] <= t < w["end"] + 0.05
                done = t >= w["end"]
                col = hi if spoken else (base_col if done else dim)
                k = 1.0 + pop * (1 - smoothstep(w["start"], w["start"] + 0.18, t)) if spoken else 1.0
                c.save()
                c.translate(x + r.advance / 2, y - size * 0.35)
                c.scale(k, k)
                c.translate(-r.advance / 2, size * 0.35)
                if shadow:
                    sp = skia.Paint(AntiAlias=True, Color4f=skia.Color4f(0, 0, 0, 0.45 * appear))
                    sp.setMaskFilter(skia.MaskFilter.MakeBlur(skia.BlurStyle.kNormal_BlurStyle, size * 0.08))
                    c.save()
                    c.translate(0, size * 0.04)
                    c.drawPath(r.path, sp)
                    c.restore()
                c.drawPath(r.path, skia.Paint(AntiAlias=True, Color4f=col.skia4f(col.a * appear)))
                c.restore()
                x += r.advance + space


def caption_groups(words: Sequence[dict], font: str = "Inter", weight: int = 700, size: float = 64.0,
                   max_width: float = 1500.0, max_lines: int = 2, max_words: int = 7, gap: float = 0.6) -> list[dict]:
    """Split word timings into on-screen phrases (break on pauses, punctuation, width)."""
    from .text import wrap_words

    groups: list[dict] = []
    cur: list[dict] = []

    def flush():
        if cur:
            groups.append({"words": list(cur), "start": cur[0]["start"], "end": cur[-1]["end"]})
            cur.clear()

    for w in words:
        if cur:
            pause = w["start"] - cur[-1]["end"]
            ends_sentence = cur[-1]["text"].rstrip().endswith((".", "!", "?", "…"))
            trial = [x["text"] for x in cur] + [w["text"]]
            too_wide = len(wrap_words(trial, font, weight, size, max_width)) > max_lines
            if pause > gap or ends_sentence or too_wide or len(cur) >= max_words:
                flush()
        cur.append(w)
    flush()
    for i in range(len(groups) - 1):  # hold each phrase until the next starts (no flicker)
        groups[i]["end"] = max(groups[i]["end"], min(groups[i + 1]["start"] - 0.02, groups[i]["end"] + 0.4))
    return groups


def draw_mask_of(frame: Frame, draw: Callable[[skia.Canvas], None]) -> np.ndarray:
    """Raster coverage of arbitrary drawing (design coordinates)."""
    return rasterize_mask(frame.w, frame.h, draw, frame.scale)


def mask_of_path(frame: Frame, path: skia.Path, matrix: np.ndarray | None = None) -> np.ndarray:
    return path_mask(frame.w, frame.h, path, skia_matrix(matrix) if matrix is not None else None, frame.scale)


__all__ = [
    "PenHead", "beam", "bloom", "caption_groups", "contour_lengths", "draw_mask_of", "glow_dot", "glow_path", "ignite",
    "kinetic_captions", "light_shafts", "mask_of_path", "outside_distance", "radial_flood", "refract", "refraction_map",
    "sdf_wave", "sheen", "shockwave_ring", "text_rise", "trim_path", "write_on",
]

_ = coverage_paint
