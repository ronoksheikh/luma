"""Encoding: dithering, FFmpeg pipes, muxing, SRT captions and ffprobe verification.

8-bit delivery uses **static TPDF dither** (the same noise texture every frame, so still
frames stay still) and skips pixels that are already exact 8-bit values — pure black
stays exactly 0, pure white 255, and flat brand colours stay exact.
"""
from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

FFMPEG = os.environ.get("LUMA_FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("LUMA_FFPROBE", "ffprobe")
BT709 = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]


@functools.lru_cache(maxsize=8)
def _tpdf(h: int, w: int, seed: int = 1234) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.random((h, w, 1), dtype=np.float32) - rng.random((h, w, 1), dtype=np.float32)).astype(np.float32)


def to_uint8(frame: np.ndarray, dither: bool = True, exact_tol: float = 0.06) -> np.ndarray:
    """float sRGB [0, 1] → uint8 with static TPDF dither (±1 LSB triangular).

    Values within ``exact_tol`` LSB of an integer are rounded without dither, so black,
    white and flat 8-bit brand colours are reproduced exactly.
    """
    v = np.clip(frame, 0.0, 1.0).astype(np.float32) * 255.0
    r = np.rint(v)
    if not dither:
        return r.astype(np.uint8)
    h, w = v.shape[:2]
    n = _tpdf(h, w)
    d = np.floor(v + 0.5 + n)
    exact = np.abs(v - r) <= exact_tol
    out = np.where(exact, r, d)
    return np.clip(out, 0, 255).astype(np.uint8)


def to_uint16(frame: np.ndarray) -> np.ndarray:
    return np.rint(np.clip(frame, 0.0, 1.0) * 65535.0).astype(np.uint16)


def save_png16(path: str, frame: np.ndarray, compression: int = 1) -> None:
    import cv2

    cv2.imwrite(path, cv2.cvtColor(to_uint16(frame), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_PNG_COMPRESSION, compression])


def load_png(path: str) -> np.ndarray:
    """PNG (8 or 16 bit) → float32 RGB [0, 1]."""
    import cv2

    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise FileNotFoundError(path)
    if img.ndim == 2:
        img = np.stack([img] * 3, axis=-1)
    if img.shape[2] == 4:
        img = img[:, :, :3]
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    scale = 65535.0 if img.dtype == np.uint16 else 255.0
    return img.astype(np.float32) / scale


def h264_args(crf: int = 12, preset: str = "slow") -> list[str]:
    return [
        "-c:v", "libx264", "-profile:v", "high", "-preset", preset, "-crf", str(crf),
        "-vf", "scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int+bitexact",
        "-pix_fmt", "yuv420p", *BT709, "-color_range", "tv", "-movflags", "+faststart",
    ]


def prores_args() -> list[str]:
    return [
        "-c:v", "prores_ks", "-profile:v", "3", "-vendor", "apl0", "-bits_per_mb", "8000",
        "-vf", "scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int",
        "-pix_fmt", "yuv422p10le", *BT709, "-color_range", "tv",
    ]


class VideoWriter:
    """Pipe float frames into FFmpeg.  ``codec``: "h264" (8-bit dithered) or "prores"
    (16-bit input → ProRes 422 HQ 10-bit)."""

    def __init__(self, path: str, width: int, height: int, fps: float, codec: str = "h264", crf: int = 12,
                 preset: str = "slow", audio: str | None = None):
        self.path, self.w, self.h, self.fps, self.codec = path, width, height, fps, codec
        pix = "rgb48le" if codec == "prores" else "rgb24"
        cmd = [FFMPEG, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", pix, "-s", f"{width}x{height}",
               "-framerate", _fps_str(fps), "-i", "-"]
        if audio:
            cmd += ["-i", audio, "-map", "0:v:0", "-map", "1:a:0"]
        cmd += prores_args() if codec == "prores" else h264_args(crf, preset)
        if audio:
            cmd += ["-c:a", "pcm_s24le"] if codec == "prores" else ["-c:a", "aac", "-b:a", "320k"]
        cmd += [path]
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        self.count = 0

    def write(self, frame: np.ndarray) -> None:
        if frame.dtype == np.uint8 or frame.dtype == np.uint16:
            data = frame
        elif self.codec == "prores":
            data = to_uint16(frame)
        else:
            data = to_uint8(frame)
        if self.codec == "prores" and data.dtype == np.uint8:
            data = data.astype(np.uint16) * 257
        assert data.shape[:2] == (self.h, self.w), f"frame {data.shape} != {self.w}x{self.h}"
        self.proc.stdin.write(np.ascontiguousarray(data[..., :3]).tobytes())
        self.count += 1

    def close(self) -> str:
        self.proc.stdin.close()
        err = self.proc.stderr.read().decode(errors="replace")
        rc = self.proc.wait()
        if rc != 0:
            raise RuntimeError(f"ffmpeg failed ({rc}): {err[-2000:]}")
        return self.path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if exc[0] is None:
            self.close()
        else:
            self.proc.kill()


