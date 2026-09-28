"""ElevenLabs tools (registered only when an ElevenLabs key is configured).

Every call: is shown as a tool card with the characters used; counts against the hard
per-run character budget (checked BEFORE the request); is cached by content hash;
saves audio into audio/ with a JSON sidecar of the request parameters.
"""
from __future__ import annotations

import asyncio
import base64
import json
import math
import shutil
import time
from pathlib import Path

from .. import elevenlabs as EL
from .base import ToolContext, ToolError, ToolOutput, tool, truncate

EL_DIR = "audio"


def _client(ctx: ToolContext):
    if not ctx.creds.elevenlabs_api_key:
        raise ToolError("ElevenLabs is not configured")
    return EL.client(ctx.creds.elevenlabs_api_key)


async def _spend(ctx: ToolContext, estimate: int, what: str) -> None:
    rem = ctx.el_budget.remaining()
    if estimate > rem:
        raise ToolError(f"{what} needs ~{estimate} characters but only {rem} remain in this run's ElevenLabs budget "
                        f"(Settings → ElevenLabs character budget). Shorten the request or ask the user to raise the budget.")


def _safe_name(s: str, default: str) -> str:
    import re

    s = re.sub(r"[^A-Za-z0-9_-]+", "_", s or "").strip("_")[:48]
    return s or default


def _cached(ctx: ToolContext, key: str) -> dict | None:
    return EL.ElCache(ctx.pdir).get(key)


def _remember(ctx: ToolContext, key: str, meta: dict) -> None:
    EL.ElCache(ctx.pdir).put(key, meta)


def _audio_event(ctx: ToolContext, rel: str, label: str, kind: str, **extra) -> None:
    ctx.emit("audio", {"path": rel, "url": ctx.url(rel), "label": label, "kind": kind, **extra})


@tool(
    "el_list_voices",
    "List ElevenLabs voices: your account voices (incl. premade) and matching voices from the shared library, with IDs, names, labels (gender, age, accent, use case) and preview URLs.",
    {"filter": {"type": "string", "description": "Optional search text, e.g. 'calm female british'"},
     "include_library": {"type": "boolean", "default": True}},
    elevenlabs=True,
)
async def el_list_voices(ctx: ToolContext, a: dict) -> ToolOutput:
    c = _client(ctx)
    q = (a.get("filter") or "").strip() or None
    out = []
    try:
        res = await c.voices.search(search=q, page_size=60, include_total_count=False)
        for v in res.voices:
            out.append({"voice_id": v.voice_id, "name": v.name, "source": "account", "category": getattr(v, "category", None),
                        "labels": getattr(v, "labels", None), "description": (getattr(v, "description", None) or "")[:160],
                        "preview_url": getattr(v, "preview_url", None)})
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"voices.search failed: {EL.err_text(e)}") from None
    if a.get("include_library", True):
        try:
            shared = await c.voices.get_shared(page_size=30, search=q)
            for v in shared.voices:
                out.append({"voice_id": v.voice_id, "name": v.name, "source": "library", "category": getattr(v, "category", None),
                            "labels": {k: getattr(v, k, None) for k in ("gender", "age", "accent", "use_case", "language")},
                            "description": (getattr(v, "description", None) or "")[:160], "preview_url": getattr(v, "preview_url", None)})
        except Exception as e:  # noqa: BLE001
            out.append({"note": f"library search unavailable: {EL.err_text(e)}"})
    return ToolOutput(truncate(json.dumps(out, indent=1, default=str), ctx, "voices"), ui={"chars": 0})


@tool("el_list_models", "List ElevenLabs models (TTS / speech-to-speech) with languages and limits.", {}, elevenlabs=True)
async def el_list_models(ctx: ToolContext, a: dict) -> ToolOutput:
    c = _client(ctx)
    try:
        models = await c.models.list()
    except Exception as e:  # noqa: BLE001
        raise ToolError(EL.err_text(e)) from None
    out = [{"model_id": m.model_id, "name": m.name, "tts": m.can_do_text_to_speech, "speech_to_speech": m.can_do_voice_conversion,
            "languages": [lg.language_id for lg in (m.languages or [])], "max_chars": m.maximum_text_length_per_request,
            "cost_multiplier": getattr(getattr(m, "model_rates", None), "character_cost_multiplier", None) or m.token_cost_factor,
            "description": (m.description or "")[:200]} for m in models]
    return ToolOutput(json.dumps(out, indent=1, default=str), ui={"chars": 0})


