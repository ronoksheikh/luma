"""Scene loading, contact sheets, resumable multi-process frame rendering, and the full
render → audio → encode → QC pipeline.

Progress is reported as JSON lines on stdout (``{"type": "progress", ...}``) so the
Luma Studio job manager can show live progress bars.
"""
from __future__ import annotations

import importlib.util
import json
import math
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path
from typing import Sequence

import numpy as np

from .scene import Scene


def emit(kind: str, **data) -> None:
    print(json.dumps({"type": kind, **data}), flush=True)


# ======================================================================================
# loading
# ======================================================================================


def load_scene(path: str, **overrides) -> Scene:
    """Import a scene file and return its scene.

    The file may define ``scene`` (an instance), ``build(**overrides)`` or ``SCENE``.
    ``overrides`` (width/height/fps/duration) are applied when the file exposes
    ``build`` or when set on the instance before setup.
    """
    p = Path(path).resolve()
    spec = importlib.util.spec_from_file_location(f"luma_scene_{abs(hash(str(p)))}", p)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(p.parent))
    old_cwd = os.getcwd()
    try:
        spec.loader.exec_module(mod)
    finally:
        if sys.path and sys.path[0] == str(p.parent):
            sys.path.pop(0)
        os.chdir(old_cwd)
    if hasattr(mod, "build"):
        sc = mod.build(**overrides)
    else:
        sc = getattr(mod, "scene", None) or getattr(mod, "SCENE", None)
        if sc is None:
            cands = [v for v in vars(mod).values() if isinstance(v, Scene)]
            if not cands:
                raise ValueError(f"{path} defines no `scene` (Scene instance) or `build()`")
            sc = cands[0]
        for k, v in overrides.items():
            if v is not None:
                setattr(sc, k, v)
        sc.timeline.duration = sc.duration
        sc.timeline.fps = sc.fps
    return sc


# ======================================================================================
# contact sheets
# ======================================================================================


def _label(img: np.ndarray, text: str) -> np.ndarray:
    import cv2

    out = (np.clip(img, 0, 1) * 255).astype(np.uint8).copy()
    h = out.shape[0]
    fs = max(0.4, h / 540 * 0.6)
    th = max(1, int(round(fs * 1.6)))
    (tw, tht), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, fs, th)
    cv2.rectangle(out, (0, 0), (tw + 16, tht + 14), (0, 0, 0), -1)
    cv2.putText(out, text, (8, tht + 7), cv2.FONT_HERSHEY_SIMPLEX, fs, (255, 255, 255), th, cv2.LINE_AA)
    return out


def contact_sheet(scene: Scene, times: Sequence[float] | None = None, frames: Sequence[int] | None = None,
                  scale: float = 0.5, cols: int | None = None, out: str = "contact_sheet.png", blur: bool = True,
                  gap: int = 6) -> dict:
    """Render labelled keyframes into a grid PNG.  Returns {path, tiles:[{t, frame}]}."""
    import cv2

    scene.ensure_setup()
    if frames is not None:
        ts = [scene.time_of(int(f)) for f in frames]
    elif times is not None:
        ts = [float(t) for t in times]
    else:
        n = 8
        ts = [scene.time_of(int(round(i * (scene.frame_count - 1) / (n - 1)))) for i in range(n)]
    tiles = []
    meta = []
    t0 = time.time()
    for t in ts:
        t = min(max(t, 0.0), scene.duration)
        fi = min(int(round(t * scene.fps)), scene.frame_count - 1)
        img = scene.render(t, scale, blur)
        ev = [e.name for e in scene.timeline.events if abs(e.t - t) < 0.5 / scene.fps]
        label = f"t={t:.3f}s  f{fi}" + (f"  [{', '.join(ev)}]" if ev else "")
        tiles.append(_label(img, label))
        meta.append({"t": round(t, 4), "frame": fi})
    n = len(tiles)
    cols = cols or (1 if n == 1 else 2 if n <= 4 else 3 if n <= 9 else 4)
    rows = math.ceil(n / cols)
    th, tw = tiles[0].shape[:2]
    sheet = np.full((rows * th + (rows + 1) * gap, cols * tw + (cols + 1) * gap, 3), 24, np.uint8)
    for i, tile in enumerate(tiles):
        r, c = divmod(i, cols)
        y, x = gap + r * (th + gap), gap + c * (tw + gap)
        sheet[y : y + th, x : x + tw] = tile
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(out, cv2.cvtColor(sheet, cv2.COLOR_RGB2BGR))
    return {"path": out, "tiles": meta, "seconds": round(time.time() - t0, 2), "size": [sheet.shape[1], sheet.shape[0]]}


# ======================================================================================
# frame rendering (multi-process, resumable)
# ======================================================================================

_W_SCENE: Scene | None = None


def _worker_init(scene_path: str, overrides: dict):
    global _W_SCENE
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    try:
        import cv2

        cv2.setNumThreads(1)
    except Exception:
        pass
    _W_SCENE = load_scene(scene_path, **overrides)
    _W_SCENE.ensure_setup()


