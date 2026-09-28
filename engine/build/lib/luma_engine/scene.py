"""Scene base class.

A scene is a pure function of time.  Subclasses implement :meth:`Scene.draw` (and
optionally :meth:`Scene.audio` / :meth:`Scene.captions`); everything else — scaling for
previews, motion blur, reference frames — is provided here.

Minimal scene file (``work/scene.py``)::

    from luma_engine import Scene, Frame
    from luma_engine.fx import glow_dot

    class Hello(Scene):
        def draw(self, f: Frame, t: float):
            f.fill("#0B0F2A")
            glow_dot(f, (960, 540), 20 + 200 * t, "#FFB547", 3.0)

    scene = Hello(width=1920, height=1080, fps=60, duration=3)
"""
from __future__ import annotations

import numpy as np

from .brand import linear_to_srgb
from .layers import Frame, motion_blur
from .motion import Timeline


class Scene:
    width: int = 1920
    height: int = 1080
    fps: float = 60.0
    duration: float = 5.0
    background = "#000000"
    shutter_deg: float = 180.0
    max_blur_samples: int = 12
    bloom: dict | None = {"threshold": 0.9, "knee": 0.5, "intensity": 0.55, "levels": 6}
    first_frame_black: bool = False

    def __init__(self, width: int | None = None, height: int | None = None, fps: float | None = None,
                 duration: float | None = None, **params):
        if width:
            self.width = int(width)
        if height:
            self.height = int(height)
        if fps:
            self.fps = float(fps)
        if duration:
            self.duration = float(duration)
        self.params = params
        for k, v in params.items():
            setattr(self, k, v)
        self.timeline = Timeline(self.duration, self.fps)
        self._ready = False

    # -- lifecycle ---------------------------------------------------------------------
    def setup(self) -> None:
        """Precompute geometry and fill ``self.timeline``. Called once per process."""

    def ensure_setup(self) -> None:
        if not self._ready:
            self.setup()
            self._ready = True

    # -- to implement ------------------------------------------------------------------
    def draw(self, frame: Frame, t: float) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def audio(self):
        """Return a stereo float array at 48 kHz (sound design synced to the timeline)
        or None."""
        return None

    def captions(self):
        """Return a list of :class:`luma_engine.encode.Cue` (for SRT) or None."""
        return None

    def bloom_at(self, t: float) -> dict | None:
        return self.bloom

    # -- rendering ---------------------------------------------------------------------
    @property
    def frame_count(self) -> int:
        return int(round(self.duration * self.fps))

    def time_of(self, i: int) -> float:
        return i / self.fps

    def new_frame(self, scale: float = 1.0) -> Frame:
        return Frame(self.width, self.height, scale, self.background)

    static_after: float | None = None  # frames at t ≥ this are identical (cached)

    def render_linear(self, t: float, scale: float = 1.0) -> np.ndarray:
        self.ensure_setup()
        sa = self.static_after
        if sa is not None and t >= sa:
            cache = self.__dict__.setdefault("_static_cache", {})
            if scale not in cache:
                f = self.new_frame(scale)
                self.draw(f, max(t, sa))
                cache[scale] = f.resolve_linear(bloom=self.bloom_at(max(t, sa)))
            return cache[scale]
        f = self.new_frame(scale)
        self.draw(f, t)
        return f.resolve_linear(bloom=self.bloom_at(t))

    def render(self, t: float, scale: float = 1.0, blur: bool = True) -> np.ndarray:
        """sRGB float32 (H, W, 3).  ``blur`` enables real temporal motion blur."""
        self.ensure_setup()
        if blur and self.shutter_deg > 0:
            lin, _ = motion_blur(lambda tt: self.render_linear(min(max(tt, 0.0), self.duration), scale), t, self.fps,
                                 self.shutter_deg, self.max_blur_samples)
        else:
            lin = self.render_linear(t, scale)
        return np.clip(linear_to_srgb(lin), 0.0, 1.0)

    def render_frame(self, i: int, scale: float = 1.0, blur: bool = True) -> np.ndarray:
        return self.render(self.time_of(i), scale, blur)

    def reference_frame(self, scale: float = 1.0) -> np.ndarray:
        """The final design (end card). Default: the last frame without motion blur."""
        return self.render(self.time_of(self.frame_count - 1), scale, blur=False)

    def describe(self) -> dict:
        self.ensure_setup()
        return {
            "class": type(self).__name__,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "duration": self.duration,
            "frames": self.frame_count,
            "timeline": self.timeline.to_dict(),
        }