@tool(
    "el_tts",
    """Text-to-speech with character-level alignment, converted to WORD timings (saved as <name>.words.json:
    {"words":[{"text","start","end"}], "duration"}). Long scripts are chunked at sentence boundaries and stitched
    (previous_request_ids / previous_text) so prosody stays consistent. Output: audio/<name>.wav (48 kHz 24-bit)
    + sidecar JSON. Costs ~len(text) characters (cached if identical).""",
    {
        "text": {"type": "string"},
        "voice_id": {"type": "string"},
        "model_id": {"type": "string", "default": EL.DEFAULT_TTS_MODEL, "description": "e.g. eleven_multilingual_v2, eleven_v3, eleven_flash_v2_5"},
        "voice_settings": {"type": "object", "description": "stability, similarity_boost, style, speed, use_speaker_boost",
                           "properties": {"stability": {"type": "number"}, "similarity_boost": {"type": "number"}, "style": {"type": "number"},
                                          "speed": {"type": "number"}, "use_speaker_boost": {"type": "boolean"}}},
        "output_format": {"type": "string", "default": "mp3_44100_128"},
        "language_code": {"type": "string"},
        "name": {"type": "string", "description": "file stem, e.g. vo_main"},
        "with_timestamps": {"type": "boolean", "default": True},
    },
    ["text", "voice_id"],
    elevenlabs=True,
)
async def el_tts(ctx: ToolContext, a: dict) -> ToolOutput:
    text = str(a["text"]).strip()
    if not text:
        raise ToolError("text is empty")
    model = a.get("model_id") or EL.DEFAULT_TTS_MODEL
    fmt = a.get("output_format") or "mp3_44100_128"
    params = {"text": text, "voice_id": a["voice_id"], "model_id": model, "voice_settings": a.get("voice_settings") or None,
              "output_format": fmt, "language_code": a.get("language_code")}
    key = EL.cache_key("tts", params)
    name = _safe_name(a.get("name"), f"tts_{key[:8]}")
    out_dir = ctx.pdir / EL_DIR
    out_dir.mkdir(exist_ok=True)
    hit = _cached(ctx, key)
    if hit:
        wav, words_p = Path(hit["files"][0]), Path(hit["files"][1])
        dest, dwords = out_dir / f"{name}.wav", out_dir / f"{name}.words.json"
        if wav != dest:
            shutil.copy2(wav, dest)
            shutil.copy2(words_p, dwords)
        words = json.loads(dwords.read_text())
        chars, cached = 0, True
        res = {"path": dest, "words_path": dwords, "words": words["words"], "duration": words["duration"], "chunks": hit.get("chunks", 1)}
    else:
        await _spend(ctx, len(text), "el_tts")
        try:
            res = await EL.tts(ctx.creds.elevenlabs_api_key, text, str(a["voice_id"]), model, a.get("voice_settings"), fmt, out_dir, name,
                               a.get("language_code"))
        except Exception as e:  # noqa: BLE001
            raise ToolError(f"TTS failed: {EL.err_text(e)}") from None
        chars, cached = res["chars"], False
        ctx.el_budget.charge(chars)
        _remember(ctx, key, {"files": [str(res["path"]), str(res["words_path"])], "chunks": res["chunks"], "created": time.time()})
    EL.write_sidecar(Path(res["path"]), {"tool": "el_tts", "params": params, "chars": chars, "cached": cached,
                                         "request_ids": res.get("request_ids", []), "chunks": res.get("chunks"), "duration": res["duration"],
                                         "words_path": ctx.rel(Path(res["words_path"]))})
    rel = ctx.rel(Path(res["path"]))
    _audio_event(ctx, rel, f"Voice-over · {name}", "voice", text=text, chars=chars, cached=cached, voice_id=a["voice_id"], model_id=model,
                 duration=res["duration"])
    words = res["words"]
    preview = " ".join(f"{w['text']}@{w['start']:.2f}" for w in words[:40])
    return ToolOutput(
        f"audio: {rel} ({res['duration']:.2f}s, {len(words)} words, {res.get('chunks', 1)} chunk(s)); word timings: {ctx.rel(Path(res['words_path']))}\n"
        f"characters charged: {chars}{' (cached — free)' if cached else ''}; budget remaining: {ctx.el_budget.remaining()}\n"
        f"first words: {preview}", ui={"chars": chars, "cached": cached, "audio": {"path": rel, "url": ctx.url(rel)}})


