"""Audio mixing/mastering, SRT timing, encoding (dither, ffprobe) and QC."""
import json
import shutil

import numpy as np
import pytest

from luma_engine import audio as A
from luma_engine.encode import (
    Cue, VideoWriter, decode_audio, mux, parse_srt, srt_time, to_uint8, video_info, words_to_cues, write_srt,
)
from luma_engine.qc import detect_spikes, qc_report

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


# ---------------------------------------------------------------- audio -------------
def test_equal_power_pan():
    x = np.ones(100)
    for p in (-1, -0.3, 0, 0.5, 1):
        st = A.pan_stereo(x, p)
        assert (st[:, 0] ** 2 + st[:, 1] ** 2) == pytest.approx(np.ones(100))
    assert A.pan_stereo(x, -1)[0].tolist() == pytest.approx([1, 0], abs=1e-12)


def test_mix_places_events_sample_accurately():
    mix = A.Mix(1.0)
    imp = np.zeros(100)
    imp[10] = 1.0
    mix.reverb = None
    mix.place(imp, 0.5, 0.0, anchor=10 / A.SR)
    out = mix.render()
    assert int(np.argmax(np.abs(out[:, 0]))) == A.samples(0.5)


def test_master_hits_lufs_and_true_peak():
    t = np.arange(A.SR * 4) / A.SR
    x = 0.9 * np.sin(2 * np.pi * 997 * t) + 0.3 * A.noise(4, "pink", 1)
    x[A.SR : A.SR + 50] += 3.0  # a nasty transient
    y = A.master(x, -14.0, -1.0)
    assert A.lufs(y) == pytest.approx(-14.0, abs=0.5)
    assert A.true_peak_db(y) <= -1.0 + 0.05


def test_true_peak_exceeds_sample_peak_for_intersample_overs():
    n = np.arange(4800)
    x = np.sin(2 * np.pi * n / 4 + np.pi / 4)  # fs/4 sine sampled at 45°: samples at 0.707, true peak 1.0
    assert A.gain_to_db(np.max(np.abs(x))) == pytest.approx(-3.01, abs=0.05)
    assert A.true_peak_db(x) == pytest.approx(0.0, abs=0.2)


def test_sidechain_ducking_reduces_music_under_voice():
    music = A.to_stereo(np.sin(2 * np.pi * 220 * np.arange(A.SR * 3) / A.SR) * 0.3)
    voice = np.zeros((A.SR * 3, 2))
    voice[A.SR : 2 * A.SR] = A.to_stereo(np.sin(2 * np.pi * 300 * np.arange(A.SR) / A.SR) * 0.5)
    d = A.sidechain_duck(music, voice, depth_db=-12)
    rms = lambda s: np.sqrt(np.mean(s**2))  # noqa: E731
    assert rms(d[int(1.5 * A.SR) : int(1.9 * A.SR)]) < rms(music[:A.SR]) * 0.35
    assert rms(d[: int(0.9 * A.SR)]) == pytest.approx(rms(music[: int(0.9 * A.SR)]), rel=0.02)


def test_onsets_find_placed_hits():
    mix = A.Mix(3.0)
    mix.reverb = None
    for t in (0.5, 1.25, 2.2):
        mix.place(A.clack(), t, -6)
    ons = A.onsets(mix.render())
    for t in (0.5, 1.25, 2.2):
        assert min(abs(o - t) for o in ons) < 0.012


def test_fit_length_and_wav_24bit(tmp_path):
    x = A.fit_length(np.ones(1000) * 0.1, 0.5)
    assert len(x) == A.samples(0.5)
    p = str(tmp_path / "a.wav")
    A.write_wav(p, x)
    import soundfile as sf

    info = sf.info(p)
    assert info.subtype == "PCM_24" and info.samplerate == 48000 and info.frames == 24000


def test_spectrogram_png(tmp_path):
    p = A.spectrogram_png(A.glass_ping(), str(tmp_path / "s.png"), events=[{"t": 0.1, "name": "hit"}])
    import cv2

    assert cv2.imread(p).shape[2] == 3


