import subprocess

import cv2
import numpy as np
import pytest
from main import run

from luma_engine.toolkit import make_test_ctx


def _video(tmp_path, img):
    cv2.imwrite(str(tmp_path / "card.png"), img)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(tmp_path / "card.png"), "-t", "0.5", "-r", "12", "-c:v", "libx264",
                    "-pix_fmt", "yuv444p", "-qp", "0", str(tmp_path / "v.mp4")], check=True)


def _card():
    img = np.full((120, 160, 3), (42, 15, 11), np.uint8)
    cv2.circle(img, (80, 60), 30, (71, 181, 255), -1)
    return img


def test_identical_end_frame_passes(tmp_path):
    _video(tmp_path, _card())
    out = run({"video": "v.mp4", "reference": "card.png"}, make_test_ctx(tmp_path, workspace=tmp_path))
    assert out["pass"] and out["mean_abs_diff"] <= 1.5  # RGB→YUV→RGB rounding


def test_a_moved_mark_fails(tmp_path):
    _video(tmp_path, _card())
    ref = _card()
    ref[:] = (42, 15, 11)
    cv2.circle(ref, (100, 60), 30, (71, 181, 255), -1)
    cv2.imwrite(str(tmp_path / "ref.png"), ref)
    ctx = make_test_ctx(tmp_path, workspace=tmp_path)
    out = run({"video": "v.mp4", "reference": "ref.png"}, ctx)
    assert not out["pass"] and out["max_abs_diff"] > 100 and ctx.images


def test_size_mismatch_is_explained(tmp_path):
    _video(tmp_path, _card())
    cv2.imwrite(str(tmp_path / "small.png"), np.zeros((10, 10, 3), np.uint8))
    with pytest.raises(ValueError, match="size mismatch"):
        run({"video": "v.mp4", "reference": "small.png"}, make_test_ctx(tmp_path, workspace=tmp_path))
