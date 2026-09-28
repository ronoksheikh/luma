import json

import numpy as np
import pytest
from main import run

from luma_engine import audio as A
from luma_engine.toolkit import make_test_ctx


def _mix(tmp_path, times):
    x = np.zeros(int(A.SR * 2.0))
    for t in times:
        c = A.click()
        i = int(t * A.SR)
        x[i : i + len(c)] += c[: len(x) - i]
    A.write_wav(str(tmp_path / "mix.wav"), np.stack([x, x], axis=1))


def test_events_in_sync_pass(tmp_path):
    _mix(tmp_path, [0.3, 0.9, 1.5])
    (tmp_path / "events.json").write_text(json.dumps({"events": [{"t": 0.3, "name": "seat"}, {"t": 0.9, "name": "catch"}, {"t": 1.5, "name": "hit"}]}))
    ctx = make_test_ctx(tmp_path, workspace=tmp_path)
    out = run({"audio": "mix.wav", "events": "events.json", "fps": 60}, ctx)
    assert out["pass"], out["events"]
    assert all(abs(e["offset_ms"]) <= 33.4 for e in out["events"])
    assert ctx.images and out["tolerance_ms"] == pytest.approx(33.33, abs=0.01)


def test_a_late_sound_fails(tmp_path):
    _mix(tmp_path, [0.3, 1.0])
    out = run({"audio": "mix.wav", "events": [{"t": 0.3, "name": "a"}, {"t": 0.8, "name": "b"}], "fps": 60}, make_test_ctx(tmp_path, workspace=tmp_path))
    assert not out["pass"]
    bad = [e for e in out["events"] if not e["pass"]]
    assert bad[0]["name"] == "b" and bad[0]["offset_ms"] > 100


def test_events_without_times_are_rejected(tmp_path):
    _mix(tmp_path, [0.3])
    with pytest.raises(ValueError, match="no events"):
        run({"audio": "mix.wav", "events": [{"name": "x"}]}, make_test_ctx(tmp_path, workspace=tmp_path))