@tool(
    "el_design_voice",
    "Generate voice previews from a text description (ElevenLabs Voice Design). Returns preview IDs + audio files; save one with el_save_designed_voice.",
    {"description": {"type": "string", "description": "20–1000 chars: age, gender, accent, tone, pacing, recording quality"},
     "sample_text": {"type": "string", "description": "100–1000 chars to speak (optional; auto-generated otherwise)"},
     "model_id": {"type": "string", "default": "eleven_multilingual_ttv_v2"}},
    ["description"],
    elevenlabs=True,
)
async def el_design_voice(ctx: ToolContext, a: dict) -> ToolOutput:
    desc = str(a["description"]).strip()
    sample = (a.get("sample_text") or "").strip()
    est = len(sample) if sample else 200
    params = {"description": desc, "sample_text": sample, "model_id": a.get("model_id") or "eleven_multilingual_ttv_v2"}
    key = EL.cache_key("design", params)
    hit = _cached(ctx, key)
    out_dir = ctx.pdir / EL_DIR / "voice_previews"
    out_dir.mkdir(parents=True, exist_ok=True)
    if hit:
        previews, chars, cached = hit["previews"], 0, True
    else:
        await _spend(ctx, est, "el_design_voice")
        c = _client(ctx)
        kw = {"voice_description": desc, "model_id": params["model_id"]}
        if sample:
            kw["text"] = sample
        else:
            kw["auto_generate_text"] = True
        try:
            resp = await c.text_to_voice.with_raw_response.design(**kw)
        except Exception as e:  # noqa: BLE001
            raise ToolError(f"voice design failed: {EL.err_text(e)}") from None
        data = resp.data
        chars = EL.charge_from_headers(resp.headers, len(data.text or sample) or est)
        ctx.el_budget.charge(chars)
        previews = []
        for p in data.previews:
            f = out_dir / f"{p.generated_voice_id}.mp3"
            f.write_bytes(base64.b64decode(p.audio_base_64))
            previews.append({"preview_id": p.generated_voice_id, "path": ctx.rel(f), "duration": p.duration_secs, "text": data.text})
        cached = False
        _remember(ctx, key, {"files": [str(ctx.pdir / p["path"]) for p in previews], "previews": previews})
    for p in previews:
        EL.write_sidecar(ctx.pdir / p["path"], {"tool": "el_design_voice", "params": params, "chars": chars, "cached": cached})
        _audio_event(ctx, p["path"], f"Voice preview {p['preview_id'][:8]}", "voice_preview", text=p.get("text"), chars=chars, cached=cached)
    return ToolOutput(json.dumps({"previews": previews, "chars": chars, "cached": cached}, indent=1), ui={"chars": chars, "cached": cached})


@tool(
    "el_save_designed_voice",
    "Save a designed voice preview to the account so it can be used with el_tts. Returns the new voice_id.",
    {"preview_id": {"type": "string"}, "name": {"type": "string"}, "description": {"type": "string"}},
    ["preview_id", "name"],
    elevenlabs=True,
)
async def el_save_designed_voice(ctx: ToolContext, a: dict) -> ToolOutput:
    c = _client(ctx)
    try:
        v = await c.text_to_voice.create(voice_name=str(a["name"])[:100], voice_description=str(a.get("description") or a["name"])[:1000],
                                         generated_voice_id=str(a["preview_id"]))
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"saving the voice failed: {EL.err_text(e)}") from None
    return ToolOutput(json.dumps({"voice_id": v.voice_id, "name": v.name}), ui={"chars": 0})


def _sfx_estimate(duration: float | None) -> int:
    # ElevenLabs bills sound effects by generated duration; without an explicit duration a
    # flat per-generation amount applies.  Conservative estimate for the budget check.
    return int(math.ceil(40 * duration)) if duration else 200


