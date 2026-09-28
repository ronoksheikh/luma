"""Palette with shares and WCAG contrast."""

import cv2
import numpy as np

from luma_engine.production import extract_palette


def _lum(hex_):
    h = hex_.lstrip("#")
    c = np.array([int(h[i : i + 2], 16) for i in (0, 2, 4)]) / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return float(c @ [0.2126, 0.7152, 0.0722])


def contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return round((la + 0.05) / (lb + 0.05), 2)


def run(params, ctx):
    pal = extract_palette(str(ctx.path(params["path"])), k=int(params.get("k", 6)))
    cols = [c for c in pal["colors"] if c["share"] >= float(params.get("min_share", 0.01))]
    for c in cols:
        c["contrast_white"] = contrast(c["hex"], "#FFFFFF")
        c["contrast_black"] = contrast(c["hex"], "#000000")
        c["text_on_it"] = "#FFFFFF" if c["contrast_white"] >= c["contrast_black"] else "#000000"
    pairs = []
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            r = contrast(cols[i]["hex"], cols[j]["hex"])
            pairs.append({"a": cols[i]["hex"], "b": cols[j]["hex"], "contrast": r, "aa_text": r >= 4.5, "aa_large": r >= 3.0})
    pairs.sort(key=lambda p: -p["contrast"])
    sw = np.zeros((120, max(1, len(cols)) * 120, 3), np.uint8)
    for i, c in enumerate(cols):
        h = c["hex"].lstrip("#")
        sw[:, i * 120 : (i + 1) * 120] = [int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16)]
        cv2.putText(sw, c["hex"], (i * 120 + 8, 110), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255) if c["text_on_it"] == "#FFFFFF" else (0, 0, 0), 1, cv2.LINE_AA)
    path = ctx.out("swatch.png")
    cv2.imwrite(str(path), sw)
    ctx.emit_image(path)
    return {"colors": cols, "declared": pal.get("declared") or [], "pairs": pairs[:30], "swatch": str(path)}
