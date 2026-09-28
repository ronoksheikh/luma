"""fan_unfold — "light-born fan unfold" brand construction.

Choreography (at 5 s; scaled to other durations):

  0.00  thread of light across the frame collapses to a seed at the logo's hinge
  0.45  seed flares; beams mark each part's final angle
  0.55  parts swing open around the hinge on damped springs (staggered);
        their outlines write on with glowing pen tips
  seat  each part seats (spring first-crossing) → fill ignites in the brand gradient
  catch the whole mark does a small damped "catch"
  shock a shockwave ring floods the frame with the brand background (with refraction)
  glide the symbol glides into the lockup; letters rise from a mask
  sheen a sheen sweeps across the lockup; then an exact hold on the reference lockup

Works with any SVG: multi-shape symbols use their shapes as parts; a single-shape
symbol is split radially around its detected pivot (the final frame always uses the
original paths, so there are no seams).
"""
from __future__ import annotations

import math

import numpy as np

from .. import audio as A
from ..fx import beam, glow_dot, ignite, radial_flood, refract, sheen, shockwave_ring, text_rise, write_on
from ..layers import Frame
from ..motion import Spring, decay, get_ease, progress, smoothstep
from ..scene import Scene
from .common import PENTATONIC, LogoKit, ease_time_scale, pan_for_x, seat_hit

NATURAL = 3.55  # seconds of choreography before the hold at 1× speed


