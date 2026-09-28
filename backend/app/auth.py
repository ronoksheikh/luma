"""Simple username/password accounts with server-side sessions (same SQLite DB).

* Passwords: scrypt (stdlib) with a per-user random salt.
* Sessions: random 256-bit token in an HttpOnly, SameSite=Lax cookie; only its SHA-256
  is stored.  30-day sliding expiry.
* Every project (and everything under it: assets, runs, jobs, files, terminal) belongs
  to one user.  API keys saved to the account are encrypted at rest.
"""
from __future__ import annotations

import contextvars
import hashlib
import hmac
import os
import re
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from . import db

COOKIE = "luma_session"
SESSION_DAYS = 30
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")

current_user: contextvars.ContextVar[str | None] = contextvars.ContextVar("luma_user", default=None)
router = APIRouter()


def signup_open() -> bool:
    if not has_users():
        return True
    return os.environ.get("LUMA_ALLOW_SIGNUP", "1").strip().lower() in ("1", "true", "yes", "on")


def has_users() -> bool:
    with db.session() as s:
        return (s.scalar(select(func.count()).select_from(db.User)) or 0) > 0


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, dk = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(dk) // 2)
        return hmac.compare_digest(got.hex(), dk)
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with db.session() as s:
        s.add(db.UserSession(token_hash=_token_hash(token), user_id=user_id, created_at=now, expires_at=now + SESSION_DAYS * 86400))
    return token


def user_from_token(token: str | None) -> db.User | None:
    if not token:
        return None
    now = time.time()
    with db.session() as s:
        sess = s.get(db.UserSession, _token_hash(token))
        if sess is None or sess.expires_at < now:
            return None
        if sess.expires_at - now < (SESSION_DAYS - 1) * 86400:  # sliding expiry, at most daily writes
            sess.expires_at = now + SESSION_DAYS * 86400
        return s.get(db.User, sess.user_id)


def user_from_request(request) -> db.User | None:
    return user_from_token(request.cookies.get(COOKIE))


async def require_user(request: Request) -> db.User:
    """Dependency for every protected route: sets the per-request user context."""
    user = user_from_request(request)
    if user is None:
        raise HTTPException(401, "Not signed in")
    request.state.user = user
    current_user.set(user.id)
    return user


def user_json(u: db.User) -> dict:
    return {"id": u.id, "username": u.username, "created_at": u.created_at}


def _set_cookie(request: Request, response: Response, token: str) -> None:
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=secure, path="/")


class Credentials(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=8, max_length=256)


@router.get("/api/auth/state")
def auth_state(request: Request):
    u = user_from_request(request)
    return {"user": user_json(u) if u else None, "has_users": has_users(), "signup_open": signup_open()}


@router.post("/api/auth/signup", status_code=201)
def signup(body: Credentials, request: Request, response: Response):
    if not signup_open():
        raise HTTPException(403, "Sign-up is closed on this server. Ask the owner for an account.")
    if not USERNAME_RE.match(body.username):
        raise HTTPException(422, "Username: 3–32 characters, letters, numbers, dot, dash or underscore.")
    first = not has_users()
    with db.session() as s:
        if s.scalars(select(db.User).where(func.lower(db.User.username) == body.username.lower())).first():
            raise HTTPException(409, "That username is taken.")
        u = db.User(username=body.username, password_hash=hash_password(body.password))
        s.add(u)
        s.flush()
        uid = u.id
        if first:  # adopt projects created before accounts existed
            for p in s.scalars(select(db.Project).where(db.Project.owner_id.is_(None))):
                p.owner_id = uid
    _set_cookie(request, response, create_session(uid))
    with db.session() as s:
        return {"user": user_json(s.get(db.User, uid))}


@router.post("/api/auth/login")
def login(body: Credentials, request: Request, response: Response):
    with db.session() as s:
        u = s.scalars(select(db.User).where(func.lower(db.User.username) == body.username.lower())).first()
    if u is None or not verify_password(body.password, u.password_hash):
        time.sleep(0.4)  # blunt online guessing
        raise HTTPException(401, "Wrong username or password.")
    _set_cookie(request, response, create_session(u.id))
    return {"user": user_json(u)}


@router.post("/api/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        with db.session() as s:
            sess = s.get(db.UserSession, _token_hash(token))
            if sess is not None:
                s.delete(sess)
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8, max_length=256)


@router.post("/api/auth/password")
def change_password(body: PasswordChange, user: db.User = Depends(require_user)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(401, "Current password is wrong.")
    with db.session() as s:
        u = s.get(db.User, user.id)
        u.password_hash = hash_password(body.new_password)
        for sess in s.scalars(select(db.UserSession).where(db.UserSession.user_id == user.id)):
            s.delete(sess)  # sign out everywhere else
    return {"ok": True, "note": "Password changed; other sessions were signed out. Sign in again."}
