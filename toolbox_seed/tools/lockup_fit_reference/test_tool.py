from pathlib import Path

import cv2
import numpy as np
import pytest
from main import run

from luma_engine.svg import SVGDocument
from luma_engine.toolkit import make_test_ctx

FIX = Path(__file__).parent / "fixtures"


def _board(tmp_path, s, tx, ty, W=400, H=300):
    doc = SVGDocument.load(FIX / "mark.svg")
    vx, vy, _, _ = doc.view_box
    m = np.array([[s, 0, tx - vx * s], [0, s, ty - vy * s], [0, 0, 1]])
    a = doc.render(W, H, matrix=m)[..., 3]
    img = np.full((H, W, 3), (40, 20, 12), np.uint8)
    img[a > 0] = (np.array([60, 180, 255]) * a[a > 0, None] + np.array([40, 20, 12]) * (1 - a[a > 0, None])).astype(np.uint8)
    p = tmp_path / "board.png"
    cv2.imwrite(str(p), img)
    return p


def test_recovers_a_known_placement(tmp_path):
    ref = _board(tmp_path, 0.42, 131.0, 58.0)
    ctx = make_test_ctx(tmp_path, workspace=tmp_path)
    out = run({"reference": ref.name, "svg": str(FIX / "mark.svg")}, ctx)
    assert out["s"] == pytest.approx(0.42, rel=0.02)
    assert out["tx"] == pytest.approx(131.0, abs=2.0)
    assert out["ty"] == pytest.approx(58.0, abs=2.0)
    assert out["rms"] < 0.08
    assert 0 < out["placement"]["center_x"] < 1 and ctx.images


def test_unreadable_reference_is_an_error(tmp_path):
    (tmp_path / "x.png").write_text("not an image")
    with pytest.raises(ValueError, match="cannot read"):
        run({"reference": "x.png", "svg": str(FIX / "mark.svg")}, make_test_ctx(tmp_path, workspace=tmp_path))
