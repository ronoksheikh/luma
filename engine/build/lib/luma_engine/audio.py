"""Sound design toolkit: synthesis, spatialisation, mixing and mastering.

All signals are float64 numpy arrays at ``SR`` (48 kHz): mono ``(n,)`` or stereo
``(n, 2)``.  Everything is deterministic (seeded RNG) so renders are reproducible.

Typical flow::

    mix = Mix(duration=5.0)
    mix.place(sub_boom(), t=2.30, gain_db=-4)
    mix.place(glass_ping(1320), t=1.52, pan=-0.3)
    mix.place(whoosh(0.8, rising=True), t=1.0, end_aligned=True, pan_path=[(0, -0.6), (1, 0.6)])
    out = mix.render()
    out = master(out, target_lufs=-14, true_peak_db=-1)
    write_wav("audio/mix.wav", out)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
import soundfile as sf
from scipy import signal

SR = 48000


def _rng(seed):
    return np.random.default_rng(seed)


def secs(n: int, sr: int = SR) -> float:
    return n / sr


def samples(t: float, sr: int = SR) -> int:
    return int(round(t * sr))


def db_to_gain(db: float) -> float:
    return 10 ** (db / 20)


def gain_to_db(g: float) -> float:
    return 20 * math.log10(max(g, 1e-12))


def to_stereo(x: np.ndarray) -> np.ndarray:
    return np.stack([x, x], axis=1) if x.ndim == 1 else x


def to_mono(x: np.ndarray) -> np.ndarray:
    return x.mean(axis=1) if x.ndim == 2 else x


# ======================================================================================
# envelopes
# ======================================================================================


def env_adsr(n: int, a: float = 0.005, d: float = 0.1, s: float = 0.6, r: float = 0.3, sr: int = SR) -> np.ndarray:
    na, nd, nr = samples(a, sr), samples(d, sr), samples(r, sr)
    ns = max(0, n - na - nd - nr)
    e = np.concatenate([
        np.linspace(0, 1, max(na, 1), endpoint=False),
        np.linspace(1, s, max(nd, 1), endpoint=False),
        np.full(ns, s),
        np.linspace(s, 0, max(nr, 1)),
    ])
    return _fit(e, n)


def env_exp(n: int, tau: float, attack: float = 0.002, sr: int = SR) -> np.ndarray:
    t = np.arange(n) / sr
    e = np.exp(-t / max(tau, 1e-6))
    na = samples(attack, sr)
    if na > 0:
        e[:na] *= np.linspace(0, 1, na) ** 2
    return e


def _fit(x: np.ndarray, n: int) -> np.ndarray:
    return x[:n] if len(x) >= n else np.concatenate([x, np.zeros(n - len(x))])


def fade(x: np.ndarray, fade_in: float = 0.0, fade_out: float = 0.0, sr: int = SR, curve: str = "equal_power") -> np.ndarray:
    y = x.copy()
    n = len(y)
    for dur, is_in in ((fade_in, True), (fade_out, False)):
        k = min(samples(dur, sr), n)
        if k <= 0:
            continue
        u = np.linspace(0, 1, k)
        g = np.sin(u * np.pi / 2) if curve == "equal_power" else u
        if not is_in:
            g = g[::-1]
        sl = slice(0, k) if is_in else slice(n - k, n)
        y[sl] = y[sl] * (g[:, None] if y.ndim == 2 else g)
    return y


# ======================================================================================
# oscillators & synthesis
# ======================================================================================


def osc(freq, dur: float, kind: str = "sine", phase: float = 0.0, sr: int = SR) -> np.ndarray:
    """Oscillator; ``freq`` may be a constant or an array (per-sample Hz, for sweeps)."""
    n = samples(dur, sr)
    f = np.full(n, float(freq)) if np.isscalar(freq) else _fit(np.asarray(freq, dtype=np.float64), n)
    ph = 2 * np.pi * np.cumsum(f) / sr + phase
    if kind == "sine":
        return np.sin(ph)
    frac = (ph / (2 * np.pi)) % 1.0
    dt = f / sr
    if kind == "saw":
        y = 2 * frac - 1
        return y - _polyblep(frac, dt)
    if kind == "square":
        y = np.where(frac < 0.5, 1.0, -1.0)
        return y + _polyblep(frac, dt) - _polyblep((frac + 0.5) % 1.0, dt)
    if kind == "triangle":
        return 2 * np.abs(2 * frac - 1) - 1
    raise ValueError(kind)


def _polyblep(t: np.ndarray, dt: np.ndarray) -> np.ndarray:
    """Band-limited step correction (reduces aliasing of saw/square)."""
    out = np.zeros_like(t)
    m = t < dt
    x = t[m] / dt[m]
    out[m] = x + x - x * x - 1
    m2 = t > 1 - dt
    x = (t[m2] - 1) / dt[m2]
    out[m2] = x * x + x + x + 1
    return out


def fm_bell(freq: float = 880.0, dur: float = 2.5, ratio: float = 3.5, index: float = 4.0, tau: float = 0.6, sr: int = SR) -> np.ndarray:
    """Chowning FM bell: inharmonic ratio, modulation index decays faster than amplitude."""
    n = samples(dur, sr)
    t = np.arange(n) / sr
    idx = index * np.exp(-t / (tau * 0.4))
    mod = np.sin(2 * np.pi * freq * ratio * t)
    y = np.sin(2 * np.pi * freq * t + idx * mod)
    return y * env_exp(n, tau, 0.001, sr)


GLASS_RATIOS = (1.0, 2.756, 5.404, 8.933, 13.34)  # free-free bar / wine-glass-like partials


def glass_ping(freq: float = 1760.0, dur: float = 2.0, brightness: float = 0.6, detune: float = 0.002, seed: int = 0, sr: int = SR) -> np.ndarray:
    """Inharmonic glass ping: decaying partials with slight beating (stereo)."""
    rng = _rng(seed)
    n = samples(dur, sr)
    t = np.arange(n) / sr
    out = np.zeros((n, 2))
    for k, r in enumerate(GLASS_RATIOS):
        f = freq * r
        if f > sr / 2 * 0.9:
            break
        amp = brightness**k / (1 + k)
        tau = dur * 0.45 / (1 + 0.9 * k)
        for ch in range(2):
            fd = f * (1 + (rng.random() - 0.5) * 2 * detune)
            out[:, ch] += amp * np.sin(2 * np.pi * fd * t + rng.random() * 6.28) * np.exp(-t / tau)
    atk = min(n, samples(0.0015, sr))
    out[:atk] *= np.linspace(0, 1, atk)[:, None]
    return out / (np.max(np.abs(out)) + 1e-12)


def noise(dur: float, color: str = "white", seed: int = 0, sr: int = SR) -> np.ndarray:
    n = samples(dur, sr)
    w = _rng(seed).standard_normal(n)
    if color == "white":
        return w / 3
    spec = np.fft.rfft(w)
    f = np.fft.rfftfreq(n, 1 / sr)
    f[0] = f[1] if n > 1 else 1
    if color == "pink":
        spec /= np.sqrt(f)
    elif color == "brown":
        spec /= f
    elif color == "blue":
        spec *= np.sqrt(f)
    y = np.fft.irfft(spec, n)
    return y / (np.max(np.abs(y)) + 1e-12) * 0.5


def _bandpass_sweep(x: np.ndarray, f0: float, f1: float, q: float = 1.2, block: int = 256, sr: int = SR) -> np.ndarray:
    """Time-varying band-pass (block-wise biquad with state carry, exponential sweep)."""
    n = len(x)
    y = np.zeros(n)
    zi = np.zeros(2)
    nb = max(1, math.ceil(n / block))
    for b in range(nb):
        u = b / max(nb - 1, 1)
        fc = f0 * (f1 / f0) ** u
        fc = min(max(fc, 20.0), sr / 2 * 0.95)
        bb, aa = signal.iirpeak(fc, q, fs=sr)
        sl = slice(b * block, min(n, (b + 1) * block))
        y[sl], zi = signal.lfilter(bb, aa, x[sl], zi=zi)
    return y


def whoosh(dur: float = 0.8, f_start: float = 300.0, f_end: float = 2400.0, rising: bool = True, q: float = 1.4,
           seed: int = 0, peak_at: float = 0.75, sr: int = SR) -> np.ndarray:
    """Filtered-noise whoosh. ``peak_at`` (0..1) positions the loudest moment — place
    the whoosh so this lands on the visual pass-by / impact."""
    n = samples(dur, sr)
    x = noise(dur, "pink", seed, sr)
    a, b = (f_start, f_end) if rising else (f_end, f_start)
    y = _bandpass_sweep(x, a, b, q, sr=sr)
    t = np.linspace(0, 1, n)
    pk = min(max(peak_at, 0.05), 0.95)
    env = np.where(t < pk, (t / pk) ** 2.2, np.exp(-(t - pk) / (1 - pk) * 4.0))
    y = y * env
    return y / (np.max(np.abs(y)) + 1e-12)


def click(dur: float = 0.03, freq: float = 3500.0, seed: int = 0, sr: int = SR) -> np.ndarray:
    n = samples(dur, sr)
    t = np.arange(n) / sr
    y = (noise(dur, "white", seed, sr) * 0.6 + 0.8 * np.sin(2 * np.pi * freq * t)) * np.exp(-t / (dur * 0.18))
    return y / (np.max(np.abs(y)) + 1e-12)


def clack(dur: float = 0.12, body: float = 900.0, seed: int = 0, sr: int = SR) -> np.ndarray:
    """Hard mechanical clack: noisy transient + two damped body modes."""
    n = samples(dur, sr)
    t = np.arange(n) / sr
    tr = noise(dur, "white", seed, sr) * np.exp(-t / 0.004)
    modes = np.sin(2 * np.pi * body * t) * np.exp(-t / 0.03) + 0.6 * np.sin(2 * np.pi * body * 2.37 * t) * np.exp(-t / 0.018)
    y = tr * 0.7 + modes * 0.6
    return y / (np.max(np.abs(y)) + 1e-12)


def thump(dur: float = 0.35, f0: float = 120.0, f1: float = 45.0, sr: int = SR) -> np.ndarray:
    n = samples(dur, sr)
    t = np.arange(n) / sr
    f = f1 + (f0 - f1) * np.exp(-t / 0.04)
    y = np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-t / (dur * 0.3))
    y[: samples(0.002, sr)] *= np.linspace(0, 1, samples(0.002, sr))
    return y / (np.max(np.abs(y)) + 1e-12)


def sub_boom(dur: float = 2.2, f0: float = 70.0, f1: float = 28.0, drive: float = 1.6, sr: int = SR) -> np.ndarray:
    """Cinematic sub drop: pitch-falling sine, soft saturation for audible harmonics."""
    n = samples(dur, sr)
    t = np.arange(n) / sr
    f = f1 + (f0 - f1) * np.exp(-t / 0.25)
    y = np.sin(2 * np.pi * np.cumsum(f) / sr)
    y = np.tanh(drive * y) / np.tanh(drive)
    y *= env_exp(n, dur * 0.35, 0.004, sr)
    return y / (np.max(np.abs(y)) + 1e-12)


def riser(dur: float = 1.5, f0: float = 200.0, f1: float = 1600.0, seed: int = 0, sr: int = SR) -> np.ndarray:
    n = samples(dur, sr)
    t = np.linspace(0, 1, n)
    tone = osc(f0 * (f1 / f0) ** t, dur, "saw", sr=sr) * 0.3
    tone = signal.sosfilt(signal.butter(2, 3000, "low", fs=sr, output="sos"), tone)
    y = (tone + whoosh(dur, f0, f1 * 3, True, 1.0, seed, 0.98, sr) * 0.7) * (t**2)
    return y / (np.max(np.abs(y)) + 1e-12)


def shimmer(dur: float = 1.5, freq: float = 2400.0, density: float = 40.0, seed: int = 0, sr: int = SR) -> np.ndarray:
    """Sparkly high texture (random tiny pings)."""
    rng = _rng(seed)
    n = samples(dur, sr)
    out = np.zeros((n, 2))
    k = int(density * dur)
    for i in range(k):
        f = freq * (1 + rng.random() * 1.5)
        g = glass_ping(f, 0.25, 0.4, seed=seed + i + 1, sr=sr) * (0.2 + 0.8 * rng.random())
        pos = int(rng.random() * max(1, n - len(g)))
        pan = rng.random() * 2 - 1
        out[pos : pos + len(g)] += pan_stereo(to_mono(g), pan)[: n - pos]
    t = np.linspace(0, 1, n)[:, None]
    out *= np.sin(np.pi * t) ** 0.7
    return out / (np.max(np.abs(out)) + 1e-12)


def granular(source: np.ndarray, dur: float, grain: float = 0.06, density: float = 80.0, pitch_jitter: float = 0.05,
             seed: int = 0, sr: int = SR) -> np.ndarray:
    """Granular texture: Hann-windowed grains read from random source positions."""
    rng = _rng(seed)
    src = to_mono(np.asarray(source, dtype=np.float64))
    n = samples(dur, sr)
    gl = max(16, samples(grain, sr))
    out = np.zeros((n, 2))
    win = np.hanning(gl)
    count = int(density * dur)
    for _ in range(count):
        rate = 1 + (rng.random() * 2 - 1) * pitch_jitter
        span = int(gl * rate) + 2
        if len(src) <= span:
            continue
        s0 = int(rng.random() * (len(src) - span))
        idx = s0 + np.arange(gl) * rate
        g = np.interp(idx, np.arange(len(src)), src) * win
        pos = int(rng.random() * max(1, n - gl))
        out[pos : pos + gl] += pan_stereo(g, rng.random() * 2 - 1)[: n - pos]
    return out / (np.max(np.abs(out)) + 1e-12)


# ======================================================================================
# filters, space
# ======================================================================================


def lowpass(x: np.ndarray, fc: float, order: int = 2, sr: int = SR) -> np.ndarray:
    return signal.sosfilt(signal.butter(order, fc, "low", fs=sr, output="sos"), x, axis=0)


def highpass(x: np.ndarray, fc: float, order: int = 2, sr: int = SR) -> np.ndarray:
    return signal.sosfilt(signal.butter(order, fc, "high", fs=sr, output="sos"), x, axis=0)


def pan_stereo(x: np.ndarray, pan: float | np.ndarray) -> np.ndarray:
    """Equal-power pan of a mono signal; ``pan`` in [−1, 1] (constant or per-sample)."""
    x = to_mono(x)
    p = np.clip(np.asarray(pan, dtype=np.float64), -1, 1)
    th = (p + 1) * np.pi / 4
    return np.stack([x * np.cos(th), x * np.sin(th)], axis=1)


def pan_path(n: int, points: Sequence[tuple[float, float]]) -> np.ndarray:
    """Per-sample pan from (u, pan) keypoints with u in [0, 1] over the sound's length."""
    u = np.linspace(0, 1, n)
    us, ps = zip(*points)
    return np.interp(u, us, ps)


