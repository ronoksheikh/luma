"""Hidden board elements: fit the background gradient, subtract it, find what is left."""

import cv2
import numpy as np


def _design(xs, ys, degree):
    cols = [xs**i * ys**j for i in range(degree + 1) for j in range(degree + 1 - i)]
    return np.stack(cols, axis=-1)


def fit_surface(img, degree, exclude, step, iters=4):
    """Robust (iteratively trimmed) least-squares polynomial surface per channel, in [0, 1] coordinates."""
    H, W = img.shape[:2]
    ys, xs = np.mgrid[0:H:step, 0:W:step]
    xn, yn = xs / max(W - 1, 1), ys / max(H - 1, 1)
    A = _design(xn.ravel(), yn.ravel(), degree)
    samples = img[::step, ::step].reshape(-1, img.shape[2]).astype(np.float64)
    keep = np.ones(len(samples), bool)
    if exclude is not None:
        keep &= ~exclude[::step, ::step].ravel()
    coef = None
    for _ in range(iters):
        coef, *_ = np.linalg.lstsq(A[keep], samples[keep], rcond=None)
        r = np.abs(samples - A @ coef).max(axis=1)
        sig = 1.4826 * np.median(r[keep]) + 1e-6
        keep = (r < 3 * sig) & (True if exclude is None else ~exclude[::step, ::step].ravel())
    yy, xx = np.mgrid[0:H, 0:W]
    model = _design(xx / max(W - 1, 1), yy / max(H - 1, 1), degree) @ coef
    return model, coef


def run(params, ctx):
    img = cv2.imread(str(ctx.path(params["image"])), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"cannot read {params['image']}")
    img = img.astype(np.float64)
    H, W = img.shape[:2]
    exclude = None
    if params.get("exclude_mask"):
        m = cv2.imread(str(ctx.path(params["exclude_mask"])), cv2.IMREAD_GRAYSCALE)
        if m is None or m.shape != (H, W):
            raise ValueError("exclude_mask must be a readable image the same size as the board")
        exclude = m > 127
    step = max(1, int(max(H, W) / 256))
    ctx.progress(20, "fitting the background")
    model, coef = fit_surface(img, int(params.get("degree", 2)), exclude, step)
    resid = img - model
    mag = np.abs(resid).max(axis=2)
    sig = float(1.4826 * np.median(mag) + 1e-6)
    mask = (mag > float(params.get("threshold_sigma", 4.0)) * sig).astype(np.uint8) * 255
    if exclude is not None:
        mask[exclude] = 0
    # drop isolated noise pixels but keep 1-px-wide lines (an opening would erase them)
    n, lab, st, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    small = np.where(st[:, cv2.CC_STAT_AREA] < 8)[0]
    mask[np.isin(lab, small[small > 0])] = 0
    ctx.progress(70, "finding line elements")
    diag = float(np.hypot(W, H))
    segs = cv2.HoughLinesP(mask, 1, np.pi / 360, threshold=40, minLineLength=float(params.get("min_line_length", 0.08)) * diag, maxLineGap=6)
    lines = []
    for x0, y0, x1, y1 in (segs.reshape(-1, 4) if segs is not None else []):
        pts = np.linspace([x0, y0], [x1, y1], 50).round().astype(int)
        delta = resid[pts[:, 1].clip(0, H - 1), pts[:, 0].clip(0, W - 1)].mean(axis=0)
        lines.append({"p0": [int(x0), int(y0)], "p1": [int(x1), int(y1)], "length": float(np.hypot(x1 - x0, y1 - y0)),
                      "angle_deg": float(np.degrees(np.arctan2(y1 - y0, x1 - x0))), "delta_bgr": [round(float(v), 2) for v in delta]})
    lines = _merge(lines)
    corners = {}
    for name, (y, x) in {"top_left": (0, 0), "top_right": (0, W - 1), "bottom_left": (H - 1, 0), "bottom_right": (H - 1, W - 1)}.items():
        b, g, r = np.clip(model[y, x], 0, 255).round().astype(int)
        corners[name] = f"#{r:02X}{g:02X}{b:02X}"
    out = {}
    for key, arr in (("background_model", np.clip(model, 0, 255)), ("residual_x8", np.clip(128 + resid * 8, 0, 255)), ("elements_mask", mask)):
        p = ctx.out(f"{key}.png")
        cv2.imwrite(str(p), arr.astype(np.uint8))
        out[key] = str(p)
    ctx.emit_image(out["residual_x8"])
    ctx.emit_image(out["elements_mask"])
    return {"corners": corners, "coefficients": np.asarray(coef).T.round(4).tolist(), "residual_sigma": sig,
            "element_fraction": float((mask > 0).mean()), "lines": lines, "images": out}


def _merge(lines, ang_tol=2.0, dist_tol=4.0):
    """Hough returns several overlapping segments per line: keep the longest per (angle, offset)."""
    kept = []
    for ln in sorted(lines, key=lambda d: -d["length"]):
        a = np.radians(ln["angle_deg"])
        n = np.array([-np.sin(a), np.cos(a)])
        off = float(n @ np.array(ln["p0"]))
        dup = False
        for k in kept:
            da = abs((ln["angle_deg"] - k["angle_deg"] + 90) % 180 - 90)
            if da < ang_tol and abs(off - k["_off"]) < dist_tol:
                dup = True
                break
        if not dup:
            kept.append({**ln, "_off": off})
    return [{k: v for k, v in d.items() if k != "_off"} for d in kept]
