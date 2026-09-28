"""A mock ElevenLabs HTTP API (just the endpoints Luma Studio uses) for tests.

Generates real MP3 audio with ffmpeg (sine tones), plausible alignment data and the
``request-id`` / ``character-cost`` headers.  Counts calls per endpoint in ``state``.
"""
from __future__ import annotations

import base64
import functools
import subprocess
import threading
import time
import uuid

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response


@functools.lru_cache(maxsize=32)
def mp3(seconds: float, freq: int = 440) -> bytes:
    return subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}", "-ac", "1", "-ar", "44100",
                           "-b:a", "128k", "-f", "mp3", "-"], capture_output=True, check=True).stdout


def make_app(state: dict, music_allowed: bool = False) -> FastAPI:
    app = FastAPI()
    state.setdefault("calls", {})

    def hit(name, req: Request):
        state["calls"][name] = state["calls"].get(name, 0) + 1
        state.setdefault("keys", set()).add(req.headers.get("xi-api-key"))
        if req.headers.get("xi-api-key") != state.get("key", "el-test-key-123456"):
            return JSONResponse({"detail": {"status": "invalid_api_key", "message": "Invalid API key"}}, 401)
        return None

    @app.get("/v1/user")
    async def user(req: Request):
        return hit("user", req) or {"user_id": "u1", "first_name": "Test", "is_new_user": False, "created_at": 0,
                                    "subscription": {"tier": "creator", "character_count": 1200, "character_limit": 100000}}

    @app.get("/v1/user/subscription")
    async def sub(req: Request):
        return hit("subscription", req) or {"tier": "creator", "character_count": 1200, "character_limit": 100000, "status": "active",
                                            "can_use_instant_voice_cloning": True, "next_character_count_reset_unix": 2000000000}

    @app.get("/v1/models")
    async def models(req: Request):
        return hit("models", req) or [
            {"model_id": "eleven_multilingual_v2", "name": "Multilingual v2", "can_do_text_to_speech": True, "can_do_voice_conversion": False,
             "languages": [{"language_id": "en", "name": "English"}], "maximum_text_length_per_request": 10000},
            {"model_id": "eleven_english_sts_v2", "name": "STS", "can_do_text_to_speech": False, "can_do_voice_conversion": True, "languages": []}]

    @app.get("/v2/voices")
    async def voices(req: Request):
        return hit("voices", req) or {"voices": [{"voice_id": "voice_calm_f", "name": "Aria", "category": "premade",
                                                  "labels": {"gender": "female", "accent": "british", "description": "calm"},
                                                  "preview_url": "https://example.invalid/aria.mp3"}], "has_more": False, "total_count": 1}

    @app.get("/v1/shared-voices")
    async def shared(req: Request):
        return hit("shared", req) or {"voices": [{"public_owner_id": "o", "voice_id": "lib_voice_1", "name": "Narrator", "gender": "male",
                                                  "age": "middle_aged", "accent": "american", "use_case": "narration", "category": "professional",
                                                  "preview_url": "https://example.invalid/n.mp3"}], "has_more": False}

    @app.post("/v1/text-to-speech/{voice_id}/with-timestamps")
    async def tts(voice_id: str, req: Request):
        err = hit("tts", req)
        if err:
            return err
        body = await req.json()
        state.setdefault("tts_bodies", []).append(body)
        text = body["text"]
        dur = max(0.4, 0.06 * len(text))
        chars = list(text)
        step = dur / max(len(chars), 1)
        payload = {"audio_base64": base64.b64encode(mp3(round(dur, 2))).decode(),
                   "alignment": {"characters": chars, "character_start_times_seconds": [round(i * step, 4) for i in range(len(chars))],
                                 "character_end_times_seconds": [round((i + 1) * step, 4) for i in range(len(chars))]}}
        return JSONResponse(payload, headers={"request-id": "req_" + uuid.uuid4().hex[:10], "character-cost": str(len(text))})

    @app.post("/v1/sound-generation")
    async def sfx(req: Request):
        err = hit("sfx", req)
        if err:
            return err
        body = await req.json()
        if not body.get("text"):
            return JSONResponse({"detail": [{"loc": ["body", "text"], "msg": "field required", "type": "value_error"}]}, 422)
        d = body.get("duration_seconds") or 1.5
        return Response(mp3(float(d), 220), media_type="audio/mpeg", headers={"character-cost": str(int(40 * d))})

    @app.post("/v1/music")
    async def music(req: Request):
        err = hit("music", req)
        if err:
            return err
        if not music_allowed:
            return JSONResponse({"detail": {"status": "payment_required", "message": "Music API requires a paid plan"}}, 402)
        body = await req.json()
        if not body.get("prompt") or (body.get("music_length_ms") or 0) < 3000:
            return JSONResponse({"detail": "invalid"}, 422)
        return Response(mp3(body["music_length_ms"] / 1000, 330), media_type="audio/mpeg")

    @app.post("/v1/text-to-voice/design")
    async def design(req: Request):
        err = hit("design", req)
        if err:
            return err
        body = await req.json()
        if len(body.get("voice_description", "")) < 20:
            return JSONResponse({"detail": "voice_description too short"}, 422)
        text = body.get("text") or "Hello, this is an automatically generated sample text for a designed voice preview."
        return {"text": text, "previews": [{"audio_base_64": base64.b64encode(mp3(1.0, 500 + 50 * i)).decode(), "generated_voice_id": f"gen_{i}",
                                             "media_type": "audio/mpeg", "duration_secs": 1.0} for i in range(3)]}

    @app.post("/v1/text-to-voice")
    async def save_voice(req: Request):
        err = hit("save_voice", req)
        if err:
            return err
        body = await req.json()
        return {"voice_id": "saved_" + body["generated_voice_id"], "name": body["voice_name"], "category": "generated"}

    @app.post("/v1/speech-to-text")
    async def stt(req: Request):
        err = hit("stt", req)
        if err:
            return err
        form = await req.form()
        if form.get("model_id") in (None, "__probe__") or "file" not in form:
            return JSONResponse({"detail": "invalid model or missing file"}, 422)
        return {"language_code": "en", "language_probability": 0.99, "text": "hello world",
                "words": [{"text": "hello", "start": 0.1, "end": 0.4, "type": "word", "logprob": -0.1},
                          {"text": " ", "start": 0.4, "end": 0.45, "type": "spacing", "logprob": 0},
                          {"text": "world", "start": 0.45, "end": 0.9, "type": "word", "logprob": -0.1}]}

    return app


class MockElevenLabs:
    def __init__(self, music_allowed: bool = False):
        import socket

        self.state: dict = {"key": "el-test-key-123456"}
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        self.server = uvicorn.Server(uvicorn.Config(make_app(self.state, music_allowed), host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                break
            time.sleep(0.05)
        return self

    def __exit__(self, *a):
        self.server.should_exit = True
        self.thread.join(5)