def reverb_ir(dur: float = 2.2, decay_db: float = 60.0, predelay: float = 0.012, damping: float = 0.5,
              width: float = 1.0, seed: int = 7, sr: int = SR) -> np.ndarray:
    """Synthetic stereo impulse response: decorrelated noise with frequency-dependent
    exponential decay (highs die faster) and a few early reflections."""
    rng = _rng(seed)
    n = samples(dur, sr)
    t = np.arange(n) / sr
    rt = dur
    ir = np.zeros((n, 2))
    for ch in range(2):
        w = rng.standard_normal(n)
        lo = lowpass(w, 1500.0) * np.exp(-t * (decay_db / 20 * math.log(10)) / rt)
        hi = (w - lowpass(w, 1500.0)) * np.exp(-t * (decay_db / 20 * math.log(10)) / (rt * (1 - 0.8 * damping)))
        ir[:, ch] = lo + hi
    for k in range(6):  # early reflections
        d = samples(predelay + 0.007 * (k + 1) + rng.random() * 0.01, sr)
        if d < n:
            ir[d, 0] += 0.5 * (0.7**k) * (1 if rng.random() > 0.5 else -1)
            ir[min(n - 1, d + samples(0.0013 * (k + 1), sr)), 1] += 0.5 * (0.7**k) * (1 if rng.random() > 0.5 else -1)
    pd = samples(predelay, sr)
    ir = np.concatenate([np.zeros((pd, 2)), ir])[:n]
    mid, side = (ir[:, 0] + ir[:, 1]) / 2, (ir[:, 0] - ir[:, 1]) / 2 * width
    ir = np.stack([mid + side, mid - side], axis=1)
    return ir / (np.sqrt(np.sum(ir**2) / 2) + 1e-12)


