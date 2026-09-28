"""SVG parsing, transforms, splitting, booleans and structure detection."""
import math
from pathlib import Path

import numpy as np
import pytest
import skia
from shapely.geometry import Polygon

from luma_engine.svg import (
    SVGDocument, SplitError, arc_to_cubics, detect_notches, detect_pivot, detect_symmetry, parse_path_d, parse_transform,
    sanitize_svg, split_polygon, split_radial, split_symbol_wordmark, union_skia,
)

SAMPLES = Path(__file__).resolve().parents[2] / "samples"


def _area(path: skia.Path, n=512):
    """Coverage area by rasterising at high resolution (independent check)."""
    b = path.computeTightBounds()
    s = n / max(b.width(), b.height())
    surf = skia.Surface.MakeRaster(skia.ImageInfo.MakeA8(n + 4, n + 4))
    c = surf.getCanvas()
    c.translate(2 - b.left() * s, 2 - b.top() * s)
    c.scale(s, s)
    c.drawPath(path, skia.Paint(AntiAlias=True))
    a = surf.makeImageSnapshot().toarray(colorType=skia.ColorType.kAlpha_8_ColorType).astype(float) / 255
    return a.sum() / (s * s)


def test_path_commands_absolute_and_relative_equivalent():
    a = parse_path_d("M10 10 L20 10 H30 V20 C30 25 25 30 20 30 S10 25 10 20 Q10 15 15 12 T20 10 Z")
    b = parse_path_d("m10 10 l10 0 h10 v10 c0 5 -5 10 -10 10 s-10 -5 -10 -10 q0 -5 5 -8 t5 -2 z")
    pa, pb = a.flatten(0.01)[0][0], b.flatten(0.01)[0][0]
    assert np.allclose(pa, pb, atol=1e-9)


def test_compact_numbers_and_implicit_lineto():
    g = parse_path_d("M0,0 10-5.5.5 1e1,2e0 3z")
    pts = g.subpaths[0].points()
    assert np.allclose(pts[:4], [[0, 0], [10, -5.5], [0.5, 10], [2, 3]])
    assert g.subpaths[0].closed
    with pytest.raises(ValueError):
        parse_path_d("M0 0 L1")


def test_arc_flags_without_separators_and_circle_area():
    r = 50.0
    g = parse_path_d(f"M{r} 0A{r} {r} 0 1 1 {-r} 0A{r} {r} 0 1 1 {r} 0Z")
    assert _area(g.to_skia()) == pytest.approx(math.pi * r * r, rel=2e-3)
    g2 = parse_path_d(f"M{r} 0a{r} {r} 0 11{-2*r} 0a{r} {r} 0 11{2*r} 0z")
    assert np.allclose(g.flatten(0.01)[0][0], g2.flatten(0.01)[0][0], atol=1e-6)


def test_arc_cubic_accuracy():
    segs = arc_to_cubics((100, 0), (0, 100), 100, 100, 0, False, True)
    # sample each cubic, all points must lie on the circle
    cur = np.array([100.0, 0.0])
    for _, c1, c2, p in segs:
        for t in np.linspace(0, 1, 20):
            q = (1 - t) ** 3 * cur + 3 * (1 - t) ** 2 * t * c1 + 3 * (1 - t) * t * t * c2 + t**3 * p
            assert abs(np.linalg.norm(q) - 100) < 0.03
        cur = p


def test_transforms_compose_in_svg_order():
    m = parse_transform("translate(10,20) rotate(90) scale(2)")
    v = m @ np.array([1, 0, 1])
    assert np.allclose(v[:2], [10, 22])
    m2 = parse_transform("rotate(90 5 5)")
    assert np.allclose((m2 @ np.array([5, 5, 1]))[:2], [5, 5])


