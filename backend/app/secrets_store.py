"""API-key handling: encryption at rest (optional "remember"), credential resolution and
redaction.

Keys are never written to logs, events, the DB (unless the user opts in to "remember",
in which case they are Fernet-encrypted with a key generated on first boot in
``/data/secrets/``), or to the agent's terminal environment.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Request

from . import db
from .config import config

log = logging.getLogger("luma.secrets")

_fernet: Fernet | None = None
_known: set[str] = set()
_known_lock = threading.Lock()

# defence-in-depth patterns (common key formats)
PATTERNS = [
    re.compile(r"sk-or-v1-[A-Za-z0-9]{20,}"),
    re.compile(r"sk-(?:proj-|ant-|live-)?[A-Za-z0-9_\-]{20,}"),
    re.compile(r"(?i)(xi-api-key|authorization|api[_-]?key)(\s*[:=]\s*)(bearer\s+)?[A-Za-z0-9_\-\.]{16,}"),
    re.compile(r"\bsk_[a-f0-9]{40,}\b"),  # ElevenLabs keys
    re.compile(r"\b(?:ghp|gho|github_pat)_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"),
]
REDACTED = "[REDACTED]"


def _key_path():
    return config.secrets_dir / "fernet.key"


def fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        config.ensure_dirs()
        p = _key_path()
        if not p.exists():
            key = Fernet.generate_key()
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(key)
        _fernet = Fernet(p.read_bytes().strip())
    return _fernet


def encrypt(s: str) -> str:
    return fernet().encrypt(s.encode()).decode()


def decrypt(s: str) -> str | None:
    try:
        return fernet().decrypt(s.encode()).decode()
    except (InvalidToken, ValueError):
        return None


def register_secret(value: str | None) -> None:
    """Remember a secret value so it is redacted from every output stream."""
    if value and len(value) >= 8:
        with _known_lock:
            _known.add(value)


def redact(text: str) -> str:
    if not text:
        return text
    with _known_lock:
        known = sorted(_known, key=len, reverse=True)
    for k in known:
        if k in text:
            text = text.replace(k, REDACTED)
    for p in PATTERNS:
        if p.groups >= 2:
            text = p.sub(lambda m: m.group(1) + m.group(2) + REDACTED, text)
        else:
            text = p.sub(REDACTED, text)
    return text


def redact_obj(obj):
    """Recursively redact strings in JSON-like data."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, list):
        return [redact_obj(x) for x in obj]
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    return obj


# ======================================================================================
# credentials
# ======================================================================================


@dataclass
class Credentials:
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    elevenlabs_api_key: str = ""

    @property
    def has_llm(self) -> bool:
        return bool(self.llm_base_url and self.llm_model)

    @property
    def has_elevenlabs(self) -> bool:
        return bool(self.elevenlabs_api_key)

    def __repr__(self) -> str:  # never print keys
        return f"Credentials(base_url={self.llm_base_url!r}, model={self.llm_model!r}, llm_key={'set' if self.llm_api_key else 'unset'}, el_key={'set' if self.elevenlabs_api_key else 'unset'})"


def remembered() -> dict:
    blob = db.get_setting("remembered_credentials")
    if not blob:
        return {}
    import json

    raw = decrypt(blob)
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


def remember(creds: dict) -> None:
    import json

    cur = remembered()
    for k, v in creds.items():
        if v is None:
            continue
        if v == "":
            cur.pop(k, None)
        else:
            cur[k] = v
            register_secret(v if "key" in k else None)
    db.set_setting("remembered_credentials", encrypt(json.dumps(cur)) if cur else None)


def forget() -> None:
    db.set_setting("remembered_credentials", None)


def resolve(request: Request | None = None, headers: dict | None = None) -> Credentials:
    """Header (browser localStorage) > remembered on server > environment."""
    h = {k.lower(): v for k, v in (headers or (dict(request.headers) if request is not None else {})).items()}
    rem = remembered()
    settings = db.all_settings() if db._Session is not None else {}

    def pick(header, rem_key, env_val, setting_key=None):
        v = (h.get(header) or "").strip()
        if v:
            return v
        if rem.get(rem_key):
            return rem[rem_key]
        if env_val:
            return env_val
        if setting_key and settings.get(setting_key):
            return settings[setting_key]
        return ""

    c = Credentials(
        llm_api_key=pick("x-llm-api-key", "llm_api_key", config.llm_api_key),
        llm_base_url=pick("x-llm-base-url", "llm_base_url", config.llm_base_url, "llm_base_url").rstrip("/"),
        llm_model=pick("x-llm-model", "llm_model", config.llm_model, "llm_model"),
        elevenlabs_api_key=pick("x-elevenlabs-api-key", "elevenlabs_api_key", config.elevenlabs_api_key),
    )
    register_secret(c.llm_api_key)
    register_secret(c.elevenlabs_api_key)
    return c


def mask(v: str) -> str:
    if not v:
        return ""
    return v[:4] + "…" + v[-4:] if len(v) > 12 else "…"


def init_secrets() -> None:
    fernet()
    for v in (config.llm_api_key, config.elevenlabs_api_key):
        register_secret(v)
    for k, v in remembered().items():
        if "key" in k:
            register_secret(v)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        red = redact(msg)
        if red != msg:
            record.msg, record.args = red, ()
        return True