def convolve(x: np.ndarray, ir: np.ndarray, wet: float = 0.25, dry: float = 1.0) -> np.ndarray:
    """Convolution reverb (FFT).  Output length = len(x) + len(ir) − 1."""
    xs = to_stereo(x)
    irs = to_stereo(ir)
    out_len = len(xs) + len(irs) - 1
    wet_sig = np.stack([signal.fftconvolve(xs[:, c], irs[:, c]) for c in range(2)], axis=1)
    y = np.zeros((out_len, 2))
    y[: len(xs)] += xs * dry
    y += wet_sig * wet
    return y


def _comb(x: np.ndarray, D: int, g: float, damp: float) -> np.ndarray:
    """Feedback comb y[n] = x[n] + g·lp(y[n−D]) computed block-wise (blocks of D samples
    only depend on the previous block, so each block is one vector operation)."""
    y = np.zeros(len(x))
    prev = np.zeros(D)
    lp_state = 0.0
    for s0 in range(0, len(x), D):
        blk = x[s0 : s0 + D]
        fbk = prev[: len(blk)]
        # one-pole low-pass on the feedback path (damping), applied per block
        if damp > 0:
            fbk, zf = signal.lfilter([1 - damp], [1, -damp], fbk, zi=[lp_state * damp])
            lp_state = fbk[-1] if len(fbk) else lp_state
        out = blk + g * fbk
        y[s0 : s0 + len(blk)] = out
        prev = np.concatenate([out, np.zeros(D - len(out))]) if len(out) < D else out
    return y


