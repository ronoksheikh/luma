"""Fit an SVG mark's placement to a reference image by least squares on coverage."""

import cv2
import numpy as np

from luma_engine.layout import fit_to_reference
from luma_engine.svg import SVGDocument


def reference_alpha(img: np.ndarray, background: str | None) -> np.ndarray:
    if img.ndim == 3 and img.shape[2] == 4 and img[..., 3].min() < 250:
        return img[..., 3].astype(np.float64) / 255.0
    rgb = img[..., :3].astype(np.float64)
    if background:
        h = background.lstrip("#")
        bg = np.array([int(h[i : i + 2], 16) for i in (4, 2, 0)], np.float64)  # BGR
    else:
        corners = np.stack([rgb[0, 0], rgb[0, -1], rgb[-1, 0], rgb[-1, -1]])
        bg = np.median(corners, axis=0)
    d = np.abs(rgb - bg).max(axis=2)
    return np.clip(d / 48.0, 0, 1)


def run(params, ctx):
    img = cv2.imread(str(ctx.path(params["reference"])), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f"cannot read {params['reference']}")
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    H0, W0 = img.shape[:2]
    k = min(1.0, int(params.get("max_side", 640)) / max(H0, W0))
    img = cv2.resize(img, (max(1, round(W0 * k)), max(1, round(H0 * k))), interpolation=cv2.INTER_AREA) if k < 1 else img
    H, W = img.shape[:2]
    ref = reference_alpha(img, params.get("background"))
    doc = SVGDocument.load(ctx.path(params["svg"]))
    vx, vy, vw, vh = doc.view_box

    def render(s, tx, ty):
        m = np.array([[s, 0, tx - vx * s], [0, s, ty - vy * s], [0, 0, 1]], dtype=np.float64)
        return doc.render(W, H, matrix=m)[..., 3]

    s0 = 0.5 * min(W / vw, H / vh)
    ctx.progress(20, "fitting")
    fit = fit_to_reference(render, ref, x0=(s0, W * 0.25, H * 0.25))
    s, tx, ty = fit["s"], fit["tx"], fit["ty"]
    placement = {"x": tx / W, "y": ty / H, "width": vw * s / W, "height": vh * s / H,
                 "center_x": (tx + vw * s / 2) / W, "center_y": (ty + vh * s / 2) / H}
    over = np.dstack([ref * 255] * 3).astype(np.uint8)
    a = render(s, tx, ty)
    over[..., 2] = np.maximum(over[..., 2], (a * 255).astype(np.uint8))
    path = ctx.out("overlay.png")
    cv2.imwrite(str(path), over)
    ctx.emit_image(path)
    ctx.log(f"s={s:.4f} tx={tx:.2f} ty={ty:.2f} rms={fit['rms']:.4f}")
    scale_back = 1 / k
    return {"s": s * scale_back, "tx": tx * scale_back, "ty": ty * scale_back, "rms": fit["rms"], "placement": placement,
            "reference_size": [W0, H0], "overlay": str(path)}
