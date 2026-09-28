"""Quality control for delivered videos.

``qc_report(path, ...)`` returns ``{"pass": bool, "checks": [...], "stats": {...}}``
where each check is ``{"name", "pass", "value", "expected", "detail"}``.  Checks:

* exact frame count, fps and duration
* first frame black (when requested)
* flicker / discontinuity spikes (frame-difference outliers not explained by events)
* final frames still
* final frame matches the reference lockup (mean absolute difference, 0–255 scale)
* audio true peak ≤ −1 dBTP, integrated loudness within target ± tolerance
* audio length equals video length
* A/V sync of marked events (audio onsets vs event times)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

import numpy as np

from . import audio as A
from .encode import decode_audio, decode_frames, load_png, video_info

SYNC_KINDS = {"impact", "seat", "hit", "boom", "click", "sync"}


def _check(name, ok, value=None, expected=None, detail="", severity="error") -> dict:
    return {"name": name, "pass": bool(ok), "value": value, "expected": expected, "detail": detail, "severity": severity}


def frame_stats(path: str, width: int = 480) -> dict:
    info = video_info(path)
    h = int(round(info["height"] * width / info["width"] / 2) * 2)
    diffs, lumas, maxes = [], [], []
    prev = None
    first = None
    last_small = []
    for f in decode_frames(path, width, h):
        g = f.astype(np.float32)
        if first is None:
            first = g
        lumas.append(float((0.2126 * g[..., 0] + 0.7152 * g[..., 1] + 0.0722 * g[..., 2]).mean()))
        maxes.append(float(g.max()))
        if prev is not None:
            diffs.append(float(np.abs(g - prev).mean()))
        prev = g
        last_small.append(g)
        if len(last_small) > 90:
            last_small.pop(0)
    return {"info": info, "diffs": diffs, "lumas": lumas, "maxes": maxes, "first": first, "tail": last_small}


def detect_spikes(diffs: Sequence[float], fps: float, events: Sequence[dict] | None = None, k: float = 5.0,
                  min_abs: float = 3.0, window: int = 6, allow_frames: int = 3) -> list[dict]:
    d = np.asarray(diffs, dtype=np.float64)
    out = []
    # an event explains spikes from 2 frames before it until ~0.6 s after (impacts
    # legitimately trigger flashes, floods and fast motion right after them)
    spans = [(e["t"] * fps - allow_frames, e["t"] * fps + max(allow_frames, 0.6 * fps if e.get("kind") in ("impact", "boom", "hit", "seat", "reveal", "cut", "flash") else allow_frames))
             for e in (events or [])]
    for i in range(len(d)):
        lo, hi = max(0, i - window), min(len(d), i + window + 1)
        neigh = np.concatenate([d[lo:i], d[i + 1 : hi]])
        base = float(np.median(neigh)) if len(neigh) else 0.0
        if d[i] > min_abs and d[i] > k * max(base, 0.4):
            frame = i + 1  # diff i is between frames i and i+1
            explained = any(a <= frame <= b for a, b in spans)
            out.append({"frame": frame, "t": round(frame / fps, 4), "diff": round(float(d[i]), 3), "baseline": round(base, 3), "explained": explained})
    return out


def qc_report(path: str, *, expected_frames: int | None = None, expected_fps: float | None = None,
              expected_duration: float | None = None, first_black: bool = False, reference: str | None = None,
              reference_max_mad: float = 2.0, events: Sequence[dict] | None = None, still_tail_s: float = 0.5,
              target_lufs: float | None = -14.0, lufs_tol: float = 1.0, true_peak_max: float = -1.0,
              sync_tol_frames: float = 2.0, require_audio: bool = True, out_json: str | None = None) -> dict:
    checks: list[dict] = []
    st = frame_stats(path)
    info = st["info"]
    fps = info.get("fps", 0.0)
    frames = info.get("frames", 0)
    dur = frames / fps if fps else 0.0

    if expected_frames is not None:
        checks.append(_check("frame_count", frames == expected_frames, frames, expected_frames))
    if expected_fps is not None:
        checks.append(_check("fps", abs(fps - expected_fps) < 1e-3, round(fps, 4), expected_fps))
    if expected_duration is not None:
        checks.append(_check("duration", abs(dur - expected_duration) < 0.5 / max(fps, 1), round(dur, 4), expected_duration,
                             "duration = frames / fps"))
    if first_black:
        fm = st["maxes"][0] if st["maxes"] else 255
        checks.append(_check("first_frame_black", fm <= 2.0, fm, "max ≤ 2 (of 255)"))

    spikes = detect_spikes(st["diffs"], fps, events)
    unexplained = [s for s in spikes if not s["explained"]]
    checks.append(_check("flicker_discontinuity", not unexplained, len(unexplained), 0,
                         "; ".join(f"frame {s['frame']} diff {s['diff']} (baseline {s['baseline']})" for s in unexplained[:5]),
                         severity="warning"))

    n_tail = max(2, int(round(still_tail_s * fps))) if fps else 2
    tail = st["tail"][-n_tail:]
    if len(tail) >= 2:
        tail_diff = max(float(np.abs(tail[i] - tail[i - 1]).max()) for i in range(1, len(tail)))
        tail_mean = max(float(np.abs(tail[i] - tail[i - 1]).mean()) for i in range(1, len(tail)))
        checks.append(_check("final_frames_still", tail_mean < 0.25 and tail_diff <= 12, {"max": tail_diff, "mean": round(tail_mean, 4)},
                             f"last {n_tail} frames identical (mean<0.25)"))

    if reference:
        ref = load_png(reference)
        last = None
        for f in decode_frames(path):
            last = f
        if last is not None:
            import cv2

            if ref.shape[:2] != last.shape[:2]:
                ref = cv2.resize(ref, (last.shape[1], last.shape[0]), interpolation=cv2.INTER_AREA)
            mad = float(np.abs(ref * 255.0 - last.astype(np.float32)).mean())
            p99 = float(np.percentile(np.abs(ref * 255.0 - last.astype(np.float32)), 99))
            checks.append(_check("final_frame_matches_reference", mad <= reference_max_mad, round(mad, 4), f"≤ {reference_max_mad}",
                                 f"mean abs diff (0-255); p99 {p99:.2f}"))

    audio_stats = {}
    if info.get("has_audio"):
        x = decode_audio(path)
        a_dur = len(x) / 48000
        tp = A.true_peak_db(x)
        lu = A.lufs(x)
        audio_stats = {"duration": round(a_dur, 4), "true_peak_dbtp": round(tp, 2), "lufs": round(lu, 2)}
        checks.append(_check("audio_true_peak", tp <= true_peak_max + 0.05, round(tp, 2), f"≤ {true_peak_max} dBTP"))
        if target_lufs is not None:
            checks.append(_check("audio_loudness", abs(lu - target_lufs) <= lufs_tol, round(lu, 2), f"{target_lufs} ± {lufs_tol} LUFS"))
        checks.append(_check("audio_length_matches_video", abs(a_dur - dur) <= 1.0 / max(fps, 1) + 1e-6, round(a_dur, 4), round(dur, 4)))
        sync_events = [e for e in (events or []) if e.get("kind") in SYNC_KINDS or e.get("meta", {}).get("sync")]
        if sync_events:
            ons = np.array(A.onsets(x))
            offs = []
            for e in sync_events:
                if len(ons) == 0:
                    offs.append({"name": e["name"], "t": e["t"], "offset_ms": None})
                    continue
                j = int(np.argmin(np.abs(ons - e["t"])))
                offs.append({"name": e["name"], "t": e["t"], "onset": round(float(ons[j]), 4), "offset_ms": round(float(ons[j] - e["t"]) * 1000, 1)})
            tol_ms = sync_tol_frames / max(fps, 1) * 1000
            bad = [o for o in offs if o["offset_ms"] is None or abs(o["offset_ms"]) > tol_ms]
            checks.append(_check("av_sync_events", not bad, offs, f"|offset| ≤ {tol_ms:.1f} ms",
                                 f"{len(offs) - len(bad)}/{len(offs)} events within tolerance"))
    elif require_audio:
        checks.append(_check("has_audio", False, False, True))

    # toolbox qc_check plugins: check(video_path, info) -> {name, pass, value?, expected?, detail?, severity?}
    try:
        from . import plugins as _plugins

        for plug in _plugins.list_plugins("qc_check"):
            try:
                mod = _plugins.load(plug["name"])
                r = mod.check(path, dict(info))
                checks.append(_check(f"plugin:{r.get('name') or plug['name']}", bool(r.get("pass")), r.get("value"), r.get("expected"),
                                     r.get("detail", ""), r.get("severity", "warning")))
            except Exception as e:  # noqa: BLE001 — a broken plugin never breaks QC
                checks.append(_check(f"plugin:{plug['name']}", False, None, None, f"plugin error: {type(e).__name__}: {e}", "warning"))
    except Exception:  # noqa: BLE001
        pass

    ok = all(c["pass"] for c in checks if c["severity"] == "error")
    report = {
        "pass": ok,
        "path": path,
        "checks": checks,
        "stats": {
            "video": {k: info.get(k) for k in ("width", "height", "fps", "frames", "duration", "vcodec", "profile", "pix_fmt", "color_space", "color_primaries", "color_transfer")},
            "audio": audio_stats,
            "spikes": spikes[:20],
            "mean_luma": [round(v, 2) for v in st["lumas"][:: max(1, len(st["lumas"]) // 120)]],
        },
    }
    if out_json:
        Path(out_json).write_text(json.dumps(report, indent=2))
    return report


def image_stats(path_or_img, crop: Sequence[int] | None = None) -> dict:
    """Numeric description of an image for non-vision models: size, mean colour,
    histogram, sharpness (variance of Laplacian), bright-region boxes."""
    import cv2

    img = load_png(path_or_img) if isinstance(path_or_img, str) else np.asarray(path_or_img, dtype=np.float32)
    if crop:
        x, y, w, h = crop
        img = img[y : y + h, x : x + w]
    u8 = (np.clip(img, 0, 1) * 255).astype(np.uint8)
    gray = cv2.cvtColor(u8, cv2.COLOR_RGB2GRAY)
    hist = np.histogram(gray, bins=16, range=(0, 256))[0]
    thr = max(200, int(np.percentile(gray, 99)))
    _, bw = cv2.threshold(gray, thr - 1, 255, cv2.THRESH_BINARY)
    n, _, stats, _ = cv2.connectedComponentsWithStats(bw)
    boxes = sorted(([int(v) for v in s[:4]] + [int(s[4])] for s in stats[1:]), key=lambda b: -b[4])[:8]
    mean = img.reshape(-1, 3).mean(axis=0)
    from .brand import Color

    return {
        "size": [int(img.shape[1]), int(img.shape[0])],
        "mean_color": Color(*np.clip(mean, 0, 1)).hex,
        "mean_luma": round(float(gray.mean()), 2),
        "histogram_16": hist.tolist(),
        "sharpness": round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 2),
        "black_fraction": round(float((gray <= 2).mean()), 4),
        "bright_regions_xywh_area": boxes,
    }


__all__ = ["detect_spikes", "frame_stats", "image_stats", "qc_report"]