def _allpass(x: np.ndarray, D: int, g: float = 0.5) -> np.ndarray:
    """Schroeder all-pass y[n] = −g·x[n] + x[n−D] + g·y[n−D], block-wise."""
    y = np.zeros(len(x))
    xp = np.zeros(D)
    yp = np.zeros(D)
    for s0 in range(0, len(x), D):
        blk = x[s0 : s0 + D]
        k = len(blk)
        out = -g * blk + xp[:k] + g * yp[:k]
        y[s0 : s0 + k] = out
        xp = blk if k == D else np.concatenate([blk, np.zeros(D - k)])
        yp = out if k == D else np.concatenate([out, np.zeros(D - k)])
    return y


def schroeder_reverb(x: np.ndarray, room: float = 0.8, damp: float = 0.4, wet: float = 0.3, sr: int = SR) -> np.ndarray:
    """Freeverb-style algorithmic reverb (8 parallel damped combs + 4 series all-passes
    per channel, with offset delays for stereo decorrelation)."""
    xs = to_stereo(x)
    tail = samples(3.0 * room, sr)
    xs = np.concatenate([xs, np.zeros((tail, 2))])
    combs = [1116, 1188, 1277, 1356, 1422, 1491, 1557, 1617]
    aps = [556, 441, 341, 225]
    k = sr / 44100
    fb = 0.7 + 0.28 * room
    out = np.zeros_like(xs)
    for ch in range(2):
        spread = 23 * ch
        acc = np.zeros(len(xs))
        for d in combs:
            acc += _comb(xs[:, ch] * 0.015, int((d + spread) * k), fb, damp)
        y = acc
        for d in aps:
            y = _allpass(y, int((d + spread) * k))
        out[:, ch] = y
    return xs + out * wet


