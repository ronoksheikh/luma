import cv2
import numpy as np
import pytest
from main import run

from luma_engine.toolkit import make_test_ctx


def _board(tmp_path):
    H, W = 240, 360
    yy, xx = np.mgrid[0:H, 0:W] / np.array([H - 1, W - 1])[:, None, None]
    img = np.zeros((H, W, 3))
    img[..., 2] = 30 + 60 * xx  # R ramps left → right
    img[..., 1] = 20 + 30 * yy
    img[..., 0] = 60 + 20 * xx * yy
    cv2.line(img, (20, 200), (340, 40), (img[..., 0].mean() + 5, img[..., 1].mean() + 5, img[..., 2].mean() + 5), 1, cv2.LINE_AA)
    img[:, 60:61] += 4.0  # a faint vertical rule: +4/255
    noise = np.random.default_rng(0).normal(0, 0.4, img.shape)
    p = tmp_path / "board.png"
    cv2.imwrite(str(p), np.clip(img + noise, 0, 255).round().astype(np.uint8))
    return p


def test_reconstructs_gradient_and_finds_faint_lines(tmp_path):
    _board(tmp_path)
    ctx = make_test_ctx(tmp_path, workspace=tmp_path)
    out = run({"image": "board.png", "degree": 2}, ctx)
    tl, br = out["corners"]["top_left"], out["corners"]["bottom_right"]
    assert abs(int(tl[1:3], 16) - 30) <= 2 and abs(int(br[1:3], 16) - 90) <= 2  # red channel 30 → 90
    assert len(out["lines"]) >= 2
    angles = sorted(abs(ln["angle_deg"]) for ln in out["lines"])
    assert any(a > 85 for a in angles)  # the vertical rule
    assert any(20 < a < 35 for a in angles)  # the diagonal
    assert out["residual_sigma"] < 2.0 and len(ctx.images) == 2


def test_mask_size_mismatch_is_rejected(tmp_path):
    _board(tmp_path)
    cv2.imwrite(str(tmp_path / "m.png"), np.zeros((10, 10), np.uint8))
    with pytest.raises(ValueError, match="same size"):
        run({"image": "board.png", "exclude_mask": "m.png"}, make_test_ctx(tmp_path, workspace=tmp_path))