@tool(
    "el_sound_effect",
    "Generate a sound effect from a prompt (e.g. 'deep cinematic sub impact with glassy shimmer tail'). duration_s 0.5–30 (auto if omitted); prompt_influence 0–1 (default 0.3). Saved to audio/sfx_*.wav (48 kHz) + sidecar.",
    {"prompt": {"type": "string"}, "duration_s": {"type": "number"}, "prompt_influence": {"type": "number"},
     "name": {"type": "string"}, "loop": {"type": "boolean", "default": False}},
    ["prompt"],
    elevenlabs=True,
)
async def el_sound_effect(ctx: ToolContext, a: dict) -> ToolOutput:
    prompt = str(a["prompt"]).strip()
    dur = a.get("duration_s")
    if dur is not None:
        dur = float(dur)
        if not 0.5 <= dur <= 30:
            raise ToolError("duration_s must be between 0.5 and 30")
    pi = a.get("prompt_influence")
    if pi is not None and not 0 <= float(pi) <= 1:
        raise ToolError("prompt_influence must be between 0 and 1")
    params = {"prompt": prompt, "duration_s": dur, "prompt_influence": pi, "loop": bool(a.get("loop"))}
    key = EL.cache_key("sfx", params)
    name = _safe_name(a.get("name"), f"sfx_{key[:8]}")
    out_dir = ctx.pdir / EL_DIR
    dest = out_dir / f"{name}.wav"
    hit = _cached(ctx, key)
    if hit:
        src = Path(hit["files"][0])
        if src != dest:
            shutil.copy2(src, dest)
        chars, cached, seconds = 0, True, hit.get("duration")
    else:
        est = _sfx_estimate(dur)
        await _spend(ctx, est, "el_sound_effect")
        c = _client(ctx)
        kw = {"text": prompt, "output_format": "mp3_44100_128"}
        if dur is not None:
            kw["duration_seconds"] = dur
        if pi is not None:
            kw["prompt_influence"] = float(pi)
        if a.get("loop"):
            kw["loop"] = True
        raw = out_dir / f".{name}.mp3"
        try:
            res = await EL.raw_stream(lambda: c.text_to_sound_effects.with_raw_response.convert(**kw), raw)
        except Exception as e:  # noqa: BLE001
            raise ToolError(f"sound effect failed: {EL.err_text(e)}") from None
        seconds = await asyncio.to_thread(EL.to_wav, raw, dest)
        raw.unlink(missing_ok=True)
        chars = EL.charge_from_headers(res["headers"], est)
        ctx.el_budget.charge(chars)
        cached = False
        _remember(ctx, key, {"files": [str(dest)], "duration": seconds})
    EL.write_sidecar(dest, {"tool": "el_sound_effect", "params": params, "chars": chars, "cached": cached, "duration": seconds})
    rel = ctx.rel(dest)
    _audio_event(ctx, rel, f"SFX · {prompt[:60]}", "sfx", text=prompt, chars=chars, cached=cached, duration=seconds)
    return ToolOutput(f"sfx: {rel} ({seconds:.2f}s); characters charged {chars}{' (cached)' if cached else ''}; remaining {ctx.el_budget.remaining()}",
                      ui={"chars": chars, "cached": cached, "audio": {"path": rel, "url": ctx.url(rel)}})


@tool(
    "el_music",
    "Compose music from a prompt (ElevenLabs Music). Only works if the account has music access; otherwise reports unavailable — then synthesise a bed with luma_engine.audio. duration_s 3–600.",
    {"prompt": {"type": "string"}, "duration_s": {"type": "number"}, "instrumental": {"type": "boolean", "default": True},
     "name": {"type": "string"}},
    ["prompt", "duration_s"],
    elevenlabs=True,
)
async def el_music(ctx: ToolContext, a: dict) -> ToolOutput:
    from ... import db

    caps = db.get_setting("elevenlabs_caps") or {}
    if caps.get("music") is False:
        return ToolOutput("el_music is unavailable for this ElevenLabs account/plan (detected by the connection test). "
                          "Synthesise a music bed with luma_engine.audio instead.", "error")
    dur = float(a["duration_s"])
    if not 3 <= dur <= 600:
        raise ToolError("duration_s must be between 3 and 600")
    params = {"prompt": str(a["prompt"]).strip(), "duration_s": dur, "instrumental": bool(a.get("instrumental", True))}
    key = EL.cache_key("music", params)
    name = _safe_name(a.get("name"), f"music_{key[:8]}")
    dest = ctx.pdir / EL_DIR / f"{name}.wav"
    hit = _cached(ctx, key)
    if hit:
        src = Path(hit["files"][0])
        if src != dest:
            shutil.copy2(src, dest)
        chars, cached, seconds = 0, True, hit.get("duration")
    else:
        est = int(math.ceil(dur * 50))
        await _spend(ctx, est, "el_music")
        c = _client(ctx)
        raw = ctx.pdir / EL_DIR / f".{name}.mp3"
        kw = {"prompt": params["prompt"], "music_length_ms": int(dur * 1000), "output_format": "mp3_44100_128"}
        if params["instrumental"]:
            kw["force_instrumental"] = True
        try:
            res = await EL.raw_stream(lambda: c.music.with_raw_response.compose(**kw), raw)
        except Exception as e:  # noqa: BLE001
            sc = EL.status_of(e)
            if sc in (401, 402, 403):
                caps["music"] = False
                db.set_setting("elevenlabs_caps", caps)
                return ToolOutput(f"el_music is unavailable for this account ({EL.err_text(e)}). Synthesise music with luma_engine.audio.", "error")
            raise ToolError(f"music failed: {EL.err_text(e)}") from None
        seconds = await asyncio.to_thread(EL.to_wav, raw, dest)
        raw.unlink(missing_ok=True)
        chars = EL.charge_from_headers(res["headers"], est)
        ctx.el_budget.charge(chars)
        cached = False
        _remember(ctx, key, {"files": [str(dest)], "duration": seconds})
    EL.write_sidecar(dest, {"tool": "el_music", "params": params, "chars": chars, "cached": cached, "duration": seconds})
    rel = ctx.rel(dest)
    _audio_event(ctx, rel, f"Music · {params['prompt'][:60]}", "music", text=params["prompt"], chars=chars, cached=cached, duration=seconds)
    return ToolOutput(f"music: {rel} ({seconds:.2f}s); characters charged {chars}{' (cached)' if cached else ''}",
                      ui={"chars": chars, "cached": cached, "audio": {"path": rel, "url": ctx.url(rel)}})