# ======================================================================================
# mixing
# ======================================================================================


@dataclass
class Placement:
    sound: np.ndarray
    t: float
    gain_db: float = 0.0
    pan: float | None = None
    pan_path: Sequence[tuple[float, float]] | None = None
    bus: str = "sfx"
    name: str = ""
    anchor: float = 0.0  # position inside the sound (s) that lands on t


@dataclass
class Mix:
    """Event-placed mix with buses (sfx, music, voice, …)."""

    duration: float
    sr: int = SR
    placements: list[Placement] = field(default_factory=list)
    bus_gain_db: dict = field(default_factory=dict)
    reverb: dict | None = field(default_factory=lambda: {"wet": 0.18, "dur": 2.4})

    def place(self, sound: np.ndarray, t: float, gain_db: float = 0.0, pan: float | None = None,
              pan_path: Sequence[tuple[float, float]] | None = None, bus: str = "sfx", name: str = "",
              anchor: float = 0.0, end_aligned: bool = False) -> Placement:
        """Place ``sound`` so that its ``anchor`` (seconds into the sound) lands on ``t``.
        ``end_aligned`` puts the sound's *end* on t (e.g. a riser into an impact)."""
        if end_aligned:
            anchor = len(sound) / self.sr
        p = Placement(np.asarray(sound, dtype=np.float64), float(t), gain_db, pan, pan_path, bus, name, anchor)
        self.placements.append(p)
        return p

    def render_bus(self, bus: str, length: int | None = None) -> np.ndarray:
        n = length or samples(self.duration, self.sr)
        out = np.zeros((n + samples(4.0, self.sr), 2))
        pad = 0
        for p in self.placements:
            if p.bus != bus:
                continue
            s = p.sound
            if p.pan_path is not None:
                st = pan_stereo(to_mono(s), pan_path(len(s), p.pan_path))
            elif p.pan is not None:
                st = pan_stereo(to_mono(s), p.pan) if s.ndim == 1 else _balance(s, p.pan)
            else:
                st = to_stereo(s)
            st = fade(st, 0.0, min(0.01, len(st) / self.sr / 4)) * db_to_gain(p.gain_db)  # no end clicks
            start = samples(p.t - p.anchor, self.sr)
            if start < 0:
                st = st[-start:]
                start = 0
            end = min(start + len(st), len(out))
            if end > start:
                out[start:end] += st[: end - start]
            pad = max(pad, end)
        return out[:n] * db_to_gain(self.bus_gain_db.get(bus, 0.0))

    def buses(self) -> list[str]:
        return sorted({p.bus for p in self.placements})

    def render(self, duck: dict | None = None) -> np.ndarray:
        """Sum all buses (sfx gets the shared reverb send; music is ducked under voice)."""
        n = samples(self.duration, self.sr)
        total = np.zeros((n, 2))
        rendered = {b: self.render_bus(b, n) for b in self.buses()}
        if "music" in rendered and "voice" in rendered:
            rendered["music"] = sidechain_duck(rendered["music"], rendered["voice"], **(duck or {}))
        for b, sig in rendered.items():
            if b == "sfx" and self.reverb:
                ir = reverb_ir(self.reverb.get("dur", 2.4))
                sig = convolve(sig, ir, wet=self.reverb.get("wet", 0.18))[:n]
            total += sig
        return total

    def to_dict(self) -> dict:
        return {
            "duration": self.duration,
            "placements": [{"name": p.name, "t": p.t, "gain_db": p.gain_db, "pan": p.pan, "bus": p.bus, "anchor": p.anchor, "length_s": len(p.sound) / self.sr} for p in self.placements],
        }


