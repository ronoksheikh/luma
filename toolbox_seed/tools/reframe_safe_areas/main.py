"""Reframe check: end card per aspect, content inside the safe area."""

import cv2
import numpy as np

from luma_engine.production import reframe_check


def run(params, ctx):
    aspects = params.get("aspects") or ["9:16", "1:1", "4:5"]
    scene = ctx.path(params["scene"])
    res = []
    for i, a in enumerate(aspects):
        if ctx.cancelled():
            break
        ctx.progress(100 * i / len(aspects), f"rendering {a}")
        r = reframe_check(str(scene), [a], str(ctx.out(f"reframe_{a.replace(':', 'x')}")), long_side=int(params.get("long_side", 960)),
                          time=params.get("time"))
        res += r
    tiles = []
    for r in res:
        img = cv2.imread(r["end_card"])
        if img is None:
            continue
        x0, y0, x1, y1 = r["safe_box"]
        cv2.rectangle(img, (x0, y0), (x1, y1), (80, 200, 120) if r["inside_safe_area"] else (60, 60, 255), 2)
        if r["content_box"]:
            cx0, cy0, cx1, cy1 = (int(v) for v in r["content_box"])
            cv2.rectangle(img, (cx0, cy0), (cx1, cy1), (255, 200, 90), 1)
        h = 360
        tiles.append(cv2.resize(img, (max(1, round(img.shape[1] * h / img.shape[0])), h)))
    contact = None
    if tiles:
        contact = ctx.out("contact.png")
        cv2.imwrite(str(contact), np.hstack([np.pad(t, ((8, 8), (8, 8), (0, 0))) for t in tiles]))
        ctx.emit_image(contact)
    out = [{k: r[k] for k in ("aspect", "size", "content_box", "safe_box", "inside_safe_area", "end_card")} for r in res]
    return {"pass": all(r["inside_safe_area"] for r in out) and bool(out), "aspects": out, "contact": str(contact) if contact else None}
