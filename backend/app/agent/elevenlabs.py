"""ElevenLabs via the official ``elevenlabs`` SDK (AsyncElevenLabs).

API surface verified against the installed SDK (generated from the official OpenAPI
spec): text_to_speech.convert_with_timestamps (previous_request_ids / previous_text /
next_text for stitching), voices.search (v2) + voices.get_shared, models.list,
text_to_voice.design / .create, text_to_sound_effects.convert (0.5–30 s,
prompt_influence 0–1), music.compose (music_length_ms 3000–600000),
speech_to_text.convert (timestamps_granularity="word"), user.get,
user.subscription.get.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import httpx
from elevenlabs.client import AsyncElevenLabs
from elevenlabs.core.api_error import ApiError

from ..config import config
from ..secrets_store import redact

DEFAULT_TTS_MODEL = "eleven_multilingual_v2"
DEFAULT_STT_MODEL = "scribe_v2"
MAX_CHUNK_CHARS = 2400


def client(api_key: str, timeout: float = 240) -> AsyncElevenLabs:
    return AsyncElevenLabs(api_key=api_key, base_url=config.elevenlabs_base_url.rstrip("/"), timeout=timeout)


def err_text(e: Exception) -> str:
    if isinstance(e, ApiError):
        body = e.body
        if isinstance(body, dict):
            det = body.get("detail", body)
            if isinstance(det, dict):
                body = det.get("message") or det.get("status") or det
            else:
                body = det
        return redact(f"HTTP {e.status_code}: {json.dumps(body)[:400] if not isinstance(body, str) else body[:400]}")
    return redact(f"{type(e).__name__}: {e}")


def status_of(e: Exception) -> int | None:
    return e.status_code if isinstance(e, ApiError) else None


# ======================================================================================
# account probe
# ======================================================================================


async def _probe(coro) -> dict:
    """Call an endpoint with deliberately invalid input: a validation error (400/422)
    proves access without generating (or paying for) anything."""
    try:
        res = coro
        if inspect.isawaitable(res):
            res = await res
        if hasattr(res, "__aiter__"):
            async for _ in res:
                break
        return {"available": True, "detail": "request accepted"}
    except ApiError as e:
        sc = e.status_code
        if sc in (400, 422):
            return {"available": True, "detail": "reachable (validation probe)"}
        if sc in (401, 403):
            return {"available": False, "detail": f"not permitted for this key/plan ({err_text(e)})"}
        if sc == 402:
            return {"available": False, "detail": f"requires a paid plan / quota ({err_text(e)})"}
        if sc == 404:
            return {"available": False, "detail": "endpoint not found"}
        return {"available": None, "detail": err_text(e)}
    except Exception as e:  # noqa: BLE001
        return {"available": None, "detail": err_text(e)}


async def probe_account(api_key: str) -> dict:
    c = client(api_key, timeout=30)
    out: dict = {"ok": False, "checks": []}
    try:
        user = await c.user.get()
    except Exception as e:  # noqa: BLE001
        out["checks"].append({"name": "auth", "status": "fail", "detail": err_text(e)})
        return out
    out["ok"] = True
    info = {"user_id": getattr(user, "user_id", None), "first_name": getattr(user, "first_name", None)}
    try:
        sub = await c.user.subscription.get()
        info.update({"tier": sub.tier, "status": str(getattr(sub, "status", "")), "character_count": sub.character_count,
                     "character_limit": sub.character_limit, "characters_remaining": max(0, sub.character_limit - sub.character_count),
                     "next_reset_unix": getattr(sub, "next_character_count_reset_unix", None),
                     "can_use_instant_voice_cloning": getattr(sub, "can_use_instant_voice_cloning", None)})
    except Exception as e:  # noqa: BLE001
        info["subscription_error"] = err_text(e)
    out["account"] = info
    out["checks"].append({"name": "auth", "status": "ok", "detail": f"tier {info.get('tier', '?')}"})
    caps = {}
    try:
        models = await c.models.list()
        tts_models = [m.model_id for m in models if getattr(m, "can_do_text_to_speech", False)]
        caps["tts"] = bool(tts_models)
        out["tts_models"] = tts_models
        out["checks"].append({"name": "tts", "status": "ok" if tts_models else "fail", "detail": ", ".join(tts_models[:8]) or "no TTS models"})
    except Exception as e:  # noqa: BLE001
        caps["tts"] = False
        out["checks"].append({"name": "tts", "status": "fail", "detail": err_text(e)})
    probes = {
        "voice_design": c.text_to_voice.design(voice_description="x"),
        "sfx": c.text_to_sound_effects.convert(text="", duration_seconds=0.1),
        "music": c.music.compose(prompt="", music_length_ms=1),
        "speech_to_text": c.speech_to_text.convert(model_id="__probe__"),
    }
    results = await asyncio.gather(*[_probe(p) for p in probes.values()])
    for name, r in zip(probes, results):
        caps[name] = r["available"]
        out["checks"].append({"name": name, "status": "ok" if r["available"] else ("warn" if r["available"] is None else "fail"), "detail": r["detail"]})
    out["capabilities"] = caps
    return out


# ======================================================================================
# cache + files
# ======================================================================================


def cache_key(kind: str, params: dict) -> str:
    return hashlib.sha256(json.dumps({"kind": kind, **params}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


class ElCache:
    """Content-hash cache in ``audio/.cache`` — identical requests are never paid twice."""

    def __init__(self, pdir: Path):
        self.dir = pdir / "audio" / ".cache"
        self.dir.mkdir(parents=True, exist_ok=True)

    def get(self, key: str) -> dict | None:
        p = self.dir / f"{key}.json"
        if not p.exists():
            return None
        meta = json.loads(p.read_text())
        if all(Path(f).exists() for f in meta.get("files", [])):
            return meta
        return None

    def put(self, key: str, meta: dict) -> None:
        (self.dir / f"{key}.json").write_text(json.dumps(meta, indent=1))


def write_sidecar(path: Path, meta: dict) -> Path:
    side = path.with_name(path.stem + ".meta.json") if path.suffix == ".json" else path.with_suffix(path.suffix + ".json")
    side.write_text(json.dumps(meta, indent=1, ensure_ascii=False))
    return side


def to_wav(src: Path, dst: Path, sr: int = 48000) -> float:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-ar", str(sr), "-ac", "2", "-c:a", "pcm_s24le", str(dst)],
                   check=True, capture_output=True)
    import soundfile as sf

    return sf.info(str(dst)).duration


def charge_from_headers(headers: dict, fallback: int) -> int:
    h = {k.lower(): v for k, v in (headers or {}).items()}
    for k in ("character-cost", "x-character-count", "x-characters-used"):
        v = h.get(k)
        if v and str(v).strip().isdigit():
            return int(v)
    return int(fallback)


# ======================================================================================
# TTS chunking + word timings
# ======================================================================================


def chunk_text(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    text = text.strip()
    if len(text) <= max_chars:
        return [text]
    sentences = re.split(r"(?<=[.!?…])\s+", text)
    chunks, cur = [], ""
    for s in sentences:
        while len(s) > max_chars:  # very long sentence: split at commas / spaces
            cut = s.rfind(", ", 0, max_chars)
            cut = cut + 1 if cut > max_chars // 2 else s.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(s[:cut].strip())
            s = s[cut:].strip()
        if cur and len(cur) + 1 + len(s) > max_chars:
            chunks.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        chunks.append(cur)
    return [c for c in chunks if c]


def words_from_alignment(chars: list[str], starts: list[float], ends: list[float], offset: float = 0.0) -> list[dict]:
    words, cur, s0, e0 = [], "", None, None
    for ch, s, e in zip(chars, starts, ends):
        if ch.isspace():
            if cur:
                words.append({"text": cur, "start": round(s0 + offset, 4), "end": round(e0 + offset, 4)})
            cur, s0, e0 = "", None, None
            continue
        if not cur:
            s0 = s
        cur += ch
        e0 = e
    if cur:
        words.append({"text": cur, "start": round(s0 + offset, 4), "end": round(e0 + offset, 4)})
    return words


async def tts(api_key: str, text: str, voice_id: str, model_id: str, voice_settings: dict | None, output_format: str,
              out_dir: Path, name: str, language_code: str | None = None) -> dict:
    """Generate speech with timestamps; long scripts are chunked and stitched with
    ``previous_request_ids`` (fallback: previous_text/next_text) for consistent prosody."""
    from elevenlabs.types.voice_settings import VoiceSettings

    c = client(api_key)
    chunks = chunk_text(text)
    parts, words, chars_total, request_ids = [], [], 0, []
    offset = 0.0
    tmp = out_dir / ".tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    all_chars = {"characters": [], "starts": [], "ends": []}
    for i, chunk in enumerate(chunks):
        kw = dict(text=chunk, model_id=model_id, output_format=output_format)
        if voice_settings:
            kw["voice_settings"] = VoiceSettings(**voice_settings)
        if language_code:
            kw["language_code"] = language_code
        if request_ids:
            kw["previous_request_ids"] = request_ids[-3:]
        elif i > 0:
            kw["previous_text"] = " ".join(chunks[:i])[-1000:]
        if i + 1 < len(chunks) and not request_ids:
            kw["next_text"] = chunks[i + 1][:1000]
        resp = await c.text_to_speech.with_raw_response.convert_with_timestamps(voice_id, **kw)
        data = resp.data
        hdr = {k.lower(): v for k, v in resp.headers.items()}
        rid = hdr.get("request-id")
        if rid:
            request_ids.append(rid)
        chars_total += charge_from_headers(hdr, len(chunk))
        audio = base64.b64decode(data.audio_base_64)
        ext = "mp3" if output_format.startswith("mp3") else "bin"
        raw = tmp / f"{name}_{i}.{ext}"
        raw.write_bytes(audio)
        wav = tmp / f"{name}_{i}.wav"
        if output_format.startswith("pcm_"):
            sr = int(output_format.split("_")[1])
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", str(sr), "-ac", "1", "-i", str(raw), "-ar", "48000", "-ac", "2",
                            "-c:a", "pcm_s24le", str(wav)], check=True, capture_output=True)
            import soundfile as sf

            dur = sf.info(str(wav)).duration
        else:
            dur = to_wav(raw, wav)
        al = data.alignment or data.normalized_alignment
        if al is not None:
            words += words_from_alignment(al.characters, al.character_start_times_seconds, al.character_end_times_seconds, offset)
            all_chars["characters"] += list(al.characters)
            all_chars["starts"] += [round(s + offset, 4) for s in al.character_start_times_seconds]
            all_chars["ends"] += [round(e + offset, 4) for e in al.character_end_times_seconds]
        parts.append(wav)
        offset += dur
    out_wav = out_dir / f"{name}.wav"
    if len(parts) == 1:
        shutil.move(str(parts[0]), out_wav)
    else:
        lst = tmp / f"{name}_list.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in parts))
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out_wav)], check=True, capture_output=True)
    shutil.rmtree(tmp, ignore_errors=True)
    words_path = out_dir / f"{name}.words.json"
    words_path.write_text(json.dumps({"words": words, "characters": all_chars, "duration": round(offset, 4)}, indent=1, ensure_ascii=False))
    return {"path": out_wav, "words_path": words_path, "words": words, "duration": round(offset, 4), "chars": chars_total,
            "request_ids": request_ids, "chunks": len(chunks)}


async def save_stream(it, dest: Path) -> int:
    n = 0
    with open(dest, "wb") as f:
        async for chunk in it:
            f.write(chunk)
            n += len(chunk)
    return n


async def raw_stream(cm_factory, dest: Path) -> dict:
    """Run a streaming *raw* SDK call (``with_raw_response``) → file + headers."""
    async with cm_factory() as pending:
        resp = await pending if inspect.isawaitable(pending) else pending
        n = await save_stream(resp.data, dest)
        return {"bytes": n, "headers": {k.lower(): v for k, v in resp.headers.items()}}


def now() -> float:
    return time.time()


_ = (httpx, os)