def _balance(st: np.ndarray, pan: float) -> np.ndarray:
    th = (np.clip(pan, -1, 1) + 1) * np.pi / 4
    return st * np.array([math.cos(th), math.sin(th)]) * math.sqrt(2)


def envelope_follower(x: np.ndarray, attack: float = 0.01, release: float = 0.25, sr: int = SR) -> np.ndarray:
    m = np.abs(to_mono(x))
    ga = math.exp(-1 / (attack * sr))
    gr = math.exp(-1 / (release * sr))
    # vectorised approximation: attack via max-filter, release via one-pole IIR
    rel = signal.lfilter([1 - gr], [1, -gr], m)
    att = signal.lfilter([1 - ga], [1, -ga], m)
    return np.maximum(rel, att)


def sidechain_duck(music: np.ndarray, voice: np.ndarray, depth_db: float = -10.0, threshold_db: float = -40.0,
                   attack: float = 0.03, release: float = 0.35, sr: int = SR) -> np.ndarray:
    """Duck ``music`` by up to ``depth_db`` while ``voice`` is above ``threshold_db``."""
    n = min(len(music), len(voice))
    env = envelope_follower(voice[:n], attack, release, sr)
    level = 20 * np.log10(env + 1e-9)
    amount = np.clip((level - threshold_db) / 12.0, 0.0, 1.0)  # 12 dB soft knee
    # smooth the gain so ducking never zippers
    g_db = depth_db * amount
    g_db = signal.lfilter([1 - math.exp(-1 / (release * sr))], [1, -math.exp(-1 / (release * sr))], g_db)
    g = 10 ** (g_db / 20)
    out = music.copy()
    out[:n] *= g[:, None] if out.ndim == 2 else g
    return out


# ======================================================================================
# loudness & limiting
# ======================================================================================


def lufs(x: np.ndarray, sr: int = SR) -> float:
    """Integrated loudness (ITU-R BS.1770-4 via pyloudnorm)."""
    import pyloudnorm as pyln

    meter = pyln.Meter(sr)
    xs = to_stereo(x)
    if len(xs) < samples(0.4, sr):
        xs = np.concatenate([xs, np.zeros((samples(0.4, sr) - len(xs), 2))])
    v = meter.integrated_loudness(xs)
    return float(v)


def true_peak_db(x: np.ndarray, oversample: int = 4) -> float:
    """True peak (dBTP) estimated with 4× polyphase oversampling (BS.1770 Annex 2)."""
    xs = to_stereo(x)
    up = signal.resample_poly(xs, oversample, 1, axis=0)
    pk = max(float(np.max(np.abs(up))), float(np.max(np.abs(xs))))
    return gain_to_db(pk)


def normalize_lufs(x: np.ndarray, target: float = -14.0, sr: int = SR) -> np.ndarray:
    cur = lufs(x, sr)
    if not np.isfinite(cur):
        return x
    return x * db_to_gain(target - cur)