@tool(
    "el_speech_to_text",
    "Transcribe an audio/video file with word timestamps (ElevenLabs Scribe), e.g. a user-uploaded voice-over or to verify TTS alignment. Saves <file>.transcript.json.",
    {"path": {"type": "string"}, "language_code": {"type": "string"}, "model_id": {"type": "string", "default": EL.DEFAULT_STT_MODEL}},
    ["path"],
    elevenlabs=True,
)
async def el_speech_to_text(ctx: ToolContext, a: dict) -> ToolOutput:
    src = ctx.resolve(a["path"], must_exist=True)
    import hashlib

    digest = hashlib.sha256(src.read_bytes()).hexdigest()[:16]
    params = {"file_sha": digest, "language_code": a.get("language_code"), "model_id": a.get("model_id") or EL.DEFAULT_STT_MODEL}
    key = EL.cache_key("stt", params)
    out = ctx.pdir / EL_DIR / f"{src.stem}.transcript.json"
    hit = _cached(ctx, key)
    if hit:
        shutil.copy2(hit["files"][0], out) if Path(hit["files"][0]) != out else None
        chars, cached = 0, True
        data = json.loads(out.read_text())
    else:
        from luma_engine.audio import read_audio

        dur = len(await asyncio.to_thread(read_audio, str(src))) / 48000
        est = int(math.ceil(dur * 15))
        await _spend(ctx, est, "el_speech_to_text")
        c = _client(ctx)
        kw = {"model_id": params["model_id"], "timestamps_granularity": "word"}
        if params["language_code"]:
            kw["language_code"] = params["language_code"]
        try:
            with open(src, "rb") as fh:
                resp = await c.speech_to_text.with_raw_response.convert(file=(src.name, fh.read()), **kw)
        except Exception as e:  # noqa: BLE001
            if EL.status_of(e) in (400, 422) and params["model_id"] == "scribe_v2":
                with open(src, "rb") as fh:
                    resp = await c.speech_to_text.with_raw_response.convert(file=(src.name, fh.read()), model_id="scribe_v1", timestamps_granularity="word")
            else:
                raise ToolError(f"speech-to-text failed: {EL.err_text(e)}") from None
        r = resp.data
        words = [{"text": w.text, "start": w.start, "end": w.end, "type": str(w.type)} for w in (getattr(r, "words", None) or [])]
        data = {"text": getattr(r, "text", ""), "language_code": getattr(r, "language_code", None), "words": [w for w in words if w["type"] == "word"],
                "all_tokens": words}
        out.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        chars = EL.charge_from_headers(resp.headers, est)
        ctx.el_budget.charge(chars)
        cached = False
        _remember(ctx, key, {"files": [str(out)]})
    EL.write_sidecar(out, {"tool": "el_speech_to_text", "params": params, "chars": chars, "cached": cached})
    return ToolOutput(truncate(f"transcript: {ctx.rel(out)}; chars charged {chars}\ntext: {data['text'][:2000]}\nwords: "
                               + json.dumps(data["words"][:80]), ctx, "stt"), ui={"chars": chars, "cached": cached})