# ---------------------------------------------------------------- captions ----------
def test_srt_timing_and_roundtrip():
    assert srt_time(3723.4567) == "01:02:03,457"
    words = [{"text": w, "start": i * 0.4, "end": i * 0.4 + 0.3} for i, w in enumerate("Hello world. This is Luma Studio speaking now".split())]
    cues = words_to_cues(words)
    assert cues[0].text == "Hello world." and cues[0].start == 0.0
    assert cues[1].start == pytest.approx(0.8)
    for a, b in zip(cues, cues[1:]):
        assert a.end <= b.start
    text = write_srt(cues)
    back = parse_srt(text)
    assert [c.text for c in back] == [c.text for c in cues]
    assert back[1].start == pytest.approx(cues[1].start, abs=1e-3)
    assert "-->" in write_srt([Cue(0, 1, "x")])


# ---------------------------------------------------------------- dither ------------
def test_dither_keeps_exact_values_and_black():
    f = np.zeros((8, 8, 3), np.float32)
    f[:4] = 1.0
    f[:, :2] = 90 / 255
    u = to_uint8(f)
    assert u[4:, 2:].max() == 0 and u[:4, 2:].min() == 255 and np.all(u[:, :2] == 90)
    ramp = np.tile(np.linspace(0.2, 0.21, 64, dtype=np.float32)[None, :, None], (64, 1, 3))
    d = to_uint8(ramp).astype(float)
    assert abs(d.mean() - ramp.mean() * 255) < 0.1  # TPDF: unbiased
    assert np.array_equal(to_uint8(ramp), to_uint8(ramp))  # static pattern → still frames stay still


# ---------------------------------------------------------------- encode + QC -------
def _make_video(tmp_path, n=60, fps=30, spike=None, audio=True, flash_at=None):
    W, H = 160, 90
    path = str(tmp_path / "v.mp4")
    wav = None
    if audio:
        mix = A.Mix(n / fps)
        mix.reverb = None
        mix.place(A.clack(), 1.0, -3)
        mix.place(A.noise(n / fps, "pink", 3), 0.0, -24, bus="music")  # a quiet bed, like real mixes
        x = A.fit_length(A.master(mix.render(), -14, -1), n / fps, fade_out=0)
        wav = str(tmp_path / "a.wav")
        A.write_wav(wav, x)
    with VideoWriter(path, W, H, fps, audio=wav, preset="veryfast") as vw:
        for i in range(n):
            f = np.zeros((H, W, 3), np.float32)
            if i > 0:
                f[:] = min(i, 30) / 30 * 0.5
            if spike is not None and i == spike:
                f[:] = 1.0
            vw.write(f)
    return path


@needs_ffmpeg
def test_encode_probe_and_qc_pass(tmp_path):
    path = _make_video(tmp_path)
    info = video_info(path)
    assert info["frames"] == 60 and info["fps"] == 30 and info["color_primaries"] == "bt709" and info["profile"] == "High"
    ref = str(tmp_path / "ref.png")
    import cv2

    cv2.imwrite(ref, np.full((90, 160, 3), 128, np.uint8))
    rep = qc_report(path, expected_frames=60, expected_fps=30, expected_duration=2.0, first_black=True, reference=ref,
                    events=[{"name": "hit", "t": 1.0, "kind": "impact"}], out_json=str(tmp_path / "qc.json"))
    names = {c["name"]: c for c in rep["checks"]}
    assert rep["pass"], json.dumps(rep["checks"], indent=1)
    assert names["av_sync_events"]["pass"] and names["audio_length_matches_video"]["pass"]
    assert abs(len(decode_audio(path)) / 48000 - 2.0) < 1 / 30


@needs_ffmpeg
def test_qc_detects_failures(tmp_path):
    path = _make_video(tmp_path, spike=40)
    rep = qc_report(path, expected_frames=61, expected_fps=30, target_lufs=-23, events=[{"name": "hit", "t": 1.5, "kind": "impact"}])
    failed = {c["name"] for c in rep["checks"] if not c["pass"]}
    assert {"frame_count", "audio_loudness", "av_sync_events", "flicker_discontinuity"} <= failed
    assert not rep["pass"]


def test_spike_detector_respects_events():
    d = [0.5] * 50
    d[20] = 30.0
    assert detect_spikes(d, 30)[0]["explained"] is False
    assert detect_spikes(d, 30, events=[{"t": 21 / 30}])[0]["explained"] is True


@needs_ffmpeg
def test_mux_replaces_audio(tmp_path):
    path = _make_video(tmp_path, audio=False)
    wav = str(tmp_path / "b.wav")
    A.write_wav(wav, np.zeros((A.samples(2.0), 2)))
    out = mux(path, wav, str(tmp_path / "m.mp4"))
    info = video_info(out)
    assert info["has_audio"] and info["acodec"] == "aac"
