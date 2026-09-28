"""Command line: ``python -m luma_engine <command> ...``

  preview   SCENE --times 0,1.5,3 | --frames 0,90 | --count 8  [--scale 0.5] [--out sheet.png] [--no-blur]
  render    SCENE --out DIR [--workers N] [--start A --end B] [--scale S] [--no-blur]
  pipeline  SCENE --out DIR [--workers N] [--prores]      frames → audio → mp4 → QC
  reference SCENE --out end_card.png
  audio     SCENE --out mix.wav [--lufs -14] [--tp -1]
  encode    --frames DIR --out out.mp4 --fps 60 [--audio mix.wav] [--prores] [--srt captions.srt --burn]
  qc        VIDEO [--reference end.png] [--events events.json] [--frames N --fps F --duration D] [--first-black]
  analyze-audio FILE [--png out.png] [--events events.json]
  inspect   FILE.svg|png|pdf|wav
  describe  SCENE
  templates
  demo      --out DIR [--workers N] [--width 1920 --height 1080 --fps 60 --duration 5]

Scene overrides: --width --height --fps --duration apply to any SCENE command.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _floats(s):
    return [float(x) for x in s.split(",") if x.strip()] if s else None


def _ints(s):
    return [int(x) for x in s.split(",") if x.strip()] if s else None


def _overrides(a) -> dict:
    return {k: getattr(a, k) for k in ("width", "height", "fps", "duration") if getattr(a, k, None) is not None}


def _add_overrides(p):
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    p.add_argument("--fps", type=float)
    p.add_argument("--duration", type=float)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="luma_engine", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("preview")
    p.add_argument("scene")
    p.add_argument("--times")
    p.add_argument("--frames")
    p.add_argument("--count", type=int, default=8)
    p.add_argument("--scale", type=float, default=0.5)
    p.add_argument("--cols", type=int)
    p.add_argument("--out", default="work/previews/contact_sheet.png")
    p.add_argument("--no-blur", action="store_true")
    _add_overrides(p)

    p = sub.add_parser("render")
    p.add_argument("scene")
    p.add_argument("--out", required=True)
    p.add_argument("--workers", type=int)
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--end", type=int)
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--no-blur", action="store_true")
    _add_overrides(p)

    p = sub.add_parser("pipeline")
    p.add_argument("scene")
    p.add_argument("--out", required=True)
    p.add_argument("--name", default="final")
    p.add_argument("--workers", type=int)
    p.add_argument("--prores", action="store_true")
    _add_overrides(p)

    p = sub.add_parser("reference")
    p.add_argument("scene")
    p.add_argument("--out", required=True)
    _add_overrides(p)

    p = sub.add_parser("audio")
    p.add_argument("scene")
    p.add_argument("--out", required=True)
    p.add_argument("--lufs", type=float, default=-14.0)
    p.add_argument("--tp", type=float, default=-1.0)
    _add_overrides(p)

    p = sub.add_parser("encode")
    p.add_argument("--frames", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--fps", type=float, required=True)
    p.add_argument("--audio")
    p.add_argument("--prores", action="store_true")
    p.add_argument("--crf", type=int, default=12)
    p.add_argument("--srt")
    p.add_argument("--burn", action="store_true")

    p = sub.add_parser("qc")
    p.add_argument("video")
    p.add_argument("--reference")
    p.add_argument("--events")
    p.add_argument("--frames", type=int)
    p.add_argument("--fps", type=float)
    p.add_argument("--duration", type=float)
    p.add_argument("--first-black", action="store_true")
    p.add_argument("--lufs", type=float, default=-14.0)
    p.add_argument("--out")

    p = sub.add_parser("analyze-audio")
    p.add_argument("file")
    p.add_argument("--png")
    p.add_argument("--events")

    p = sub.add_parser("inspect")
    p.add_argument("file")

    p = sub.add_parser("describe")
    p.add_argument("scene")
    _add_overrides(p)

    sub.add_parser("templates")

    p = sub.add_parser("demo")
    p.add_argument("--out", required=True)
    p.add_argument("--workers", type=int)
    p.add_argument("--logo")
    p.add_argument("--wordmark", default="Veyra")
    _add_overrides(p)

    a = ap.parse_args(argv)
    out = run(a)
    if out is not None:
        print(json.dumps(out, indent=2, default=str))
    return 0


def run(a):
    from . import render as R

    if a.cmd == "preview":
        sc = R.load_scene(a.scene, **_overrides(a))
        sc.ensure_setup()
        frames = _ints(a.frames)
        times = _floats(a.times)
        if frames is None and times is None:
            n = max(1, a.count)
            frames = [int(round(i * (sc.frame_count - 1) / max(n - 1, 1))) for i in range(n)]
        return R.contact_sheet(sc, times=times, frames=frames, scale=a.scale, cols=a.cols, out=a.out, blur=not a.no_blur)
    if a.cmd == "render":
        return R.render_frames(a.scene, a.out, a.start, a.end, a.workers, a.scale, not a.no_blur, _overrides(a))
    if a.cmd == "pipeline":
        return R.render_pipeline(a.scene, a.out, a.name, a.workers, a.prores, _overrides(a))
    if a.cmd == "reference":
        import cv2

        from .encode import to_uint8

        sc = R.load_scene(a.scene, **_overrides(a))
        img = sc.reference_frame()
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(a.out, cv2.cvtColor(to_uint8(img, dither=False), cv2.COLOR_RGB2BGR))
        return {"path": a.out, "size": [img.shape[1], img.shape[0]]}
    if a.cmd == "audio":
        from . import audio as A

        sc = R.load_scene(a.scene, **_overrides(a))
        sc.ensure_setup()
        x = sc.audio()
        if x is None:
            return {"error": "scene has no audio()"}
        dur = sc.frame_count / sc.fps
        x = A.fit_length(A.master(A.fit_length(x, dur), a.lufs, a.tp), dur, fade_out=0.0)
        A.write_wav(a.out, x)
        events = [{"name": e.name, "t": e.t} for e in sc.timeline.events]
        png = os.path.splitext(a.out)[0] + "_spectrogram.png"
        A.spectrogram_png(x, png, events=events, title=os.path.basename(a.out))
        return {"path": a.out, "spectrogram": png, **A.analyze(x)}
    if a.cmd == "encode":
        from . import encode as E

        out = E.encode_frames(a.frames, a.out, a.fps, audio=a.audio, codec="prores" if a.prores else "h264", crf=a.crf)
        if a.srt and a.burn and not a.prores:
            burned = os.path.splitext(a.out)[0] + "_captions.mp4"
            E.burn_subtitles(out, a.srt, burned, a.crf)
            return {"path": out, "captioned": burned, "info": E.video_info(burned)}
        return {"path": out, "info": E.video_info(out)}
    if a.cmd == "qc":
        from .qc import qc_report

        events = None
        if a.events:
            d = json.loads(Path(a.events).read_text())
            events = d.get("events", d) if isinstance(d, dict) else d
        return qc_report(a.video, expected_frames=a.frames, expected_fps=a.fps, expected_duration=a.duration,
                         first_black=a.first_black, reference=a.reference, events=events, target_lufs=a.lufs, out_json=a.out)
    if a.cmd == "analyze-audio":
        from . import audio as A

        x = A.read_audio(a.file)
        events = None
        if a.events:
            d = json.loads(Path(a.events).read_text())
            events = d.get("events", d) if isinstance(d, dict) else d
        res = A.analyze(x)
        if a.png:
            A.spectrogram_png(x, a.png, events=events, title=os.path.basename(a.file))
            res["png"] = a.png
        return res
    if a.cmd == "inspect":
        from .analysis import analyze_file

        return analyze_file(a.file)
    if a.cmd == "describe":
        return R.load_scene(a.scene, **_overrides(a)).describe()
    if a.cmd == "templates":
        from .templates import TEMPLATES

        return {k: v for k, v in TEMPLATES.items()}
    if a.cmd == "demo":
        return demo(a.out, a.workers, a.logo, a.wordmark, _overrides(a))
    raise SystemExit(f"unknown command {a.cmd}")


def demo(out_dir: str, workers: int | None = None, logo: str | None = None, wordmark: str = "Veyra", overrides: dict | None = None) -> dict:
    """Render the bundled fan_unfold demo on the sample logo — no LLM, no API keys."""
    from . import render as R

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    logo = logo or str(sample_path("veyra-symbol.svg"))
    ov = {"width": 1920, "height": 1080, "fps": 60, "duration": 5.0}
    ov.update({k: v for k, v in (overrides or {}).items() if v is not None})
    scene_file = out / "demo_scene.py"
    scene_file.write_text(
        "from luma_engine.templates.fan_unfold import FanUnfold\n\n"
        f"scene = FanUnfold(logo={logo!r}, wordmark={wordmark!r}, background='#0B0F2A',\n"
        f"                  width={ov['width']}, height={ov['height']}, fps={ov['fps']}, duration={ov['duration']})\n"
    )
    return R.render_pipeline(str(scene_file), str(out), "luma_demo", workers)


def sample_path(name: str) -> Path:
    env = os.environ.get("LUMA_SAMPLES_DIR")
    cands = [Path(env)] if env else []
    cands += [Path(__file__).resolve().parents[2] / "samples", Path("/opt/luma/samples")]
    for c in cands:
        if (c / name).exists():
            return c / name
    raise FileNotFoundError(f"sample {name} not found (set LUMA_SAMPLES_DIR)")


if __name__ == "__main__":
    sys.exit(main())
