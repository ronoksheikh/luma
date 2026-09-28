"""voiced_explainer — scenes timed to voice-over word timestamps, kinetic captions and a
logo end card.

Parameters
----------
words      list of {text, start, end} (seconds) or a path to a JSON file with
           ``{"words": [...]}`` — e.g. the word timings saved by ``el_tts``.
voice      path to the voice-over audio (wav/mp3); placed at t = ``voice_offset``.
scenes     list of {"title": str, "subtitle"?: str, "at_word"?: int, "at"?: float}.
           Each scene starts at the given word index (or time).  Default: one scene per
           sentence using the sentence's first words as the title.
music      optional music file (ducked under the voice); otherwise a soft synth pad.
logo, wordmark …  as in the other templates (end card).

The video length is extended if needed so the end card holds ≥ ``end_card`` seconds
after the last word.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import skia

from .. import audio as A
from ..brand import Color
from ..encode import words_to_cues
from ..fx import glow_dot, kinetic_captions, sheen, text_rise
from ..layers import Frame
from ..motion import progress, smoothstep
from ..scene import Scene
from ..text import shape_text
from .common import LogoKit


class VoicedExplainer(Scene):
    max_blur_samples = 6
    logo: str = ""
    wordmark: str | None = None
    font: str = "Inter"
    weight: int = 600
    tracking: float = 40.0
    background: str = "#0B0F2A"
    wordmark_color: str | None = None
    light_color: str | None = None
    words: list | str | None = None
    voice: str | None = None
    voice_offset: float = 0.3
    scenes: list | None = None
    music: str | None = None
    captions_on: bool = True
    caption_size: float = 58.0
    title_size: float = 96.0
    end_card: float = 2.2
    symbol_ids: list | None = None
    sound: bool = True

    def setup(self):
        ws = self.words
        if isinstance(ws, str):
            d = json.loads(Path(ws).read_text())
            ws = d.get("words", d) if isinstance(d, dict) else d
        ws = [dict(w) for w in (ws or []) if str(w.get("text", "")).strip()]
        for w in ws:
            w["start"] = float(w["start"]) + self.voice_offset
            w["end"] = float(w["end"]) + self.voice_offset
        self.word_list = ws
        last = ws[-1]["end"] if ws else 0.0
        need = last + self.end_card
        if need > self.duration:
            self.duration = math.ceil(need * self.fps) / self.fps
        self.timeline.duration = self.duration
        self.kit = LogoKit(self.logo, self.width, self.height, wordmark=self.wordmark, font=self.font, weight=self.weight,
                           tracking=self.tracking, symbol_ids=self.symbol_ids, wordmark_color=self.wordmark_color,
                           background=self.background) if self.logo else None
        self.light = self.light_color or (self.kit.light_color.hex if self.kit else "#FFB547")
        self.text_color = self.kit.text_color if self.kit else Color.of("#F5F1EA")
        # scenes
        scenes = list(self.scenes or [])
        if not scenes and ws:
            sent, cur = [], []
            for i, w in enumerate(ws):
                cur.append(i)
                if w["text"].rstrip().endswith((".", "!", "?")):
                    sent.append(cur)
                    cur = []
            if cur:
                sent.append(cur)
            for s in sent:
                n = len(s) if len(s) <= 6 else 4
                title = " ".join(ws[i]["text"] for i in s[:n]).rstrip(".,!?;:") + ("…" if n < len(s) else "")
                scenes.append({"title": title, "at_word": s[0]})
        self.scene_list = []
        for sc in scenes:
            if "at" in sc:
                t0 = float(sc["at"])
            elif "at_word" in sc and ws:
                t0 = ws[min(int(sc["at_word"]), len(ws) - 1)]["start"] - 0.15
            else:
                t0 = 0.0
            self.scene_list.append({**sc, "t": max(0.0, t0)})
        self.scene_list.sort(key=lambda s: s["t"])
        self.t_end = (last + 0.35) if ws else max(0.5, self.duration - self.end_card)
        self.t_hold = min(self.duration - 0.3, self.t_end + 1.3)
        self.static_after = self.t_hold
        tl = self.timeline
        for i, s in enumerate(self.scene_list):
            tl.add(f"scene_{i + 1}", s["t"], "reveal", 0.5, title=s.get("title", ""))
        for w in ws:
            tl.add("word", w["start"], "word", 0.2, text=w["text"])
        tl.add("end_card", self.t_end, "impact", 0.8)
        tl.span("end_card", self.t_end, self.duration)

    def _scene_at(self, t):
        cur = None
        for i, s in enumerate(self.scene_list):
            if s["t"] <= t:
                cur = i
        return cur

    def draw(self, f: Frame, t: float):
        W, H = self.width, self.height
        if self.kit is not None and t >= self.t_hold:
            self.kit.draw_reference(f)
            return
        f.fill(self.background)
        if t < self.t_end:
            # slowly drifting light pool for depth
            gx = W * (0.3 + 0.4 * (0.5 + 0.5 * math.sin(t * 0.35)))
            glow_dot(f, (gx, H * 0.35), 420, self.light, 0.05 * smoothstep(0, 0.6, t))
            i = self._scene_at(t)
            if i is not None:
                s = self.scene_list[i]
                nxt = self.scene_list[i + 1]["t"] if i + 1 < len(self.scene_list) else self.t_end
                out = 1 - progress(t, nxt - 0.25, nxt, "in_cubic")
                title = str(s.get("title", ""))
                if title:
                    size = self.title_size
                    run = shape_text(title, self.font, 700, size, -10)
                    if run.advance > W * 0.84:
                        size *= W * 0.84 / run.advance
                        run = shape_text(title, self.font, 700, size, -10)
                    x = W / 2 - run.advance / 2
                    y = H * 0.44
                    n = len(run.glyphs)
                    col = self.text_color.with_alpha(self.text_color.a * out)
                    text_rise(f, run, x, y, lambda j: progress(t, s["t"] + j * 0.018, s["t"] + j * 0.018 + 0.45, None), color=col, rise=0.8)
                    # accent underline that draws with the title
                    u = progress(t, s["t"] + 0.1, s["t"] + 0.1 + 0.02 * n + 0.3, "out_cubic")
                    if u > 0:
                        with f.draw() as c:
                            c.drawRoundRect(skia.Rect.MakeXYWH(W / 2 - run.advance / 2, y + size * 0.28, run.advance * u, max(4, size * 0.06)),
                                            3, 3, skia.Paint(AntiAlias=True, Color4f=Color.of(self.light).skia4f(out)))
                sub = s.get("subtitle")
                if sub:
                    r2 = shape_text(str(sub), self.font, 400, self.title_size * 0.38)
                    with f.draw() as c:
                        c.save()
                        c.translate(W / 2 - r2.advance / 2, H * 0.44 + self.title_size * 0.9)
                        a = progress(t, s["t"] + 0.3, s["t"] + 0.6) * out
                        c.drawPath(r2.path, skia.Paint(AntiAlias=True, Color4f=self.text_color.skia4f(0.75 * a)))
                        c.restore()
            if self.captions_on and self.word_list:
                kinetic_captions(f, self.word_list, t, self.font, 700, self.caption_size, center_y=H * 0.8, max_width=W * 0.78,
                                 color=self.text_color.hex, highlight=self.light)
        elif self.kit is not None:
            kit = self.kit
            a = progress(t, self.t_end, self.t_end + 0.45, "out_cubic")
            kit.draw_symbol(f, kit.lockup.symbol, a)
            run = kit.sized_run()
            if run is not None:
                x, y, _ = kit.wordmark_origin()
                text_rise(f, run, x, y, lambda j: progress(t, self.t_end + 0.2 + j * 0.04, self.t_end + 0.7 + j * 0.04), color=kit.text_color)
            else:
                kit.draw_wordmark(f, a)
            ts0 = self.t_end + 0.7
            if ts0 < t < self.t_hold:
                sheen(f, kit.lockup_mask(f), progress(t, ts0, self.t_hold), 18, 70, "#FFFFFF", 0.45)

    def bloom_at(self, t):
        return None if t >= self.t_hold else {"threshold": 0.9, "intensity": 0.4, "levels": 6}

    def captions(self):
        return words_to_cues(self.word_list) if self.word_list else None

    def audio(self):
        self.ensure_setup()
        mix = A.Mix(self.duration)
        if self.voice:
            v = A.read_audio(self.voice)
            mix.place(v, self.voice_offset, 0.0, bus="voice", name="voice")
        if self.music:
            m = A.read_audio(self.music)
            m = A.fade(A.fit_length(m, self.duration), 0.5, 1.5)
            mix.place(m, 0.0, -14.0, bus="music", name="music")
        elif self.sound:
            base = 146.83  # D3 pad
            pad = sum(A.osc(base * r, self.duration, "saw") * g for r, g in ((1, 0.3), (1.5, 0.2), (2, 0.15), (2.52, 0.1)))
            pad = A.fade(A.lowpass(pad, 700.0), 1.0, 1.5)
            mix.place(A.to_stereo(pad), 0.0, -24.0, bus="music", name="pad")
        if self.sound:
            for s in self.scene_list:
                mix.place(A.whoosh(0.45, 500, 2600, True, 1.3, seed=int(s["t"] * 10), peak_at=0.8), s["t"] + 0.05, -22, end_aligned=True, name="scene")
            mix.place(A.glass_ping(1318.5, 2.5, 0.5, seed=3), self.t_end + 0.05, -12, name="end card")
            mix.place(A.sub_boom(1.6, 60, 30), self.t_end + 0.05, -10, name="end boom")
        mix.reverb = {"wet": 0.12, "dur": 2.0}
        return mix.render(duck={"depth_db": -12.0})


def build(**kw) -> VoicedExplainer:
    return VoicedExplainer(**kw)


_ = np
