"""exploded_assembly — 3D exploded view that collapses into the logo.

The symbol's parts float apart along a shared 3D axis under a perspective camera (with
depth-of-field bins), the camera orbits back to front-on while parts collapse along the
axis on staggered springs, the assembled silhouette emits distance-field waves, and the
mark glides into the lockup.  Works with any SVG (single shapes are split radially).
"""
from __future__ import annotations

import math

import numpy as np
import skia

from .. import audio as A
from ..brand import skia_matrix
from ..camera import Camera, euler
from ..fx import glow_dot, sdf_wave, text_rise
from ..layers import Frame, dof_composite, new_surface, read_surface
from ..motion import Spring, decay, progress
from ..scene import Scene
from .common import PENTATONIC, LogoKit, ease_time_scale, seat_hit

NATURAL = 3.7


class ExplodedAssembly(Scene):
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
    spacing: float = 220.0  # design px between exploded parts along the axis
    axis: tuple = (0.45, -0.25, 1.0)
    orbit: tuple = (16.0, -30.0, 0.0)  # starting camera-relative rotation (deg)
    dof: bool = True
    aperture: float = 60000.0
    sound: bool = True

    def setup(self):
        self.kit = LogoKit(self.logo, self.width, self.height, wordmark=self.wordmark, font=self.font, weight=self.weight,
                           tracking=self.tracking, symbol_ids=self.symbol_ids, wordmark_color=self.wordmark_color,
                           background=self.background)
        kit = self.kit
        self.light = self.light_color or kit.light_color.hex
        k = self.k = ease_time_scale(self.duration, NATURAL)
        self.cam = Camera(self.width, self.height)
        parts = sorted(kit.parts, key=lambda p: (p.is_hub, p.order))
        self.parts = parts
        n = len(parts)
        ax = np.asarray(self.axis, dtype=np.float64)
        self.ax = ax / np.linalg.norm(ax)
        self.slot = [(i - (n - 1) / 2) for i in range(n)]
        self.springs = [Spring(1.6 / k ** 0.5, 0.55) for _ in parts]
        self.c_start = [0.45 * k + 0.09 * k * i for i in range(n)]
        self.seats = [c + (s.first_crossing_time() or 0.3) for c, s in zip(self.c_start, self.springs)]
        tl = self.timeline
        for i, s in enumerate(self.seats):
            tl.add(f"seat_{i + 1}", s, "seat", 0.7)
        self.t_lock = max(self.seats) + 0.12 * k
        tl.add("lock", self.t_lock, "impact", 1.0)
        self.t_glide = self.t_lock + 0.55 * k
        self.glide_dur = 0.7 * k
        nl = len(kit.run.glyphs) if kit.run is not None else 0
        self.t_letters = self.t_glide + 0.3 * k
        self.ls = 0.04 * k
        self.t_hold = max(self.t_glide + self.glide_dur, self.t_letters + max(nl - 1, 0) * self.ls + 0.5 * k if nl else 0) + 0.15 * k
        self.static_after = self.t_hold
        tl.span("orbit", 0.0, self.t_lock)
        tl.span("glide", self.t_glide, self.t_glide + self.glide_dur)
        tl.span("hold", self.t_hold, self.duration)
        self._dist = {}

    def _assembly(self, t: float) -> float:
        return progress(t, 0.0, self.t_lock, "in_out_cubic")

    def draw(self, f: Frame, t: float):
        kit, k = self.kit, self.k
        if t >= self.t_hold:
            kit.draw_reference(f)
            return
        f.fill(self.background)
        M = kit.glide_matrix(progress(t, self.t_glide, self.t_glide + self.glide_dur, "cinematic"))
        fade_in = progress(t, 0.0, 0.35 * k, "out_cubic")
        if t < self.t_lock:
            a = self._assembly(t)
            R = euler(*(np.asarray(self.orbit) * (1 - a)))
            C = np.array([*kit.to_frame(M, ((kit.symbol_bounds[0] + kit.symbol_bounds[2]) / 2, (kit.symbol_bounds[1] + kit.symbol_bounds[3]) / 2)), 0.0])
            layers = []
            for i, p in enumerate(self.parts):
                sp = self.springs[i].at(t, self.c_start[i], 0.0, 1.0)
                off = self.ax * self.spacing * self.slot[i] * (1 - sp) * 1.0
                o = C - R @ C + off
                H = self.cam.plane_homography(o, R[:, 0], R[:, 1]) @ M
                depth = float(self.cam.to_camera(np.array([C + off]))[0, 2])
                alpha = fade_in
                if self.dof and f.scale >= 0.2:
                    surf = new_surface(f.w, f.h)
                    c = surf.getCanvas()
                    c.clear(skia.Color4f(0, 0, 0, 0))
                    c.scale(f.scale, f.scale)
                    c.concat(skia_matrix(H))
                    c.drawPath(p.path, p.fill_paint(alpha))
                    layers.append((read_surface(surf), depth))
                else:
                    with f.draw(H) as c:
                        c.drawPath(p.path, p.fill_paint(alpha))
                # a small light at each part's leading edge while it travels
                travel = (1 - sp)
                if 0.02 < travel < 0.98 and t >= self.c_start[i]:
                    cc = kit.to_frame(H, p.centroid)
                    glow_dot(f, cc, 6, self.light, 1.6 * travel * fade_in)
            if layers:
                focus = float(np.median([d for _, d in layers]))
                base = f.base()
                out = dof_composite(layers, focus, self.aperture * (1 - self._assembly(t)), max_blur=14 * f.scale, bins=5, background=base)
                f.set_base(out)
            for i, s in enumerate(self.seats):
                if t >= s:
                    glow_dot(f, kit.to_frame(M, self.parts[i].centroid), 10, self.light, 2.5 * decay(t, s, 0.08 * k))
        else:
            kit.draw_symbol(f, M)
            # silhouette waves radiating from the assembled mark
            tw = t - self.t_lock
            if tw < 1.0 * k:
                key = (f.w, f.h, tuple(np.round(M.ravel(), 3)))
                if key not in self._dist:
                    from ..fx import mask_of_path, outside_distance

                    mask = mask_of_path(f, kit.doc.union_path(kit.symbol_shapes), M)
                    self._dist = {key: outside_distance(mask)}
                d = self._dist[key]
                for j in range(3):
                    r = (tw - 0.12 * k * j) * 520 / k
                    if r > 0:
                        sdf_wave(f, None, r, 8 + r * 0.03, self.light, 0.85 * math.exp(-r / 300) * (1 - j * 0.25), dist=d)
                glow_dot(f, kit.to_frame(M, kit.pivot), 14, self.light, 3.0 * decay(t, self.t_lock, 0.1 * k))
        run = kit.sized_run()
        if run is not None and t >= self.t_letters:
            x, y, _ = kit.wordmark_origin()
            text_rise(f, run, x, y, lambda j: progress(t, self.t_letters + j * self.ls, self.t_letters + j * self.ls + 0.5 * k), color=kit.text_color)
        elif kit.lockup.wordmark is not None and kit.run is None and t >= self.t_letters:
            kit.draw_wordmark(f, progress(t, self.t_letters, self.t_letters + 0.5 * k))

    def bloom_at(self, t):
        return None if t >= self.t_hold else {"threshold": 0.85, "intensity": 0.55, "levels": 6}

    def audio(self):
        if not self.sound:
            return None
        self.ensure_setup()
        k = self.k
        mix = A.Mix(self.duration)
        mix.place(A.granular(A.noise(1.0, "pink", seed=2), self.t_lock, 0.08, 40, seed=4), 0.0, -26, name="drift texture")
        for i, s in enumerate(self.seats):
            mix.place(seat_hit(PENTATONIC[i % 5] / 2, seed=30 + i), s, -10, pan=(i / max(1, len(self.seats) - 1) - 0.5) * 0.8, name=f"seat {i + 1}")
        mix.place(A.sub_boom(2.0, 65, 28), self.t_lock, -4, name="lock boom")
        mix.place(A.clack(0.2, 500, seed=9), self.t_lock, -6, name="lock clack")
        mix.place(A.shimmer(1.0 * k, 2800, 35, seed=6), self.t_lock + 0.05, -20, name="waves")
        mix.place(A.whoosh(self.glide_dur + 0.2, 300, 1600, True, 1.2, seed=9, peak_at=0.55), self.t_glide - 0.1, -16, name="glide")
        return mix.render()


def build(**kw) -> ExplodedAssembly:
    return ExplodedAssembly(**kw)