class FanUnfold(Scene):
    first_frame_black = True
    max_blur_samples = 8

    # parameters (override via constructor kwargs)
    logo: str = ""
    wordmark: str | None = None
    font: str = "Inter"
    weight: int = 600
    tracking: float = 40.0
    background: str = "#0B0F2A"
    wordmark_color: str | None = None
    light_color: str | None = None
    symbol_ids: list | None = None
    spring_freq: float = 2.1
    spring_damping: float = 0.42
    stagger: float = 0.1
    sound: bool = True

    def setup(self):
        W, H = self.width, self.height
        self.kit = LogoKit(self.logo, W, H, wordmark=self.wordmark, font=self.font, weight=self.weight,
                           tracking=self.tracking, symbol_ids=self.symbol_ids, wordmark_color=self.wordmark_color,
                           background=self.background)
        kit = self.kit
        self.light = self.light_color or kit.light_color.hex
        k = self.k = ease_time_scale(self.duration, NATURAL)
        tl = self.timeline
        tl.duration, tl.fps = self.duration, self.fps
        self.t_thread = 0.0
        self.t_seed = 0.45 * k
        tl.add("seed", self.t_seed, "impact", 0.6)
        blades = kit.blades
        # fold: all blades start stacked at the mean angle (circular mean)
        if blades:
            ang = np.radians([b.angle for b in blades])
            self.fold_angle = math.degrees(math.atan2(np.mean(np.sin(ang)), np.mean(np.cos(ang))))
        else:
            self.fold_angle = -90.0
        self.springs = []
        self.u_start, self.seat, self.beam_t = [], [], []
        for i, b in enumerate(blades):
            sp = Spring(self.spring_freq / k ** 0.5, self.spring_damping)
            u0 = 0.55 * k + max(self.stagger * k, 0.095) * i  # keep seats audibly separate
            seat = u0 + (sp.first_crossing_time() or 0.25)
            self.springs.append(sp)
            self.u_start.append(u0)
            self.seat.append(seat)
            self.beam_t.append(self.t_seed + 0.05 * i * k)
            tl.add(f"seat_{i + 1}", seat, "seat", 0.8, part=i)
        last = max(self.seat) if self.seat else 0.9 * k
        self.t_catch = last + 0.14 * k
        self.t_shock = self.t_catch + 0.1 * k
        self.t_glide = self.t_shock + 0.5 * k
        self.glide_dur = 0.75 * k
        n_letters = len(kit.run.glyphs) if kit.run is not None else 0
        self.t_letters = self.t_glide + 0.35 * k
        self.letter_stagger = 0.045 * k
        letters_end = self.t_letters + (n_letters - 1) * self.letter_stagger + 0.5 * k if n_letters else self.t_glide + self.glide_dur
        self.t_sheen = max(self.t_glide + self.glide_dur, letters_end) + 0.02 * k
        self.sheen_dur = 0.55 * k
        self.t_hold = self.t_sheen + self.sheen_dur
        tl.add("catch", self.t_catch, "impact", 0.7)
        tl.add("shock", self.t_shock, "boom", 1.0)
        tl.span("glide", self.t_glide, self.t_glide + self.glide_dur)
        tl.span("letters", self.t_letters, letters_end)
        tl.span("sheen", self.t_sheen, self.t_hold)
        tl.span("hold", self.t_hold, self.duration)
        self.static_after = self.t_hold
        W2 = max(math.hypot(W, H), 1)
        self.far = W2
        self._mask = {}
        self._ref = {}

    # ------------------------------------------------------------------------------------
    def symbol_matrix(self, t: float) -> np.ndarray:
        kit = self.kit
        u = progress(t, self.t_glide, self.t_glide + self.glide_dur, "cinematic")
        M = kit.glide_matrix(u)
        # catch: a small damped squash about the pivot
        du = t - self.t_catch
        if 0 < du < 0.9 * self.k:
            s = 1.0 - 0.045 * math.exp(-du / (0.12 * self.k)) * math.sin(du / self.k * 2 * math.pi * 3.0)
            from ..svg import scale as m_scale

            M = M @ m_scale(s, s, *kit.pivot)
        return M

    def draw(self, f: Frame, t: float):
        if t >= self.t_hold:
            self.kit.draw_reference(f)
            return
        kit = self.kit
        k = self.k
        W, H = self.width, self.height
        M = self.symbol_matrix(t)
        piv = kit.to_frame(M, kit.pivot)

        # ---- background: black, flooded by the brand colour behind the shockwave ------
        f.fill("#000000")
        ts = t - self.t_shock
        if ts > 0:
            far = max(math.hypot(piv[0] - x, piv[1] - y) for x in (0, W) for y in (0, H))
            flood_dur = 0.8 * k
            r = (far + 80) * get_ease("out_cubic")(min(ts / flood_dur, 1.0))
            radial_flood(f, piv, r - 24, self.background, softness=60)
            if ts < flood_dur:
                refract(f, piv, r, width=40, strength=10 * (1 - ts / flood_dur))
                shockwave_ring(f, piv, r, width=22, color=self.light, intensity=2.2 * (1 - ts / flood_dur) ** 1.5, blur=14)

        # ---- thread of light collapsing to the seed ------------------------------------
        if t < self.t_seed + 0.2 * k:
            p = progress(t, 0.0, self.t_seed, "in_cubic")
            fade_in = smoothstep(0.03, 0.18 * k, t)
            fade_out = 1 - progress(t, self.t_seed, self.t_seed + 0.2 * k)
            half = (W * 0.62) * (1 - p)
            inten = (1.2 + 3.5 * p) * fade_in * fade_out
            if half > 2 and inten > 0:
                for d in (-1, 1):
                    beam(f, piv, (piv[0] + d * half, piv[1]), width=1.4, color=self.light, intensity=inten, falloff=0.6, fade_in=0.0, halo=10, halo_intensity=0.15)

        # ---- seed ----------------------------------------------------------------------
        seed_i = 5.0 * smoothstep(0.05, self.t_seed, t) * (1 - progress(t, self.t_seed + 0.15 * k, self.t_seed + 1.0 * k))
        seed_i += 9.0 * decay(t, self.t_seed, 0.09 * k)
        if seed_i > 0.01:
            glow_dot(f, piv, 7 + 5 * decay(t, self.t_seed, 0.2 * k), self.light, seed_i)

        # ---- beams along each part's final angle ---------------------------------------
        for i, b in enumerate(kit.blades):
            tb = self.beam_t[i]
            if t < tb or t > tb + 1.0 * k:
                continue
            L = b.radius * M[0, 0] * 1.5 * get_ease("out_cubic")(progress(t, tb, tb + 0.35 * k))
            inten = 3.2 * (1 - progress(t, tb + 0.2 * k, tb + 0.8 * k))
            a = math.radians(b.angle)
            end = (piv[0] + math.cos(a) * L, piv[1] + math.sin(a) * L)
            if L > 1 and inten > 0:
                beam(f, piv, end, width=1.6, color=self.light, intensity=inten, falloff=1.2, fade_in=0.04, halo=8)

        # ---- hubs (rivets) pop at the seed ---------------------------------------------
        for h in kit.hubs:
            sp = Spring(3.0, 0.5)
            s = sp.at(t, self.t_seed + 0.05 * k, 0.0, 1.0)
            if s > 0:
                Mh = kit.part_matrix(h, M, 0.0, max(s, 1e-4))
                ignite(f, h.path, h.fill_paint(), 1.0, matrix=Mh, flash=4.0 * decay(t, self.t_seed + 0.05 * k, 0.12 * k), flash_color=self.light)

        # ---- blades: spring unfold, write-on, ignition -----------------------------------
        for i, b in enumerate(kit.blades):
            u0 = self.u_start[i]
            if t < u0:
                continue
            delta = b.angle - self.fold_angle
            s = self.springs[i].at(t, u0, 0.0, 1.0)
            rot = -delta * (1 - s)
            Mp = kit.part_matrix(b, M, rot)
            seat = self.seat[i]
            # fill ignition at seat
            ig = progress(t, seat, seat + 0.32 * k, "out_cubic")
            flash = 1.1 * decay(t, seat, 0.09 * k) if t >= seat else 0.0
            if ig > 0 or flash > 0:
                ignite(f, b.path, b.fill_paint(), ig, origin=piv, matrix=Mp, flash=flash, flash_color=self.light, flash_blur=9)
            # outline write-on with pen tip; fades once the fill is in
            w = get_ease("out_quad")(progress(t, u0, u0 + 0.62 * k))
            out_a = 1 - progress(t, seat + 0.12 * k, seat + 0.42 * k)
            if w > 0 and out_a > 0:
                col = kit.accent.mix(self.light, 0.5).with_alpha(out_a)
                write_on(f, b.path, w, width=2.0, color=col, matrix=Mp, mode="parallel", pen_color=self.light,
                         pen_intensity=4.5 * out_a, pen_radius=4.0, glow_intensity=1.0 * out_a, tail=0.22)

        # ---- wordmark rises from a mask --------------------------------------------------
        run = kit.sized_run()
        if run is not None and t >= self.t_letters:
            x, y, _ = kit.wordmark_origin()
            text_rise(f, run, x, y, lambda j: progress(t, self.t_letters + j * self.letter_stagger, self.t_letters + j * self.letter_stagger + 0.5 * self.k),
                      color=kit.text_color, rise=0.95, ease="out_cubic")
        elif kit.lockup.wordmark is not None and kit.run is None and t >= self.t_letters:
            kit.draw_wordmark(f, progress(t, self.t_letters, self.t_letters + 0.5 * k))

        # ---- sheen across the lockup -----------------------------------------------------
        if self.t_sheen < t < self.t_hold:
            key = (f.w, f.h)
            if key not in self._mask:
                self._mask[key] = kit.lockup_mask(f)
            sheen(f, self._mask[key], progress(t, self.t_sheen, self.t_hold, "in_out_sine"), angle_deg=18, width=70,
                  color="#FFFFFF", intensity=0.55)

    def bloom_at(self, t):
        if t >= self.t_hold:
            return None
        return {"threshold": 0.85, "knee": 0.5, "intensity": 0.6, "levels": 7}

    def reference_frame(self, scale: float = 1.0):
        return self.render(self.t_hold + 1e-6 if self.t_hold < self.duration else self.duration, scale, blur=False)

    # ------------------------------------------------------------------------------------
    def audio(self):
        if not self.sound:
            return None
        self.ensure_setup()
        kit, k, W = self.kit, self.k, self.width
        mix = A.Mix(self.duration)
        piv_stage = kit.to_frame(kit.stage.symbol, kit.pivot)
        # thread → seed: a rising airy riser landing on the seed, plus a high ping
        mix.place(A.riser(self.t_seed - 0.02, 400, 3000, seed=3), self.t_seed, -20, end_aligned=True, name="thread")
        mix.place(A.glass_ping(PENTATONIC[5], 2.2, 0.55, seed=11), self.t_seed, -9, name="seed ping")
        mix.place(A.click(0.03, 6000, seed=12), self.t_seed, -12, name="seed tick")
        mix.place(A.shimmer(1.0 * k, 3000, 30, seed=5), self.t_seed, -24, name="seed shimmer")
        # beams: short whooshes panned along their direction
        for i, b in enumerate(kit.blades):
            a = math.radians(b.angle)
            pan = float(np.clip(math.cos(a), -1, 1)) * 0.8
            mix.place(A.whoosh(0.35 * k, 900, 4200, True, 1.6, seed=20 + i, peak_at=0.6), self.beam_t[i] + 0.2 * k, -24,
                      pan_path=[(0, pan * 0.3), (1, pan)], anchor=0.21 * k, name=f"beam {i + 1}")
        # seats: clack + glass ping per blade (pentatonic, panned by angle)
        for i, b in enumerate(kit.blades):
            a = math.radians(b.angle)
            x = piv_stage[0] + math.cos(a) * b.radius * kit.stage.symbol[0, 0]
            mix.place(seat_hit(PENTATONIC[i % len(PENTATONIC)], seed=40 + i), self.seat[i], -9, pan=pan_for_x(x, W), name=f"seat {i + 1}")
            mix.place(A.fm_bell(PENTATONIC[i % 5] / 2, 1.6, 3.5, 2.0, 0.5), self.seat[i] + 0.01, -26, pan=pan_for_x(x, W) * 0.5, name=f"ignite {i + 1}")
        # catch + shock
        mix.place(A.thump(0.4, 140, 50), self.t_catch, -6, name="catch")
        mix.place(A.sub_boom(2.4, 72, 27), self.t_shock, -3, name="shock boom")
        mix.place(A.whoosh(1.1 * k, 200, 5000, False, 0.9, seed=7, peak_at=0.08), self.t_shock, -12, anchor=0.08 * 1.1 * k, name="shock air")
        mix.place(A.glass_ping(PENTATONIC[3] / 2, 3.0, 0.4, seed=13), self.t_shock, -16, name="shock glass")
        # glide + letters + sheen
        mix.place(A.whoosh(self.glide_dur + 0.2, 300, 1800, True, 1.2, seed=9, peak_at=0.55), self.t_glide - 0.1, -16,
                  pan_path=[(0, 0.2), (1, -0.4)], name="glide")
        if kit.run is not None:
            for j in range(len(kit.run.glyphs)):
                if kit.run.glyphs[j].char.strip():
                    tj = self.t_letters + j * self.letter_stagger + 0.18 * k
                    mix.place(A.click(0.025, 5200 + 300 * (j % 4), seed=60 + j), tj, -30, pan=-0.2 + 0.6 * j / max(1, len(kit.run.glyphs)), name="letter")
        mix.place(A.shimmer(self.sheen_dur + 0.4, 4200, 45, seed=17), self.t_sheen, -22, name="sheen")
        # a soft pad bed under the whole piece, swelling into the hold
        bed = A.osc(110.0, self.duration, "saw") * 0.2 + A.osc(164.81, self.duration, "saw") * 0.15 + A.osc(220.0, self.duration, "triangle") * 0.2
        bed = A.lowpass(bed, 900.0)
        env = np.clip((np.arange(len(bed)) / A.SR - self.t_shock) / 0.6, 0, 1) ** 1.5
        bed = A.fade(bed * env, 0, 0.8)
        mix.place(A.to_stereo(bed), 0.0, -30, bus="music", name="pad")
        mix.reverb = {"wet": 0.22, "dur": 2.6}
        self.mix_plan = mix.to_dict()
        return mix.render()


def build(**kw) -> FanUnfold:
    return FanUnfold(**kw)
