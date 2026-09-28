"""stroke_reveal — universal write-on → fill → gradient wipe → lockup.

Works with any SVG: every symbol shape's outline is written on (sequentially along the
total length, with a glowing pen tip), fills fade in behind a soft directional wipe,
then the mark glides into the lockup while the wordmark rises, and a sheen passes.
"""
from __future__ import annotations

import math

import numpy as np
import skia

from .. import audio as A
from ..brand import Color, skia_matrix
from ..fx import glow_path, sheen, text_rise, write_on
from ..layers import Frame
from ..motion import get_ease, progress
from ..scene import Scene
from ..motion import Timeline
from .common import fit_schedule, PENTATONIC, LogoKit

NATURAL = 3.6


class StrokeReveal(Scene):
    max_blur_samples = 8
    logo: str = ""
    wordmark: str | None = None
    font: str = "Inter"
    weight: int = 600
    tracking: float = 40.0
    background: str = "#0B0F2A"
    wordmark_color: str | None = None
    light_color: str | None = None
    symbol_ids: list | None = None
    stroke_width: float = 2.5
    wipe_angle: float = 20.0
    sound: bool = True

    def setup(self):
        self.kit = LogoKit(self.logo, self.width, self.height, wordmark=self.wordmark, font=self.font, weight=self.weight,
                           tracking=self.tracking, symbol_ids=self.symbol_ids, wordmark_color=self.wordmark_color,
                           background=self.background)
        kit = self.kit
        self.light = self.light_color or kit.light_color.hex
        fit_schedule(self, NATURAL)

    def _schedule(self, k: float) -> None:
        """All timings for time-scale k (re-run by fit_schedule until the hold fits)."""
        self.k = k
        self.timeline = Timeline(self.duration, self.fps)
        kit = self.kit
        _W, _H = self.width, self.height
        tl = self.timeline
        self.t_write = 0.2 * k
        self.write_dur = 1.3 * k
        self.t_fill = self.t_write + 0.9 * k
        self.fill_dur = 0.7 * k
        self.t_glide = self.t_fill + self.fill_dur + 0.1 * k
        self.glide_dur = 0.7 * k
        n = len(kit.run.glyphs) if kit.run is not None else 0
        self.t_letters = self.t_glide + 0.3 * k
        self.ls = 0.04 * k
        letters_end = self.t_letters + max(n - 1, 0) * self.ls + 0.5 * k
        self.t_sheen = max(self.t_glide + self.glide_dur, letters_end if n else 0)
        self.t_hold = self.t_sheen + 0.5 * k
        self.static_after = self.t_hold
        tl.span("write", self.t_write, self.t_write + self.write_dur)
        tl.add("fill", self.t_fill, "reveal", 0.6)
        tl.add("filled", self.t_fill + self.fill_dur, "impact", 0.7)
        tl.span("glide", self.t_glide, self.t_glide + self.glide_dur)
        tl.span("sheen", self.t_sheen, self.t_hold)
        # combined outline (all symbol shapes) for a sequential write-on
        self.outline = skia.Path()
        for s in kit.symbol_shapes:
            self.outline.addPath(s.outline_path())
        self._mask = {}

    def draw(self, f: Frame, t: float):
        kit, k = self.kit, self.k
        if t >= self.t_hold:
            kit.draw_reference(f)
            return
        f.fill(self.background)
        M = kit.glide_matrix(progress(t, self.t_glide, self.t_glide + self.glide_dur, "cinematic"))
        # directional fill wipe (soft edge) across the symbol's bbox
        wp = progress(t, self.t_fill, self.t_fill + self.fill_dur, "in_out_cubic")
        if wp > 0:
            with f.draw(M) as c:
                if wp >= 1:
                    for s in kit.symbol_shapes:
                        s.draw(c)
                else:
                    x0, y0, x1, y1 = kit.symbol_bounds
                    a = math.radians(self.wipe_angle)
                    d = np.array([math.cos(a), math.sin(a)])
                    corners = np.array([[x0, y0], [x1, y0], [x0, y1], [x1, y1]])
                    proj = corners @ d
                    lo, hi = proj.min(), proj.max()
                    soft = (hi - lo) * 0.25
                    pos = lo - soft + (hi - lo + 2 * soft) * wp
                    p0 = d * (pos - soft)
                    p1 = d * pos
                    c.saveLayer()
                    for s in kit.symbol_shapes:
                        s.draw(c)
                    sh = skia.GradientShader.MakeLinear([skia.Point(*p0), skia.Point(*p1)], [skia.ColorWHITE, skia.ColorTRANSPARENT])
                    c.drawPaint(skia.Paint(Shader=sh, BlendMode=skia.BlendMode.kDstIn))
                    c.restore()
            # glow along the wipe edge region (fading)
            glow_path(f, self.outline, self.light, 0.8 * (1 - wp), blur=10, matrix=M)
        # write-on outline
        w = get_ease("in_out_sine")(progress(t, self.t_write, self.t_write + self.write_dur))
        fade = 1 - progress(t, self.t_fill + 0.2 * k, self.t_fill + self.fill_dur)
        if w > 0 and fade > 0:
            col = Color.of(self.light).with_alpha(fade)
            write_on(f, self.outline, w, width=self.stroke_width, color=col, matrix=M, mode="sequential",
                     pen_color=self.light, pen_intensity=5 * fade, pen_radius=4.5, glow_intensity=1.1 * fade)
        run = kit.sized_run()
        if run is not None and t >= self.t_letters:
            x, y, _ = kit.wordmark_origin()
            text_rise(f, run, x, y, lambda j: progress(t, self.t_letters + j * self.ls, self.t_letters + j * self.ls + 0.5 * k),
                      color=kit.text_color)
        elif kit.lockup.wordmark is not None and kit.run is None and t >= self.t_letters:
            kit.draw_wordmark(f, progress(t, self.t_letters, self.t_letters + 0.5 * k))
        if self.t_sheen < t < self.t_hold:
            key = (f.w, f.h)
            if key not in self._mask:
                self._mask[key] = kit.lockup_mask(f)
            sheen(f, self._mask[key], progress(t, self.t_sheen, self.t_hold, "in_out_sine"), 18, 70, "#FFFFFF", 0.5)

    def bloom_at(self, t):
        return None if t >= self.t_hold else {"threshold": 0.85, "intensity": 0.5, "levels": 6}

    def audio(self):
        if not self.sound:
            return None
        self.ensure_setup()
        k = self.k
        mix = A.Mix(self.duration)
        # pen: a soft continuous scratch/air texture during the write-on
        dur = self.write_dur
        scratch = A.highpass(A.noise(dur, "pink", seed=3), 2500) * A.env_adsr(A.samples(dur), 0.1, 0.2, 0.7, 0.3)
        mix.place(A.pan_stereo(scratch, A.pan_path(len(scratch), [(0, -0.5), (1, 0.5)])), self.t_write, -24, name="pen")
        mix.place(A.whoosh(self.fill_dur, 400, 3000, True, 1.2, seed=5, peak_at=0.9), self.t_fill + self.fill_dur, -14, end_aligned=True, name="wipe")
        mix.place(A.glass_ping(PENTATONIC[2], 2.4, 0.5, seed=8), self.t_fill + self.fill_dur, -9, name="filled")
        mix.place(A.thump(0.35, 120, 50), self.t_fill + self.fill_dur, -10, name="filled thump")
        mix.place(A.whoosh(self.glide_dur + 0.2, 300, 1600, True, 1.2, seed=9, peak_at=0.55), self.t_glide - 0.1, -16, name="glide")
        mix.place(A.shimmer(0.5 * k + 0.3, 4000, 40, seed=17), self.t_sheen, -22, name="sheen")
        return mix.render()


def build(**kw) -> StrokeReveal:
    return StrokeReveal(**kw)


_ = skia_matrix