def _worker_render(args):
    i, out_dir, scale, blur = args
    from .encode import save_png16

    assert _W_SCENE is not None
    path = os.path.join(out_dir, f"frame_{i:06d}.png")
    img = _W_SCENE.render_frame(i, scale, blur)
    tmp = path + ".tmp.png"
    save_png16(tmp, img)
    os.replace(tmp, path)
    return i


def render_frames(scene_path: str, out_dir: str, start: int = 0, end: int | None = None, workers: int | None = None,
                  scale: float = 1.0, blur: bool = True, overrides: dict | None = None, quiet: bool = False) -> dict:
    """Render frames [start, end) to 16-bit PNGs, skipping frames that already exist."""
    overrides = overrides or {}
    sc = load_scene(scene_path, **overrides)
    n = sc.frame_count
    end = n if end is None else min(end, n)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    todo = [i for i in range(start, end) if not os.path.exists(os.path.join(out_dir, f"frame_{i:06d}.png"))]
    total = end - start
    done = total - len(todo)
    if not quiet:
        emit("progress", stage="render", frame=done, total=total, eta_s=None, message=f"{done} frames already rendered" if done else "starting")
    t0 = time.time()
    workers = workers or max(1, min(os.cpu_count() or 1, 8))
    if todo:
        ctx = mp.get_context("fork" if sys.platform.startswith("linux") else "spawn")
        with ctx.Pool(workers, initializer=_worker_init, initargs=(scene_path, overrides)) as pool:
            k = 0
            for _ in pool.imap_unordered(_worker_render, [(i, out_dir, scale, blur) for i in todo], chunksize=1):
                k += 1
                el = time.time() - t0
                rate = k / el if el > 0 else 0
                eta = (len(todo) - k) / rate if rate > 0 else None
                if not quiet:
                    emit("progress", stage="render", frame=done + k, total=total, eta_s=round(eta, 1) if eta else None,
                         fps=round(rate, 2))
    return {"frames_dir": out_dir, "frames": total, "rendered": len(todo), "seconds": round(time.time() - t0, 2)}


def render_audio(scene: Scene, out_wav: str, target_lufs: float = -14.0, true_peak: float = -1.0) -> dict | None:
    from . import audio as A

    scene.ensure_setup()
    x = scene.audio()
    if x is None:
        return None
    x = A.fit_length(x, scene.frame_count / scene.fps)
    x = A.master(x, target_lufs, true_peak)
    x = A.fit_length(x, scene.frame_count / scene.fps, fade_out=0.0)
    A.write_wav(out_wav, x)
    return {"path": out_wav, **A.analyze(x)}


def render_pipeline(scene_path: str, out_dir: str, name: str = "final", workers: int | None = None,
                    prores: bool = False, overrides: dict | None = None, qc: bool = True) -> dict:
    """frames → end card → audio → MP4 (+ProRes) → SRT → QC.  Returns a manifest."""
    from . import encode as E
    from .qc import qc_report

    overrides = overrides or {}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    frames_dir = str(out / "frames")
    res = render_frames(scene_path, frames_dir, workers=workers, overrides=overrides)
    sc = load_scene(scene_path, **overrides)
    sc.ensure_setup()
    emit("progress", stage="end_card", frame=0, total=1, message="rendering reference end card")
    ref = sc.reference_frame()
    ref_png = str(out / "end_card.png")
    import cv2

    cv2.imwrite(ref_png, cv2.cvtColor(E.to_uint8(ref, dither=False), cv2.COLOR_RGB2BGR))
    emit("progress", stage="audio", frame=0, total=1, message="synthesising sound design")
    audio = render_audio(sc, str(out / "mix.wav"))
    events_path = str(out / "events.json")
    sc.timeline.to_json(events_path)
    mp4 = str(out / f"{name}.mp4")

    def prog(i, n):
        if i % 10 == 0 or i == n:
            emit("progress", stage="encode", frame=i, total=n)

    E.encode_frames(frames_dir, mp4, sc.fps, audio=audio["path"] if audio else None, progress=prog)
    manifest = {"mp4": mp4, "end_card": ref_png, "events": events_path, "frames": res, "audio": audio}
    if prores:
        mov = str(out / f"{name}_prores.mov")
        E.encode_frames(frames_dir, mov, sc.fps, audio=audio["path"] if audio else None, codec="prores")
        manifest["prores"] = mov
    cues = sc.captions()
    if cues:
        manifest["srt"] = str(out / f"{name}.srt")
        E.write_srt(cues, manifest["srt"])
    if qc:
        emit("progress", stage="qc", frame=0, total=1, message="running QC")
        rep = qc_report(mp4, expected_frames=sc.frame_count, expected_fps=sc.fps, expected_duration=sc.frame_count / sc.fps,
                        first_black=sc.first_frame_black, reference=ref_png,
                        events=[{"name": e.name, "t": e.t, "kind": e.kind} for e in sc.timeline.events],
                        require_audio=audio is not None, out_json=str(out / "qc.json"))
        manifest["qc"] = {"pass": rep["pass"], "path": str(out / "qc.json"), "failed": [c["name"] for c in rep["checks"] if not c["pass"]]}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    emit("done", manifest=manifest)
    return manifest
