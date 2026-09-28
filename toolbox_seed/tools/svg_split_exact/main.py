"""Split a logo shape into parts with an exact area check (radial around the pivot, or along lines)."""

import math

import numpy as np
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

from luma_engine.svg import SVGDocument, SplitError, detect_notches, detect_pivot, split_polygon, split_radial

COLORS = [(255, 90, 95), (255, 181, 71), (80, 200, 170), (90, 140, 255), (200, 120, 255), (255, 220, 120)]


def _geometry(doc, shape):
    if shape is None:
        polys = [s.polygon(0.01) for s in doc.shapes]
        return unary_union(polys), polys
    if isinstance(shape, int):
        if not 0 <= shape < len(doc.shapes):
            raise ValueError(f"shape index {shape} out of range (0..{len(doc.shapes) - 1})")
        s = doc.shapes[shape]
    else:
        match = [s for s in doc.shapes if getattr(s, "id", None) == shape]
        if not match:
            raise ValueError(f"no shape with id {shape!r}")
        s = match[0]
    p = s.polygon(0.01)
    return p, [p]


def _svg_path(poly) -> str:
    out = []
    for g in getattr(poly, "geoms", [poly]):
        for ring in [g.exterior, *g.interiors]:
            xy = np.asarray(ring.coords)
            out.append("M" + " L".join(f"{x:.4f} {y:.4f}" for x, y in xy[:-1]) + " Z")
    return " ".join(out)


def run(params, ctx):
    doc = SVGDocument.load(ctx.path(params["svg"]))
    geom, pieces_in = _geometry(doc, params.get("shape"))
    tol = float(params.get("tol", 1e-9))
    notches = detect_notches(geom)[:8] if isinstance(geom, (Polygon, MultiPolygon)) else []
    pivot = params.get("pivot")
    if pivot is None:
        pivot = detect_pivot(pieces_in if len(pieces_in) > 1 else [geom]).tolist()
    ctx.progress(30, "splitting")
    try:
        if params.get("mode", "radial") == "lines":
            if not params.get("lines"):
                raise ValueError("lines mode needs `lines`")
            parts = split_polygon(geom, [tuple(map(tuple, ln)) for ln in params["lines"]], tol=tol)
        else:
            angles = params.get("angles_deg")
            if not angles:
                n = int(params.get("n_parts", 5))
                angles = [-90 + i * 360 / n for i in range(n)]
            parts = split_radial(geom, pivot, angles, tol=tol)
    except SplitError as e:
        raise ValueError(f"split rejected: {e}") from None
    area_error = abs(sum(p.area for p in parts) - geom.area) / max(geom.area, 1e-300)
    vx, vy, vw, vh = doc.view_box
    out = []
    for i, p in enumerate(parts):
        f = ctx.out(f"part_{i:02d}.svg")
        f.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vx} {vy} {vw} {vh}"><path d="{_svg_path(p)}"/></svg>\n')
        c = p.centroid
        out.append({"index": i, "svg": str(f.relative_to(ctx.workspace)) if ctx.workspace in f.parents else str(f), "area": p.area,
                    "centroid": [c.x, c.y], "angle_deg": math.degrees(math.atan2(c.y - pivot[1], c.x - pivot[0]))})
    preview = _preview(parts, doc.view_box, pivot, ctx.out("preview.png"))
    ctx.emit_image(preview)
    ctx.log(f"{len(parts)} parts, area error {area_error:.2e}")
    return {"parts": out, "area_error": area_error, "pivot": [float(pivot[0]), float(pivot[1])],
            "notches": [{"point": [float(n["point"][0]), float(n["point"][1])], "turn_deg": float(n["turn_deg"])} for n in notches],
            "preview": str(preview)}


def _preview(parts, view_box, pivot, path):
    import cv2

    vx, vy, vw, vh = view_box
    s = 800 / max(vw, vh)
    img = np.full((int(vh * s) + 1, int(vw * s) + 1, 3), 24, np.uint8)
    for i, p in enumerate(parts):
        for g in getattr(p, "geoms", [p]):
            pts = ((np.asarray(g.exterior.coords) - [vx, vy]) * s).astype(np.int32)
            cv2.fillPoly(img, [pts], COLORS[i % len(COLORS)][::-1])
            cv2.polylines(img, [pts], True, (20, 20, 20), 1, cv2.LINE_AA)
    cv2.circle(img, (int((pivot[0] - vx) * s), int((pivot[1] - vy) * s)), 5, (255, 255, 255), -1, cv2.LINE_AA)
    cv2.imwrite(str(path), img)
    return path