def test_document_styles_css_classes_gradients_and_use():
    svg = """<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 100 50">
      <defs><style>.a{fill:#ff0000}</style>
        <linearGradient id="g" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#000"/><stop offset="100%" stop-color="#fff"/></linearGradient>
        <radialGradient id="r2" xlink:href="#g" cx="50%" cy="50%" r="50%"/>
        <rect id="proto" width="10" height="10"/>
      </defs>
      <g transform="translate(5,5)" fill="blue"><rect class="a" width="10" height="10"/><circle cx="30" cy="5" r="5"/></g>
      <rect x="50" y="0" width="20" height="10" fill="url(#g)"/>
      <rect x="50" y="20" width="20" height="10" style="fill:url(#r2)"/>
      <use href="#proto" x="80" y="30" fill="#00ff00"/>
    </svg>"""
    doc = SVGDocument.parse(svg)
    assert len(doc.shapes) == 5
    assert doc.shapes[0].fill.hex == "#FF0000"  # class beats inherited fill
    assert doc.shapes[1].fill.hex == "#0000FF"  # inherited from <g>
    assert doc.shapes[0].bbox() == pytest.approx((5, 5, 10, 10))
    g = doc.shapes[2].fill
    assert g.units == "objectBoundingBox" and len(g.stops) == 2
    r2 = doc.shapes[3].fill
    assert r2.kind == "radial" and len(r2.stops) == 2  # stops inherited via href
    assert doc.shapes[4].bbox() == pytest.approx((80, 30, 10, 10))
    a = doc.analysis()
    assert a["viewBox"] == [0, 0, 100, 50] and "g" in a["gradients"]


def test_sample_logo_analysis_pivot_and_symmetry():
    doc = SVGDocument.load(SAMPLES / "veyra-symbol.svg")
    a = doc.analysis()
    assert a["shape_count"] == 6
    assert a["pivot"] == pytest.approx([256.0, 404.0], abs=0.05)
    assert a["symmetry"]["symmetric"] and a["symmetry"]["axis_deg"] == pytest.approx(90, abs=1)
    assert set(a["colors"]) >= {"#FF5A5F", "#FFB547", "#FFE0A3"}


def test_split_exactness_area_error_below_1e_9():
    doc = SVGDocument.load(SAMPLES / "veyra-symbol.svg")
    poly = doc.shapes[2].polygon(0.01)
    pieces = split_polygon(poly, [((0, 250), (512, 250)), ((256, 0), (256, 500))])
    assert len(pieces) >= 3
    err = abs(sum(p.area for p in pieces) - poly.area) / poly.area
    assert err < 1e-9
    wedges = split_radial(poly, (256, 404), [-90, -60, -120, 90])
    assert abs(sum(p.area for p in wedges) - poly.area) / poly.area < 1e-9


def test_split_raises_when_area_is_lost():
    sq = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    with pytest.raises(SplitError):
        split_polygon(sq, [((0, 5), (10, 5))], tol=-1.0)  # impossible tolerance


def test_union_notches_symbol_wordmark():
    a = parse_path_d("M0 0H10V10H0Z").to_skia()
    b = parse_path_d("M5 5H15V15H5Z").to_skia()
    u = union_skia([a, b])
    assert _area(u) == pytest.approx(175, rel=5e-3)
    L = Polygon([(0, 0), (10, 0), (10, 4), (4, 4), (4, 10), (0, 10)])
    notches = detect_notches(L)
    assert notches and notches[0]["point"] == pytest.approx([4, 4])
    doc = SVGDocument.parse('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 50"><rect width="40" height="40"/>'
                            '<rect x="80" width="20" height="30"/><rect x="110" width="20" height="30"/></svg>')
    sym, wm = split_symbol_wordmark(doc.shapes)
    assert len(sym) == 1 and len(wm) == 2


def test_pivot_from_converging_rays():
    rays = []
    for ang in (-150, -110, -70, -30):
        a = math.radians(ang)
        d = np.array([math.cos(a), math.sin(a)])
        n = np.array([-d[1], d[0]])
        c0, c1 = np.array([50, 80]) + d * 10, np.array([50, 80]) + d * 60
        rays.append(Polygon([c0 - n * 3, c1 - n * 5, c1 + n * 5, c0 + n * 3]))
    assert detect_pivot(rays) == pytest.approx([50, 80], abs=1e-6)
    sq = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    assert detect_symmetry(sq)["symmetric"]


def test_sanitize_svg_strips_active_content():
    dirty = ('<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><script>alert(2)</script>'
             '<a href="javascript:alert(3)"><rect width="1" height="1" onclick="x()"/></a>'
             '<image href="https://evil.example/x.png"/><foreignObject><div/></foreignObject>'
             '<style>@import url(http://evil); .a{fill:url(http://x/y)}</style></svg>')
    clean = sanitize_svg(dirty)
    low = clean.lower()
    for bad in ("onload", "onclick", "<script", "javascript:", "evil", "foreignobject", "@import"):
        assert bad not in low
    with pytest.raises(Exception):
        sanitize_svg('<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]><svg>&x;</svg>')
