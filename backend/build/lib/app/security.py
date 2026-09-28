"""Request hardening for an app that has no login.

* Host allow-list (blocks DNS-rebinding attacks against 127.0.0.1).
* State-changing API calls must carry ``X-Luma-Client`` — a non-simple header, so a
  malicious web page cannot forge them cross-origin (the CORS preflight fails).
* WebSockets (not covered by CORS) must come from an allowed Origin.
* Request size limit and standard security headers.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .config import config

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def allowed_hosts() -> set[str]:
    extra = {h.strip().lower() for h in os.environ.get("LUMA_ALLOWED_HOSTS", "").split(",") if h.strip()}
    return {"localhost", "127.0.0.1", "[::1]", "::1", "testserver"} | extra


def host_ok(host_header: str | None) -> bool:
    if not host_header:
        return False
    hosts = allowed_hosts()
    if "*" in hosts:
        return True
    h = host_header.lower()
    name = h.rsplit(":", 1)[0] if not h.startswith("[") else h.split("]")[0] + "]"
    return name in hosts or h in hosts


def origin_ok(origin: str | None, host_header: str | None) -> bool:
    if not origin:
        return True  # non-browser clients (curl, tests) send no Origin
    o = urlparse(origin)
    if not o.hostname:
        return False
    if host_header and o.netloc.lower() == host_header.lower():
        return True
    dev = os.environ.get("LUMA_DEV_ORIGIN", "")
    return bool(dev) and origin.rstrip("/") == dev.rstrip("/")


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path == "/healthz":
            return await call_next(request)
        if not host_ok(request.headers.get("host")):
            return JSONResponse({"detail": "Host not allowed. Set LUMA_ALLOWED_HOSTS if you run behind a proxy."}, 421)
        if path.startswith("/api/"):
            if request.method not in SAFE_METHODS:
                if request.headers.get("x-luma-client") != "1":
                    return JSONResponse({"detail": "missing X-Luma-Client header"}, 403)
                if not origin_ok(request.headers.get("origin"), request.headers.get("host")):
                    return JSONResponse({"detail": "cross-origin request refused"}, 403)
            cl = request.headers.get("content-length")
            if cl and cl.isdigit() and int(cl) > config.max_request_bytes:
                return JSONResponse({"detail": f"request too large (limit {config.max_request_bytes // (1024 * 1024)} MB)"}, 413)
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        if not path.startswith("/api/files/"):
            resp.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self' ws: wss:; "
                "style-src 'self' 'unsafe-inline'; font-src 'self' data:; script-src 'self'; frame-ancestors 'none'",
            )
        return resp
