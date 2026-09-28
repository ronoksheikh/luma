from pathlib import Path

import pytest
from main import run

from luma_engine.toolkit import make_test_ctx

FIX = Path(__file__).parent / "fixtures"


def test_radial_split_of_fan_is_exact_and_finds_the_pivot(tmp_path):
    ctx = make_test_ctx(tmp_path, workspace=FIX.parent)
    out = run({"svg": "fixtures/fan.svg", "n_parts": 6}, ctx)
    assert out["area_error"] < 1e-9
    assert len(out["parts"]) >= 3
    assert out["pivot"] == pytest.approx([256, 404], abs=1.0)
    assert Path(out["preview"]).exists() and ctx.images


def test_lines_split_of_an_l_shape_and_notch(tmp_path):
    ctx = make_test_ctx(tmp_path, workspace=FIX.parent)
    out = run({"svg": "fixtures/ell.svg", "shape": "ell", "mode": "lines", "lines": [[[4, -1], [4, 11]]], "pivot": [4, 4]}, ctx)
    assert len(out["parts"]) == 2
    assert out["area_error"] < 1e-9
    assert out["notches"][0]["point"] == pytest.approx([4, 4])
    assert sum(p["area"] for p in out["parts"]) == pytest.approx(40 + 24)


def test_bad_shape_index_is_an_honest_error(tmp_path):
    with pytest.raises(ValueError, match="out of range"):
        run({"svg": "fixtures/ell.svg", "shape": 7}, make_test_ctx(tmp_path, workspace=FIX.parent))
