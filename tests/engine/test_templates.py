"""Templates work with arbitrary SVGs and end pixel-exact on their reference lockup."""
from pathlib import Path

import numpy as np
import pytest

from luma_engine.analysis import analyze_file, sniff
from luma_engine.templates import TEMPLATES, get_template

SAMPLES = Path(__file__).resolve().parents[2] / "samples"

SINGLE_SHAPE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200">
<path d="M100 10 L190 100 L100 190 L10 100 Z M100 60 L140 100 L100 140 L60 100 Z" fill="#3366FF" fill-rule="evenodd"/></svg>"""

LOCKUP = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 100">
<circle cx="50" cy="50" r="40" fill="#22AA88"/><rect x="40" y="10" width="20" height="80" fill="#FFFFFF"/>
<rect x="130" y="30" width="30" height="40" fill="#EEEEEE"/><rect x="170" y="30" width="30" height="40" fill="#EEEEEE"/>
<rect x="210" y="30" width="60" height="40" fill="#EEEEEE"/></svg>"""


@pytest.fixture(params=["sample", "single", "lockup"])
def logo(request, tmp_path):
    if request.param == "sample":
        return str(SAMPLES / "veyra-symbol.svg"), "Veyra"
    p = tmp_path / f"{request.param}.svg"
    p.write_text(SINGLE_SHAPE if request.param == "single" else LOCKUP)
    return str(p), ("Mono" if request.param == "single" else None)


@pytest.mark.parametrize("name", ["fan_unfold", "stroke_reveal", "exploded_assembly"])
def test_template_renders_and_ends_on_reference(name, logo):
    path, wordmark = logo
    cls = get_template(name)
    sc = cls(logo=path, wordmark=wordmark, width=320, height=180, fps=30, duration=5)
    sc.ensure_setup()
    mid = sc.render(sc.duration * 0.3, 1.0, blur=False)
    assert mid.shape == (180, 320, 3) and np.isfinite(mid).all()
    last = sc.render_frame(sc.frame_count - 1, blur=True)
    ref = sc.reference_frame()
    assert np.array_equal(last, ref)  # exact hold
    before = sc.render(sc.t_hold - 1 / sc.fps, blur=False)
    assert np.abs(before - ref).mean() < 3 / 255  # no visible pop into the hold
    assert sc.audio() is not None


def test_fan_unfold_events_and_first_frame_black():
    cls = get_template("fan_unfold")
    sc = cls(logo=str(SAMPLES / "veyra-symbol.svg"), wordmark="Veyra", width=320, height=180, fps=60, duration=5)
    sc.ensure_setup()
    kinds = [e.kind for e in sc.timeline.events]
    assert kinds.count("seat") == 5 and "boom" in kinds
    seats = [e.t for e in sc.timeline.events if e.kind == "seat"]
    assert seats == sorted(seats) and min(np.diff(seats)) > 0.08  # audibly separate
    assert sc.render_frame(0).max() == 0.0


def test_voiced_explainer_extends_duration_and_captions():
    cls = get_template("voiced_explainer")
    words = [{"text": w, "start": 0.3 * i, "end": 0.3 * i + 0.25} for i, w in enumerate("One two three. Four five six seven.".split())]
    sc = cls(logo=str(SAMPLES / "veyra-symbol.svg"), wordmark="Veyra", words=words, width=320, height=180, fps=30, duration=2)
    sc.ensure_setup()
    assert sc.duration >= words[-1]["end"] + 2.0
    cues = sc.captions()
    assert cues[0].text.startswith("One two three")
    img = sc.render(1.0, blur=False)
    assert img.shape == (180, 320, 3)
    assert np.array_equal(sc.render_frame(sc.frame_count - 1), sc.reference_frame())


def test_templates_registry_complete():
    assert set(TEMPLATES) == {"fan_unfold", "exploded_assembly", "stroke_reveal", "voiced_explainer"}


def test_asset_analysis(tmp_path):
    assert sniff(b"\x89PNG\r\n\x1a\n....") == "png"
    assert sniff(b"%PDF-1.7") == "pdf"
    assert sniff(b"<?xml version='1.0'?><svg xmlns='http://www.w3.org/2000/svg'/>") == "svg"
    assert sniff(b"<html><script>") is None
    pdf = analyze_file(str(SAMPLES / "veyra-brand.pdf"))
    assert {"#FF5A5F", "#FFB547", "#0B0F2A"} <= set(pdf["hex_colors_in_text"])
    assert any("Inter" in f for f in pdf["embedded_fonts"] + pdf["font_mentions"])
    import cv2

    img = np.zeros((40, 40, 4), np.uint8)
    img[10:30, 10:30] = (0, 0, 255, 255)
    cv2.imwrite(str(tmp_path / "x.png"), img)
    a = analyze_file(str(tmp_path / "x.png"))
    assert a["has_transparency"] and a["palette"][0]["hex"] == "#FF0000" and a["content_bbox"] == [10, 10, 20, 20]