def _fps_str(fps: float) -> str:
    if abs(fps - round(fps)) < 1e-9:
        return str(int(round(fps)))
    for num, den in ((24000, 1001), (30000, 1001), (60000, 1001)):
        if abs(fps - num / den) < 1e-6:
            return f"{num}/{den}"
    return f"{fps:.6f}"


def frame_files(frames_dir: str) -> list[str]:
    d = Path(frames_dir)
    return sorted(str(p) for p in d.glob("*.png"))


def encode_frames(frames: str | Sequence[str], out: str, fps: float, audio: str | None = None, codec: str = "h264",
                  crf: int = 12, preset: str = "slow", progress=None) -> str:
    """Encode a directory (or list) of PNG frames (16-bit preferred) to MP4/MOV."""
    files = frame_files(frames) if isinstance(frames, str) else list(frames)
    if not files:
        raise FileNotFoundError(f"no frames in {frames}")
    first = load_png(files[0])
    h, w = first.shape[:2]
    with VideoWriter(out, w, h, fps, codec, crf, preset, audio) as vw:
        for i, f in enumerate(files):
            vw.write(first if i == 0 else load_png(f))
            if progress:
                progress(i + 1, len(files))
    return out


def mux(video: str, audio: str, out: str, codec: str | None = None) -> str:
    """Replace/attach audio without re-encoding video (AAC 320k for MP4, PCM for MOV)."""
    is_mov = out.lower().endswith(".mov")
    acodec = codec or ("pcm_s24le" if is_mov else "aac")
    cmd = [FFMPEG, "-y", "-v", "error", "-i", video, "-i", audio, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", acodec]
    if acodec == "aac":
        cmd += ["-b:a", "320k"]
    if not is_mov:
        cmd += ["-movflags", "+faststart"]
    cmd.append(out)
    _run(cmd)
    return out


def burn_subtitles(video: str, srt: str, out: str, crf: int = 12, style: str | None = None) -> str:
    """Burn an SRT into the picture (libass) and re-encode H.264; audio is copied."""
    style = style or "FontName=Inter,FontSize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,BorderStyle=1,Outline=1.2,Shadow=0,MarginV=48"
    esc = srt.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    cmd = [FFMPEG, "-y", "-v", "error", "-i", video, "-vf", f"subtitles='{esc}':force_style='{style}'",
           "-c:v", "libx264", "-profile:v", "high", "-preset", "slow", "-crf", str(crf), "-pix_fmt", "yuv420p",
           *BT709, "-color_range", "tv", "-movflags", "+faststart", "-c:a", "copy", out]
    _run(cmd)
    return out


def _run(cmd: list[str]) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {p.stderr[-2000:]}")
    return p.stdout


# ======================================================================================
# captions
# ======================================================================================


@dataclass
class Cue:
    start: float
    end: float
    text: str


def srt_time(t: float) -> str:
    ms = int(round(max(t, 0.0) * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(cues: Iterable[Cue | tuple | dict], path: str | None = None) -> str:
    lines = []
    for i, c in enumerate(cues, 1):
        if isinstance(c, dict):
            c = Cue(c["start"], c["end"], c["text"])
        elif isinstance(c, tuple):
            c = Cue(*c)
        lines += [str(i), f"{srt_time(c.start)} --> {srt_time(c.end)}", c.text.strip(), ""]
    s = "\n".join(lines)
    if path:
        Path(path).write_text(s, encoding="utf-8")
    return s


def parse_srt(text: str) -> list[Cue]:
    import re

    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        ls = [x for x in block.strip().splitlines() if x.strip()]
        if len(ls) < 2:
            continue
        m = re.match(r"(\d+):(\d+):(\d+),(\d+)\s*-->\s*(\d+):(\d+):(\d+),(\d+)", ls[1] if "-->" in ls[1] else ls[0])
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        st = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        en = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        body = ls[2:] if "-->" in ls[1] else ls[1:]
        cues.append(Cue(st, en, "\n".join(body)))
    return cues


def words_to_cues(words: Sequence[dict], max_chars: int = 42, max_lines: int = 2, max_dur: float = 6.0,
                  min_dur: float = 0.8, gap: float = 0.6) -> list[Cue]:
    """Group word timings (``{text, start, end}``) into readable subtitle cues:
    ≤ max_chars per line, ≤ max_lines, break on sentence ends and pauses."""
    cues: list[Cue] = []
    cur: list[dict] = []

    def text_of(ws):
        # greedy wrap
        lines, line = [], ""
        for w in ws:
            cand = (line + " " + w["text"]).strip()
            if len(cand) > max_chars and line:
                lines.append(line)
                line = w["text"]
            else:
                line = cand
        if line:
            lines.append(line)
        return lines

    def flush():
        if cur:
            cues.append(Cue(cur[0]["start"], cur[-1]["end"], "\n".join(text_of(cur))))
            cur.clear()

    for w in words:
        if cur:
            pause = w["start"] - cur[-1]["end"]
            sentence = cur[-1]["text"].rstrip().endswith((".", "!", "?", "…"))
            too_long = len(text_of(cur + [w])) > max_lines or (w["end"] - cur[0]["start"]) > max_dur
            if pause > gap or sentence or too_long:
                flush()
        cur.append(w)
    flush()
    for i, c in enumerate(cues):  # minimum duration without overlapping the next cue
        nxt = cues[i + 1].start if i + 1 < len(cues) else c.end + min_dur
        if c.end - c.start < min_dur:
            c.end = min(c.start + min_dur, nxt - 0.001)
    return cues


# ======================================================================================
# probing
# ======================================================================================


def probe(path: str, count_frames: bool = True) -> dict:
    cmd = [FFPROBE, "-v", "error", "-show_format", "-show_streams", "-of", "json"]
    if count_frames:
        cmd.insert(3, "-count_frames")
    return json.loads(_run(cmd + [path]))


def video_info(path: str) -> dict:
    """Frame count, fps, duration, size, codecs, colour tags, audio presence."""
    info = probe(path)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    out = {"path": path, "format": info["format"].get("format_name"), "size_bytes": int(info["format"].get("size", 0))}
    if v:
        num, den = (int(x) for x in v.get("r_frame_rate", "0/1").split("/"))
        fps = num / den if den else 0.0
        frames = int(v.get("nb_read_frames") or v.get("nb_frames") or 0)
        out.update({
            "width": v["width"], "height": v["height"], "fps": fps, "frames": frames,
            "duration": frames / fps if fps else float(v.get("duration", 0)),
            "vcodec": v.get("codec_name"), "profile": v.get("profile"), "pix_fmt": v.get("pix_fmt"),
            "color_space": v.get("color_space"), "color_primaries": v.get("color_primaries"),
            "color_transfer": v.get("color_transfer"), "color_range": v.get("color_range"),
        })
    if a:
        out.update({"acodec": a.get("codec_name"), "sample_rate": int(a.get("sample_rate", 0)), "channels": a.get("channels"),
                    "audio_bitrate": int(a.get("bit_rate", 0) or 0)})
    out["has_audio"] = a is not None
    return out


def decode_audio(path: str, sr: int = 48000) -> np.ndarray:
    """Decode the first audio stream to float stereo (honours MP4 edit lists)."""
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", path, "-map", "0:a:0", "-f", "f32le", "-ac", "2", "-ar", str(sr), "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).astype(np.float64)


def decode_frames(path: str, width: int | None = None, height: int | None = None, every: int = 1) -> Iterable[np.ndarray]:
    """Yield decoded RGB uint8 frames (optionally scaled) — used by QC."""
    info = video_info(path)
    w = width or info["width"]
    h = height or info["height"]
    vf = [f"scale={w}:{h}:in_color_matrix=bt709:in_range=tv:flags=accurate_rnd+full_chroma_int"]
    if every > 1:
        vf.append(f"select=not(mod(n\\,{every}))")
    cmd = [FFMPEG, "-v", "error", "-i", path, "-vf", ",".join(vf), "-vsync", "0", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = w * h * 3
    try:
        while True:
            buf = p.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    finally:
        p.stdout.close()
        p.wait()


def have_ffmpeg() -> bool:
    return shutil.which(FFMPEG) is not None


__all__ = [
    "Cue", "VideoWriter", "burn_subtitles", "decode_audio", "decode_frames", "encode_frames", "frame_files", "h264_args",
    "have_ffmpeg", "load_png", "mux", "parse_srt", "probe", "prores_args", "save_png16", "srt_time", "to_uint16", "to_uint8",
    "video_info", "words_to_cues", "write_srt",
]
