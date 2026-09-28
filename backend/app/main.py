"""Luma Studio — FastAPI application (API + SSE + terminal WebSocket + static SPA)."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from . import db
from .config import config
from .events import bus
from .jobs import jobs
from .secrets_store import RedactingFilter, init_secrets

log = logging.getLogger("luma")

BANNER = r"""
  ██╗     ██╗   ██╗███╗   ███╗ █████╗     ███████╗████████╗██╗   ██╗██████╗ ██╗ ██████╗
  ██║     ██║   ██║████╗ ████║██╔══██╗    ██╔════╝╚══██╔══╝██║   ██║██╔══██╗██║██╔═══██╗
  ██║     ██║   ██║██╔████╔██║███████║    ███████╗   ██║   ██║   ██║██║  ██║██║██║   ██║
  ██║     ██║   ██║██║╚██╔╝██║██╔══██║    ╚════██║   ██║   ██║   ██║██║  ██║██║██║   ██║
  ███████╗╚██████╔╝██║ ╚═╝ ██║██║  ██║    ███████║   ██║   ╚██████╔╝██████╔╝██║╚██████╔╝
  ╚══════╝ ╚═════╝ ╚═╝     ╚═╝╚═╝  ╚═╝    ╚══════╝   ╚═╝    ╚═════╝ ╚═════╝ ╚═╝ ╚═════╝
"""

WARNING = """
  ⚠  SECURITY NOTE — every signed-in account gets a shell inside this container and can
  ⚠  spend the API credits saved to it. The first visitor creates the first account, so
  ⚠  keep the port on 127.0.0.1 (the default) until you have signed up. To expose it, use
  ⚠  TLS (a reverse proxy), set LUMA_ALLOWED_HOSTS and set LUMA_ALLOW_SIGNUP=false once
  ⚠  your accounts exist.
"""


def _setup_logging():
    logging.basicConfig(level=os.environ.get("LUMA_LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    f = RedactingFilter()
    for name in ("", "luma", "uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "openai"):
        logging.getLogger(name).addFilter(f)
    for h in logging.getLogger().handlers:
        h.addFilter(f)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_logging()
    os.umask(0o002)  # project workspaces are shared with the sandbox user's group
    config.ensure_dirs()
    db.init_db()
    init_secrets()
    loop = asyncio.get_running_loop()
    bus.bind_loop(loop)
    jobs.bind_loop(loop)
    jobs.recover()
    from .agent.runner import runs
    from .terminal import terminals

    runs.recover()
    print(BANNER, flush=True)
    print(WARNING, flush=True)
    log.warning("Accounts get a sandbox shell: keep the port private, use TLS when exposing it, and close sign-up (LUMA_ALLOW_SIGNUP=false) once accounts exist.")
    if db.get_setting("terminal_network") is False:
        from .routes_settings import apply_network

        apply_network(False)
    yield
    await runs.shutdown()
    terminals.close_all()


def create_app() -> FastAPI:
    app = FastAPI(title="Luma Studio", version="1.0.0", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    from .auth import router as auth_router
    from .routes_long import router as long_router
    from .routes_projects import router as projects_router
    from .routes_runs import router as runs_router
    from .routes_runs import ws_router
    from .routes_settings import public_router
    from .routes_settings import router as settings_router
    from .security import SecurityMiddleware

    app.add_middleware(SecurityMiddleware)
    app.include_router(public_router)
    app.include_router(auth_router)
    app.include_router(ws_router)
    app.include_router(settings_router)
    app.include_router(projects_router)
    app.include_router(runs_router)
    app.include_router(long_router)

    static = Path(config.static_dir)

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith(("api/", "ws/")):
            raise HTTPException(404)
        target = (static / full_path).resolve()
        if full_path and static.exists() and static.resolve() in target.parents and target.is_file():
            return FileResponse(target, headers={"Cache-Control": "public, max-age=31536000, immutable"} if "/assets/" in f"/{full_path}" else None)
        index = static / "index.html"
        if index.exists():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return JSONResponse({"detail": "frontend not built (run `npm run build` in frontend/)"}, 404)

    return app


app = create_app()


def main():  # pragma: no cover - entry point
    import uvicorn

    uvicorn.run("app.main:app", host=config.host, port=config.port, log_level="info", proxy_headers=False,
                ws_max_size=1 << 20, timeout_graceful_shutdown=5)


if __name__ == "__main__":  # pragma: no cover
    main()
