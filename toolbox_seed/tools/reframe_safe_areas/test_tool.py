from pathlib import Path

from main import run

from luma_engine.toolkit import make_test_ctx

FIX = Path(__file__).parent / "fixtures"


def test_square_and_portrait_keep_the_mark_inside_the_safe_area(tmp_path):
    scene = tmp_path / "scene.py"
    scene.write_text("from luma_engine.templates.fan_unfold import FanUnfold\n"
                     f"scene = FanUnfold(logo={str(FIX / 'mark.svg')!r}, wordmark=None, width=640, height=360, fps=12, duration=2, sound=False)\n")
    ctx = make_test_ctx(tmp_path, workspace=tmp_path)
    out = run({"scene": "scene.py", "aspects": ["9:16", "1:1"], "long_side": 320}, ctx)
    assert [a["aspect"] for a in out["aspects"]] == ["9:16", "1:1"]
    assert out["pass"], out
    w, h = out["aspects"][0]["size"]
    assert h > w
    assert ctx.images and Path(out["contact"]).exists()