def true_peak_limit(x: np.ndarray, ceiling_db: float = -1.0, lookahead: float = 0.005, release: float = 0.08,
                    oversample: int = 4, sr: int = SR) -> np.ndarray:
    """Look-ahead brick-wall limiter operating on the 4× oversampled peak envelope, so the
    *true* peak stays ≤ ``ceiling_db``."""
    xs = to_stereo(x).astype(np.float64)
    ceiling = db_to_gain(ceiling_db)
    up = np.abs(signal.resample_poly(xs, oversample, 1, axis=0)).max(axis=1)
    # peak per original sample
    pk = up[: len(xs) * oversample].reshape(-1, oversample).max(axis=1)
    pk = np.maximum(pk, np.abs(xs).max(axis=1))
    need = np.minimum(1.0, ceiling / np.maximum(pk, 1e-12))
    la = max(1, samples(lookahead, sr))
    # look-ahead: gain must already be low `la` samples before the peak
    from scipy.ndimage import minimum_filter1d

    g = minimum_filter1d(need, size=2 * la + 1, mode="nearest")
    # smooth: instant attack (already min-filtered), exponential release
    rel = math.exp(-1 / (release * sr))
    sm = np.empty_like(g)
    cur = 1.0
    for i in range(len(g)):  # simple recursive release; fast enough for short mixes
        cur = g[i] if g[i] < cur else g[i] + (cur - g[i]) * rel
        sm[i] = cur
    y = xs * sm[:, None]
    # safety: iterate if resampling overshoot survived
    for _ in range(3):
        tp = db_to_gain(true_peak_db(y, oversample))
        if tp <= ceiling * 1.0001:
            break
        y *= ceiling / tp * 0.999
    return y


def master(x: np.ndarray, target_lufs: float = -14.0, true_peak: float = -1.0, sr: int = SR, iterations: int = 6) -> np.ndarray:
    """Normalise integrated loudness, then true-peak limit; iterate because limiting
    lowers loudness.  Result: ≈ target LUFS (±0.5) and ≤ true_peak dBTP."""
    y = to_stereo(x)
    for _ in range(iterations):
        y = normalize_lufs(y, target_lufs, sr)
        y = true_peak_limit(y, true_peak, sr=sr)
        if abs(lufs(y, sr) - target_lufs) < 0.3:
            break
    return y


def fit_length(x: np.ndarray, duration: float, sr: int = SR, fade_out: float = 0.02) -> np.ndarray:
    """Trim/pad to exactly ``duration`` seconds (sample-accurate A/V length match)."""
    n = samples(duration, sr)
    y = to_stereo(x)
    if len(y) >= n:
        y = y[:n].copy()
        return fade(y, 0, fade_out, sr) if fade_out > 0 else y
    return np.concatenate([y, np.zeros((n - len(y), 2))])


# ======================================================================================
# IO & analysis
# ======================================================================================


def write_wav(path: str, x: np.ndarray, sr: int = SR, subtype: str = "PCM_24") -> str:
    y = np.clip(to_stereo(x), -1.0, 1.0)
    sf.write(path, y, sr, subtype=subtype)
    return path


