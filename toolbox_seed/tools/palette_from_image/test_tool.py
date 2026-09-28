import cv2
import numpy as np
import pytest
from main import contrast, run

from luma_engine.toolkit import make_test_ctx


def test_three_flat_colours_are_found_with_shares(tmp_path):
    img = np.zeros((90, 300, 3), np.uint8)
    img[:, :150] = (95, 90, 255)  # BGR of #FF5A5F
    img[:, 150:250] = (71, 181, 255)  # #FFB547
    img[:, 250:] = (42, 15, 11)  # #0B0F2A
    cv2.imwrite(str(tmp_path / "b.png"), img)
    ctx = make_test_ctx(tmp_path, workspace=tmp_path)
    out = run({"path": "b.png", "k": 3}, ctx)
    def rgb(h):
        return np.array([int(h[i : i + 2], 16) for i in (1, 3, 5)])

    for want in ("#FF5A5F", "#FFB547", "#0B0F2A"):  # k-means in Lab: within a few codes
        assert min(np.abs(rgb(c["hex"]) - rgb(want)).max() for c in out["colors"]) <= 4, (want, out["colors"])
    top = max(out["colors"], key=lambda c: c["share"])
    assert np.abs(rgb(top["hex"]) - rgb("#FF5A5F")).max() <= 4 and top["share"] == pytest.approx(0.5, abs=0.02)
    assert out["pairs"][0]["contrast"] > 4.5 and ctx.images


def test_wcag_contrast_reference_values():
    assert contrast("#FFFFFF", "#000000") == 21.0
    assert contrast("#777777", "#FFFFFF") == pytest.approx(4.48, abs=0.01)
