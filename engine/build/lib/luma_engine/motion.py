"""Motion primitives: easings, closed-form damped springs, Hermite keyframe channels,
staggers and a timeline of named events shared by picture and sound.

All functions are pure functions of time, so any frame can be rendered independently.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from typing import Callable, Iterable, Sequence

import numpy as np

# ======================================================================================
# scalar helpers
# ======================================================================================


def clamp(x, lo=0.0, hi=1.0):
    return np.clip(x, lo, hi) if isinstance(x, np.ndarray) else min(max(x, lo), hi)


def lerp(a, b, t):
    return a + (b - a) * t


def remap(x, a, b, c=0.0, d=1.0, clamped: bool = True, ease: Callable | str | None = None):
    """Map x from [a, b] to [c, d] (optionally clamped and eased)."""
    t = (x - a) / (b - a) if b != a else (1.0 if x >= b else 0.0)
    if clamped:
        t = clamp(t)
    if ease is not None:
        t = get_ease(ease)(t)
    return c + (d - c) * t


def progress(t: float, start: float, end: float, ease: Callable | str | None = None) -> float:
    """0 before ``start``, 1 after ``end``, eased in between."""
    return remap(t, start, end, 0.0, 1.0, True, ease)


def smoothstep(e0, e1, x):
    t = clamp((x - e0) / (e1 - e0))
    return t * t * (3 - 2 * t)


def smootherstep(e0, e1, x):
    t = clamp((x - e0) / (e1 - e0))
    return t * t * t * (t * (t * 6 - 15) + 10)


def pulse(t: float, center: float, width: float) -> float:
    """Smooth bump: 1 at ``center``, 0 beyond ±width."""
    u = abs(t - center) / max(width, 1e-9)
    return 0.0 if u >= 1 else 0.5 * (1 + math.cos(math.pi * u))


def decay(t: float, start: float, tau: float) -> float:
    """1 at ``start`` decaying exponentially with time constant ``tau``; 0 before."""
    return 0.0 if t < start else math.exp(-(t - start) / max(tau, 1e-9))


# ======================================================================================
# easings
# ======================================================================================


def _in_out(f_in):
    return lambda t: 0.5 * f_in(2 * t) if t < 0.5 else 1 - 0.5 * f_in(2 - 2 * t)


def _out(f_in):
    return lambda t: 1 - f_in(1 - t)


def _back_in(t, s=1.70158):
    return t * t * ((s + 1) * t - s)


def _elastic_in(t):
    if t in (0.0, 1.0):
        return t
    return -(2 ** (10 * t - 10)) * math.sin((t * 10 - 10.75) * (2 * math.pi) / 3)


def _bounce_out(t):
    n1, d1 = 7.5625, 2.75
    if t < 1 / d1:
        return n1 * t * t
    if t < 2 / d1:
        t -= 1.5 / d1
        return n1 * t * t + 0.75
    if t < 2.5 / d1:
        t -= 2.25 / d1
        return n1 * t * t + 0.9375
    t -= 2.625 / d1
    return n1 * t * t + 0.984375


_BASE_IN: dict[str, Callable[[float], float]] = {
    "quad": lambda t: t * t,
    "cubic": lambda t: t**3,
    "quart": lambda t: t**4,
    "quint": lambda t: t**5,
    "sine": lambda t: 1 - math.cos(t * math.pi / 2),
    "expo": lambda t: 0.0 if t <= 0 else 2 ** (10 * t - 10),
    "circ": lambda t: 1 - math.sqrt(max(0.0, 1 - t * t)),
    "back": _back_in,
    "elastic": _elastic_in,
    "bounce": lambda t: 1 - _bounce_out(1 - t),
}

EASINGS: dict[str, Callable[[float], float]] = {"linear": lambda t: t}
for _name, _f in _BASE_IN.items():
    EASINGS[f"in_{_name}"] = _f
    EASINGS[f"out_{_name}"] = _out(_f)
    EASINGS[f"in_out_{_name}"] = _in_out(_f)
EASINGS["out_bounce"] = _bounce_out


def cubic_bezier(x1: float, y1: float, x2: float, y2: float) -> Callable[[float], float]:
    """CSS ``cubic-bezier()`` easing (Newton iterations with bisection fallback)."""

    def bez(t, a1, a2):
        return 3 * (1 - t) ** 2 * t * a1 + 3 * (1 - t) * t * t * a2 + t**3

    def dbez(t, a1, a2):
        return 3 * (1 - t) ** 2 * a1 + 6 * (1 - t) * t * (a2 - a1) + 3 * t * t * (1 - a2)

    def f(x):
        if x <= 0:
            return 0.0
        if x >= 1:
            return 1.0
        t = x
        for _ in range(8):
            err = bez(t, x1, x2) - x
            if abs(err) < 1e-7:
                return bez(t, y1, y2)
            d = dbez(t, x1, x2)
            if abs(d) < 1e-6:
                break
            t -= err / d
        lo, hi = 0.0, 1.0
        t = x
        for _ in range(60):
            v = bez(t, x1, x2)
            if abs(v - x) < 1e-9:
                break
            if v < x:
                lo = t
            else:
                hi = t
            t = (lo + hi) / 2
        return bez(t, y1, y2)

    return f


# Named curves common in motion design
EASINGS["standard"] = cubic_bezier(0.4, 0.0, 0.2, 1.0)
EASINGS["emphasized"] = cubic_bezier(0.2, 0.0, 0.0, 1.0)
EASINGS["decelerate"] = cubic_bezier(0.0, 0.0, 0.2, 1.0)
EASINGS["accelerate"] = cubic_bezier(0.4, 0.0, 1.0, 1.0)
EASINGS["cinematic"] = cubic_bezier(0.77, 0.0, 0.175, 1.0)


def get_ease(e: Callable | str | None) -> Callable[[float], float]:
    if e is None:
        return EASINGS["linear"]
    if callable(e):
        return e
    key = e.replace("-", "_").replace("InOut", "in_out_").lower()
    if key not in EASINGS:
        raise KeyError(f"unknown easing {e!r}; available: {', '.join(sorted(EASINGS))}")
    return EASINGS[key]


def ease(name: str, t: float) -> float:
    return get_ease(name)(clamp(t))


# ======================================================================================
# damped spring (closed form)
# ======================================================================================


class Spring:
    """Damped harmonic oscillator going from 0 to 1, in closed form.

    Parameterise either physically (``stiffness``, ``damping``, ``mass``) or perceptually
    (``freq`` in Hz of the undamped oscillator and ``damping_ratio`` ζ).  ``v0`` is the
    initial velocity in units/second (1 unit = the whole 0→1 move).

    >>> s = Spring(freq=2.0, damping_ratio=0.5)
    >>> round(s.value(s.peak_time()) - 1, 4) == round(s.overshoot(), 4)
    True
    """

    def __init__(self, freq: float | None = None, damping_ratio: float | None = None, *, stiffness: float | None = None,
                 damping: float | None = None, mass: float = 1.0, v0: float = 0.0):
        if stiffness is not None:
            self.w0 = math.sqrt(stiffness / mass)
            self.zeta = (damping or 0.0) / (2 * math.sqrt(stiffness * mass))
        else:
            self.w0 = 2 * math.pi * (freq if freq is not None else 1.5)
            self.zeta = 0.6 if damping_ratio is None else damping_ratio
        if self.w0 <= 0 or self.zeta < 0:
            raise ValueError("spring needs positive frequency and non-negative damping")
        self.v0 = v0
        z, w = self.zeta, self.w0
        A = -1.0  # e(0) = x(0) - 1
        if z < 1:
            self.kind = "under"
            self.wd = w * math.sqrt(1 - z * z)
            self.A = A
            self.B = (v0 + z * w * A) / self.wd
        elif abs(z - 1) < 1e-12:
            self.kind = "critical"
            self.A = A
            self.B = v0 + w * A
        else:
            self.kind = "over"
            s = math.sqrt(z * z - 1)
            self.r1 = -w * (z - s)
            self.r2 = -w * (z + s)
            self.C2 = (v0 - self.r1 * A) / (self.r2 - self.r1)
            self.C1 = A - self.C2

    # -- evaluation -------------------------------------------------------------------
    def error(self, t):
        """x(t) − 1."""
        t = np.maximum(t, 0.0) if isinstance(t, np.ndarray) else max(t, 0.0)
        if self.kind == "under":
            return np.exp(-self.zeta * self.w0 * t) * (self.A * np.cos(self.wd * t) + self.B * np.sin(self.wd * t))
        if self.kind == "critical":
            return (self.A + self.B * t) * np.exp(-self.w0 * t)
        return self.C1 * np.exp(self.r1 * t) + self.C2 * np.exp(self.r2 * t)

    def value(self, t):
        """Position at time t (0 for t ≤ 0)."""
        v = 1.0 + self.error(t)
        if isinstance(t, np.ndarray):
            return np.where(t <= 0, 0.0, v)
        return 0.0 if t <= 0 else float(v)

    def velocity(self, t):
        t = max(t, 0.0)
        z, w = self.zeta, self.w0
        if self.kind == "under":
            e = math.exp(-z * w * t)
            c, s = math.cos(self.wd * t), math.sin(self.wd * t)
            return e * ((-z * w) * (self.A * c + self.B * s) + self.wd * (-self.A * s + self.B * c))
        if self.kind == "critical":
            return (self.B - w * (self.A + self.B * t)) * math.exp(-w * t)
        return self.C1 * self.r1 * math.exp(self.r1 * t) + self.C2 * self.r2 * math.exp(self.r2 * t)

    # -- analytic landmarks -------------------------------------------------------------
    def first_crossing_time(self) -> float | None:
        """First t > 0 where x(t) = 1 (the "seat" moment for an impact sound)."""
        if self.kind == "under":
            # A cos + B sin = R cos(wd t − φ), zero when wd t − φ = π/2 + kπ
            phi = math.atan2(self.B, self.A)
            for k in range(-2, 4):
                t = (phi + math.pi / 2 + k * math.pi) / self.wd
                if t > 1e-12:
                    return t
            return None
        if self.kind == "critical":
            t = -self.A / self.B if self.B != 0 else -1
            return t if t > 1e-12 else None
        ratio = -self.C2 / self.C1 if self.C1 != 0 else -1
        if ratio <= 0:
            return None
        t = math.log(ratio) / (self.r1 - self.r2)
        return t if t > 1e-12 else None

    def peak_time(self) -> float | None:
        """Time of the overshoot peak: first zero of velocity after the first crossing."""
        tc = self.first_crossing_time()
        if tc is None:
            return None
        if self.kind == "under":
            z, w = self.zeta, self.w0
            # velocity ∝ cos(wd t − φ − ψ) with the combined phase below
            a = -z * w * self.A + self.wd * self.B
            b = -z * w * self.B - self.wd * self.A
            # v(t) = e^{..}(a cos + b sin) → zero when tan(wd t) = −a/b
            psi = math.atan2(b, a)
            for k in range(-2, 8):
                t = (psi + math.pi / 2 + k * math.pi) / self.wd
                if t > tc + 1e-12:
                    return t
            return None
        if self.kind == "critical":
            # v = (B − w(A + B t)) e^{−wt} = 0 → t = (B − wA)/(wB)
            if self.B == 0:
                return None
            t = (self.B - self.w0 * self.A) / (self.w0 * self.B)
            return t if t > tc else None
        num = -self.C2 * self.r2
        den = self.C1 * self.r1
        if den == 0 or num / den <= 0:
            return None
        t = math.log(num / den) / (self.r1 - self.r2)
        return t if t > tc else None

    def overshoot(self) -> float:
        tp = self.peak_time()
        if tp is None:
            return 0.0
        return max(0.0, self.value(tp) - 1.0)

    def settle_time(self, eps: float = 1e-3) -> float:
        """Time after which |x − 1| ≤ eps forever (envelope bound)."""
        if self.kind == "under":
            R = math.hypot(self.A, self.B)
            return max(0.0, math.log(max(R, eps) / eps) / (self.zeta * self.w0)) if self.zeta > 0 else math.inf
        # monotone envelopes: bisection on |e|
        lo, hi = 0.0, 1.0
        while abs(self.error(hi)) > eps:
            hi *= 2
            if hi > 1e6:
                return math.inf
        for _ in range(80):
            mid = (lo + hi) / 2
            if abs(self.error(mid)) > eps:
                lo = mid
            else:
                hi = mid
        return hi

    def at(self, t: float, start: float = 0.0, frm=0.0, to=1.0, snap_eps: float | None = 1e-4):
        """Animate ``frm → to`` starting at ``start``. With ``snap_eps`` the value snaps
        exactly to ``to`` once settled (so final frames are pixel-exact)."""
        lt = t - start
        if lt <= 0:
            return frm
        if snap_eps is not None and lt >= self.settle_time(snap_eps):
            return to
        return frm + (to - frm) * self.value(lt)


# ======================================================================================
# keyframe channels
# ======================================================================================


@dataclass
class Key:
    t: float
    value: object
    ease: str | None = None  # easing of the segment starting at this key (None → Hermite)
    tangent: object | None = None  # explicit slope (value/second); None → auto


class Channel:
    """Keyframed value (scalar or vector) with cubic Hermite interpolation.

    Tangents default to Catmull-Rom ("auto"); ``tangents="flat"`` gives ease-in/out at
    every key.  A key with ``ease`` set uses that easing for its outgoing segment.
    """

    def __init__(self, keys: Iterable, tangents: str = "auto"):
        ks = []
        for k in keys:
            if isinstance(k, Key):
                ks.append(k)
            elif isinstance(k, dict):
                ks.append(Key(**k))
            else:
                ks.append(Key(*k))
        if not ks:
            raise ValueError("channel needs at least one key")
        self.keys = sorted(ks, key=lambda k: k.t)
        self.mode = tangents
        self._vals = [np.asarray(k.value, dtype=np.float64) for k in self.keys]

    def _tangent(self, i: int) -> np.ndarray:
        k = self.keys[i]
        if k.tangent is not None:
            return np.asarray(k.tangent, dtype=np.float64)
        n = len(self.keys)
        if self.mode == "flat" or n == 1 or i == 0 or i == n - 1:
            return np.zeros_like(self._vals[i])
        dt = self.keys[i + 1].t - self.keys[i - 1].t
        return (self._vals[i + 1] - self._vals[i - 1]) / dt if dt > 0 else np.zeros_like(self._vals[i])

    def __call__(self, t: float):
        ks = self.keys
        if t <= ks[0].t:
            v = self._vals[0]
        elif t >= ks[-1].t:
            v = self._vals[-1]
        else:
            i = max(j for j in range(len(ks) - 1) if ks[j].t <= t)
            k0, k1 = ks[i], ks[i + 1]
            h = k1.t - k0.t
            u = (t - k0.t) / h
            p0, p1 = self._vals[i], self._vals[i + 1]
            if k0.ease is not None:
                v = p0 + (p1 - p0) * get_ease(k0.ease)(u)
            else:
                m0, m1 = self._tangent(i) * h, self._tangent(i + 1) * h
                u2, u3 = u * u, u * u * u
                v = (2 * u3 - 3 * u2 + 1) * p0 + (u3 - 2 * u2 + u) * m0 + (-2 * u3 + 3 * u2) * p1 + (u3 - u2) * m1
        return float(v) if np.ndim(v) == 0 else v

    @property
    def end(self) -> float:
        return self.keys[-1].t


def stagger(n: int, start: float, each: float, order: str = "forward", seed: int = 0) -> list[float]:
    """Start times for ``n`` items (order: forward, reverse, center, edges, random)."""
    idx = list(range(n))
    if order == "reverse":
        idx = idx[::-1]
    elif order == "center":
        c = (n - 1) / 2
        idx = sorted(range(n), key=lambda i: (abs(i - c), i))
    elif order == "edges":
        c = (n - 1) / 2
        idx = sorted(range(n), key=lambda i: (-abs(i - c), i))
    elif order == "random":
        rng = np.random.default_rng(seed)
        idx = list(rng.permutation(n))
    times = [0.0] * n
    for rank, i in enumerate(idx):
        times[i] = start + rank * each
    return times


# ======================================================================================
# timeline
# ======================================================================================


@dataclass
class Event:
    """A moment shared by picture and sound (seat, impact, reveal, word…)."""

    name: str
    t: float
    kind: str = "impact"
    strength: float = 1.0
    meta: dict = field(default_factory=dict)


class Timeline:
    """Named events and spans. Serialisable to JSON for the audio mix and QC."""

    def __init__(self, duration: float, fps: float = 60.0):
        self.duration = float(duration)
        self.fps = float(fps)
        self.events: list[Event] = []
        self.spans: dict[str, tuple[float, float]] = {}

    def add(self, name: str, t: float, kind: str = "impact", strength: float = 1.0, snap: bool = False, **meta) -> Event:
        if snap:
            t = self.snap(t)
        e = Event(name, float(t), kind, float(strength), meta)
        self.events.append(e)
        self.events.sort(key=lambda e: e.t)
        return e

    def span(self, name: str, start: float, end: float) -> tuple[float, float]:
        self.spans[name] = (float(start), float(end))
        return self.spans[name]

    def snap(self, t: float) -> float:
        """Round to the nearest frame time (useful for hard cuts and sync points)."""
        return round(t * self.fps) / self.fps

    def get(self, name: str) -> Event:
        for e in self.events:
            if e.name == name:
                return e
        raise KeyError(name)

    def t(self, name: str) -> float:
        if name in self.spans:
            return self.spans[name][0]
        return self.get(name).t

    def local(self, t: float, span: str, ease=None) -> float:
        a, b = self.spans[span]
        return progress(t, a, b, ease)

    def of_kind(self, *kinds: str) -> list[Event]:
        return [e for e in self.events if e.kind in kinds]

    def to_dict(self) -> dict:
        return {
            "duration": self.duration,
            "fps": self.fps,
            "events": [asdict(e) for e in self.events],
            "spans": {k: list(v) for k, v in self.spans.items()},
        }

    def to_json(self, path=None) -> str:
        s = json.dumps(self.to_dict(), indent=2)
        if path:
            with open(path, "w") as f:
                f.write(s)
        return s

    @classmethod
    def from_dict(cls, d: dict) -> "Timeline":
        tl = cls(d.get("duration", 0), d.get("fps", 60))
        for e in d.get("events", []):
            tl.add(e["name"], e["t"], e.get("kind", "impact"), e.get("strength", 1.0), **(e.get("meta") or {}))
        for k, v in (d.get("spans") or {}).items():
            tl.span(k, v[0], v[1])
        return tl

    @classmethod
    def load(cls, path) -> "Timeline":
        with open(path) as f:
            return cls.from_dict(json.load(f))


def frame_times(duration: float, fps: float) -> np.ndarray:
    n = int(round(duration * fps))
    return np.arange(n) / fps


def seq(*items: Sequence) -> list[tuple[str, float, float]]:
    """Chain (name, duration) pairs into (name, start, end) spans."""
    t = 0.0
    out = []
    for name, dur in items:
        out.append((name, t, t + dur))
        t += dur
    return out


__all__ = [
    "Channel", "EASINGS", "Event", "Key", "Spring", "Timeline", "clamp", "cubic_bezier", "decay", "ease",
    "frame_times", "get_ease", "lerp", "progress", "pulse", "remap", "seq", "smootherstep", "smoothstep", "stagger",
]
