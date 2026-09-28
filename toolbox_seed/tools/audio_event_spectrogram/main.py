"""Spectrogram with event markers + onset offsets per event."""

import json

import numpy as np

from luma_engine import audio as A


def _events(spec, ctx):
    if isinstance(spec, str):
        data = json.loads(ctx.path(spec).read_text())
        spec = data.get("events", data) if isinstance(data, dict) else data
    out = []
    for e in spec:
        if isinstance(e, dict) and "t" in e:
            out.append({"t": float(e["t"]), "name": str(e.get("name") or e.get("label") or "event")})
    if not out:
        raise ValueError("no events with a time `t` found")
    return sorted(out, key=lambda e: e["t"])


def run(params, ctx):
    x = A.read_audio(str(ctx.path(params["audio"])))
    events = _events(params["events"], ctx)
    if params.get("only_named"):
        keep = set(params["only_named"])
        events = [e for e in events if e["name"] in keep]
    ons = np.asarray(A.onsets(x, threshold=float(params.get("onset_threshold", 0.3))))
    tol_ms = float(params.get("tolerance_frames", 2)) / float(params.get("fps", 60)) * 1000
    rows = []
    for e in events:
        if ons.size:
            j = int(np.argmin(np.abs(ons - e["t"])))
            off = (float(ons[j]) - e["t"]) * 1000
            rows.append({**e, "onset": round(float(ons[j]), 4), "offset_ms": round(off, 1), "pass": abs(off) <= tol_ms})
        else:
            rows.append({**e, "onset": None, "offset_ms": None, "pass": False})
    path = ctx.out("spectrogram.png")
    A.spectrogram_png(x, str(path), events=events, title=f"{len(events)} events · tolerance ±{tol_ms:.0f} ms")
    ctx.emit_image(path)
    bad = [r for r in rows if not r["pass"]]
    ctx.log(f"{len(rows) - len(bad)}/{len(rows)} events within ±{tol_ms:.1f} ms")
    return {"pass": not bad, "events": rows, "onsets": [round(float(o), 4) for o in ons[:200]], "tolerance_ms": round(tol_ms, 2),
            "duration_s": round(len(x) / A.SR, 3), "spectrogram": str(path)}
