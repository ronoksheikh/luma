"""Compare a video's last frame with the reference end card."""

import subprocess

import cv2
import numpy as np


def last_frame(video, out, from_end):
    if from_end > 0:  # the first frame after seeking to `from_end` before the end
        tail = ["-sseof", f"-{from_end:.3f}", "-i", str(video), "-frames:v", "1"]
    else:  # decode the last second and keep overwriting: the file ends up holding the very last frame
        tail = ["-sseof", "-1", "-i", str(video)]
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", *tail, "-update", "1", str(out)], capture_output=True, text=True, timeout=90)
    img = cv2.imread(str(out), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"could not decode the last frame of {video}: {r.stderr.strip()[-300:]}")
    return img


def run(params, ctx):
    ref = cv2.imread(str(ctx.path(params["reference"])), cv2.IMREAD_COLOR)
    if ref is None:
        raise ValueError(f"cannot read the reference {params['reference']}")
    frame = last_frame(ctx.path(params["video"]), ctx.out("last_frame.png"), float(params.get("from_end_s", 0.0)))
    if frame.shape != ref.shape:
        raise ValueError(f"size mismatch: video frame {frame.shape[1]}×{frame.shape[0]} vs reference {ref.shape[1]}×{ref.shape[0]} "
                         "— export the end card at the video size")
    d = np.abs(frame.astype(np.int16) - ref.astype(np.int16)).max(axis=2)
    mad, mx = float(d.mean()), float(d.max())
    heat = cv2.applyColorMap(np.clip(d * 16, 0, 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
    hp = ctx.out("diff_heatmap.png")
    cv2.imwrite(str(hp), heat)
    ctx.emit_image(hp)
    ok = mad <= float(params.get("max_mad", 2.0))
    ctx.log(f"mean |Δ| {mad:.3f}, max {mx:.0f} → {'PASS' if ok else 'FAIL'}")
    return {"pass": ok, "mean_abs_diff": round(mad, 4), "max_abs_diff": mx, "pct_pixels_over_8": round(float((d > 8).mean() * 100), 3),
            "heatmap": str(hp), "last_frame": str(ctx.out("last_frame.png"))}