def read_audio(path: str, sr: int = SR) -> np.ndarray:
    """Read any audio (via soundfile, or ffmpeg for mp3 etc.), resampled to ``sr``, stereo."""
    try:
        data, file_sr = sf.read(path, always_2d=True, dtype="float64")
    except Exception:
        import subprocess

        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", path, "-f", "f32le", "-ac", "2", "-ar", str(sr), "-"],
            capture_output=True, check=True,
        ).stdout
        return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).astype(np.float64)
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    data = data[:, :2]
    if file_sr != sr:
        g = math.gcd(int(file_sr), sr)
        data = signal.resample_poly(data, sr // g, int(file_sr) // g, axis=0)
    return data


def onsets(x: np.ndarray, sr: int = SR, hop: int = 256, threshold: float = 0.3, min_gap: float = 0.08) -> list[float]:
    """Onset times (s) via spectral flux with adaptive peak picking."""
    m = to_mono(x)
    if len(m) < 2048:
        return []
    f, t, Z = signal.stft(m, sr, nperseg=1024, noverlap=1024 - hop, boundary=None)
    mag = np.log1p(np.abs(Z) * 10)
    flux = np.maximum(np.diff(mag, axis=1), 0).sum(axis=0)
    flux = np.concatenate([[0], flux])
    if flux.max() <= 0:
        return []
    flux /= flux.max()
    # adaptive threshold: a peak must rise clearly above the local (~0.5 s) median, so
    # stationary beds (noise, pads, music) don't produce false onsets
    k = max(3, int(0.5 * sr / hop) | 1)
    from scipy.ndimage import median_filter

    med = median_filter(flux, size=k, mode="nearest")
    peaks, _ = signal.find_peaks(flux, height=med + max(0.18, threshold * 0.6), distance=max(1, int(min_gap * sr / hop)))
    # stft frame time is the window centre; the onset lies about half a window earlier
    return [float(t[p] - (1024 / 2) / sr + hop / sr) for p in peaks]


def spectrogram_png(x: np.ndarray, path: str, sr: int = SR, events: Sequence[dict] | None = None, width: int = 1600,
                    height: int = 520, title: str = "") -> str:
    """Waveform + log-frequency spectrogram with event markers, as a PNG."""
    import cv2

    m = to_mono(x)
    dur = len(m) / sr
    wave_h = int(height * 0.28)
    spec_h = height - wave_h - 30
    img = np.full((height, width, 3), (22, 18, 16), np.uint8)
    # waveform (min/max per column)
    cols = np.array_split(to_stereo(x), width)
    mid = 30 + wave_h // 2
    for i, c in enumerate(cols):
        if len(c) == 0:
            continue
        for ch, col in ((0, (240, 190, 90)), (1, (120, 200, 250))):
            lo, hi = float(c[:, ch].min()), float(c[:, ch].max())
            y0 = int(mid - hi * wave_h / 2 * 0.95)
            y1 = int(mid - lo * wave_h / 2 * 0.95)
            cv2.line(img, (i, y0), (i, y1), col, 1)
    # spectrogram
    if len(m) > 2048:
        f, t, Z = signal.stft(m, sr, nperseg=2048, noverlap=2048 - 512)
        S = 20 * np.log10(np.abs(Z) + 1e-9)
        S = np.clip((S + 100) / 100, 0, 1)
        fmin, fmax = 20.0, sr / 2
        ys = np.geomspace(fmin, fmax, spec_h)[::-1]
        rows = np.clip(np.searchsorted(f, ys), 0, len(f) - 1)
        Sr = S[rows]
        Sr = cv2.resize(Sr.astype(np.float32), (width, spec_h), interpolation=cv2.INTER_LINEAR)
        cm = cv2.applyColorMap((Sr * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
        img[height - spec_h :, :] = cm
    # events
    for e in events or []:
        x_ = int(e["t"] / max(dur, 1e-9) * width)
        cv2.line(img, (x_, 30), (x_, height), (90, 255, 140), 1)
        cv2.putText(img, str(e.get("name", ""))[:18], (x_ + 3, 44), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (90, 255, 140), 1, cv2.LINE_AA)
    # time ruler
    for s in range(int(dur) + 1):
        x_ = int(s / max(dur, 1e-9) * width)
        cv2.line(img, (x_, 0), (x_, 12), (200, 200, 200), 1)
        cv2.putText(img, f"{s}s", (x_ + 2, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1, cv2.LINE_AA)
    if title:
        cv2.putText(img, title[:80], (width - 10 - 8 * len(title[:80]), 24), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (230, 230, 230), 1, cv2.LINE_AA)
    cv2.imwrite(path, img)
    return path


def analyze(x: np.ndarray, sr: int = SR) -> dict:
    return {
        "duration_s": round(len(x) / sr, 4),
        "lufs": round(lufs(x, sr), 2),
        "true_peak_dbtp": round(true_peak_db(x), 2),
        "sample_peak_db": round(gain_to_db(float(np.max(np.abs(x))) if len(x) else 0.0), 2),
        "onsets_s": [round(t, 3) for t in onsets(x, sr)][:200],
    }


__all__ = [
    "GLASS_RATIOS", "Mix", "Placement", "SR", "analyze", "clack", "click", "convolve", "db_to_gain", "env_adsr", "env_exp",
    "envelope_follower", "fade", "fit_length", "fm_bell", "gain_to_db", "glass_ping", "granular", "highpass", "lowpass",
    "lufs", "master", "noise", "normalize_lufs", "onsets", "osc", "pan_path", "pan_stereo", "read_audio", "reverb_ir",
    "riser", "samples", "schroeder_reverb", "shimmer", "sidechain_duck", "spectrogram_png", "sub_boom", "thump",
    "to_mono", "to_stereo", "true_peak_db", "true_peak_limit", "whoosh", "write_wav",
]

