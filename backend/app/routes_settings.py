"""Settings, model listing, connection tests (LLM + ElevenLabs), health."""
from __future__ import annotations

import shutil
import subprocess
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import db
from .auth import require_user
from .agent.llm import PRESETS, describe_error, list_models, test_connection
from .config import DEFAULT_SETTINGS, config
from .secrets_store import Credentials, forget, mask, remember, remembered, resolve

router = APIRouter(dependencies=[Depends(require_user)])
public_router = APIRouter()


def load_settings() -> dict:
    st = dict(DEFAULT_SETTINGS)
    st.update({k: v for k, v in db.all_settings().items() if k in DEFAULT_SETTINGS or k in ("job_concurrency", "model_caps")})
    st.setdefault("job_concurrency", config.job_concurrency)
    return st


def default_director_prompt() -> str:
    p = config.prompts_dir / "director.md"
    return p.read_text() if p.exists() else "You are a motion director."


def director_prompt() -> str:
    return load_settings().get("director_prompt") or default_director_prompt()


def model_caps(base_url: str, model: str) -> dict:
    caps = (db.get_setting("model_caps") or {}).get(f"{base_url}|{model}")
    return caps or {}


@router.get("/api/settings")
def get_settings(request: Request):
    st = load_settings()
    c = resolve(request)
    rem = remembered()
    hdr = {k.lower() for k in request.headers.keys()}

    def source(header, rem_key, env_val):
        if header in hdr and request.headers.get(header):
            return "browser"
        if rem.get(rem_key):
            return "server"
        if env_val:
            return "env"
        return "none"

    st_public = {k: v for k, v in st.items() if k != "model_caps"}
    return {
        "settings": st_public,
        "llm": {
            "configured": bool(c.llm_base_url and c.llm_model),
            "base_url": c.llm_base_url, "model": c.llm_model,
            "key_source": source("x-llm-api-key", "llm_api_key", config.llm_api_key),
            "key_masked": mask(c.llm_api_key),
            "capabilities": model_caps(c.llm_base_url, c.llm_model),
        },
        "elevenlabs": {
            "configured": bool(c.elevenlabs_api_key),
            "key_source": source("x-elevenlabs-api-key", "elevenlabs_api_key", config.elevenlabs_api_key),
            "key_masked": mask(c.elevenlabs_api_key),
            "capabilities": db.get_setting("elevenlabs_caps") or {},
        },
        "env_prefill": {
            "llm_base_url": config.llm_base_url, "llm_model": config.llm_model,
            "llm_api_key": bool(config.llm_api_key), "elevenlabs_api_key": bool(config.elevenlabs_api_key),
        },
        "remembered": {k: bool(v) for k, v in rem.items()},
        "presets": PRESETS,
        "limits": {"max_files": config.max_files, "max_file_mb": config.max_file_bytes // (1024 * 1024)},
        "network_control_available": bool(config.netctl),
        "version": "1.0.0",
    }


class SettingsPatch(BaseModel):
    temperature: float | None = Field(None, ge=0, le=2)
    max_steps: int | None = Field(None, ge=1, le=2000)
    wall_clock_minutes: float | None = Field(None, ge=1, le=1440)
    context_budget_tokens: int | None = Field(None, ge=8000, le=4_000_000)
    el_char_budget: int | None = Field(None, ge=0, le=5_000_000)
    terminal_network: bool | None = None
    director_prompt: str | None = Field(None, max_length=200_000)
    reset_director_prompt: bool = False
    llm_base_url: str | None = Field(None, max_length=500)
    llm_model: str | None = Field(None, max_length=300)
    llm_preset: str | None = Field(None, max_length=32)
    job_concurrency: int | None = Field(None, ge=1, le=8)
    tool_wait_seconds: int | None = Field(None, ge=10, le=3600)


@router.put("/api/settings")
def put_settings(body: SettingsPatch):
    data = body.model_dump(exclude_none=True)
    reset = data.pop("reset_director_prompt", False)
    for k, v in data.items():
        db.set_setting(k, v)
    if reset:
        db.set_setting("director_prompt", None)
    if "terminal_network" in data:
        apply_network(data["terminal_network"])
    return load_settings()


def apply_network(on: bool) -> dict:
    """Toggle outbound network for the sandbox user (iptables owner match via a tiny
    root helper, see docker/luma-netctl).  Returns the helper's report."""
    if not config.netctl:
        return {"applied": False, "reason": "network control helper not configured (LUMA_NETCTL)"}
    try:
        p = subprocess.run(config.netctl.split() + ["on" if on else "off"], capture_output=True, text=True, timeout=20)
        return {"applied": p.returncode == 0, "output": (p.stdout + p.stderr)[-500:]}
    except Exception as e:  # noqa: BLE001
        return {"applied": False, "reason": str(e)}


@router.get("/api/settings/director-prompt")
def get_director_prompt():
    return {"prompt": director_prompt(), "default": default_director_prompt(), "customized": bool(load_settings().get("director_prompt"))}


class RememberIn(BaseModel):
    llm_api_key: str | None = Field(None, max_length=1000)
    llm_base_url: str | None = Field(None, max_length=500)
    llm_model: str | None = Field(None, max_length=300)
    elevenlabs_api_key: str | None = Field(None, max_length=1000)


@router.post("/api/settings/remember")
def remember_keys(body: RememberIn):
    remember(body.model_dump(exclude_none=True))
    return {"remembered": {k: bool(v) for k, v in remembered().items()}}


@router.delete("/api/settings/remember")
def forget_keys():
    forget()
    return {"remembered": {}}


class ConnIn(BaseModel):
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None


def _creds(request: Request, body: ConnIn | None) -> Credentials:
    c = resolve(request)
    if body is not None:
        if body.api_key:
            c.llm_api_key = body.api_key
        if body.base_url:
            c.llm_base_url = body.base_url.rstrip("/")
        if body.model:
            c.llm_model = body.model
    from .secrets_store import register_secret

    register_secret(c.llm_api_key)
    return c


@router.post("/api/models")
async def models(request: Request, body: ConnIn | None = None):
    c = _creds(request, body)
    if not c.llm_base_url:
        raise HTTPException(400, "base URL required")
    try:
        return {"models": await list_models(c)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Could not list models: {describe_error(e)}") from None


@router.post("/api/test-connection")
async def test_conn(request: Request, body: ConnIn | None = None):
    c = _creds(request, body)
    if not (c.llm_base_url and c.llm_model):
        raise HTTPException(400, "base URL and model are required")
    res = await test_connection(c)
    caps = db.get_setting("model_caps") or {}
    caps[f"{c.llm_base_url}|{c.llm_model}"] = {**res["capabilities"], "tested_at": time.time()}
    db.set_setting("model_caps", caps)
    return res


class ElIn(BaseModel):
    api_key: str | None = None


@router.post("/api/elevenlabs/test")
async def el_test(request: Request, body: ElIn | None = None):
    c = resolve(request)
    if body and body.api_key:
        c.elevenlabs_api_key = body.api_key
        from .secrets_store import register_secret

        register_secret(body.api_key)
    if not c.elevenlabs_api_key:
        raise HTTPException(400, "ElevenLabs API key required")
    from .agent.elevenlabs import probe_account

    res = await probe_account(c.elevenlabs_api_key)
    if res.get("ok"):
        db.set_setting("elevenlabs_caps", res.get("capabilities", {}))
    return res


@public_router.get("/healthz")
def healthz():
    ok_db = True
    try:
        db.get_setting("__probe__")
    except Exception:
        ok_db = False
    return {"status": "ok" if ok_db else "degraded", "db": ok_db, "ffmpeg": shutil.which("ffmpeg") is not None, "time": time.time()}


_ = describe_error
