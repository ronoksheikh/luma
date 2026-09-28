"""UI tests: a real backend (in a thread, temp data dir), the scripted mock director and Chromium.

    LUMA_CHROMIUM=/path/to/chrome pytest -m ui tests/ui
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tests"))

_DATA = tempfile.mkdtemp(prefix="luma-ui-")
os.environ["LUMA_DATA_DIR"] = _DATA
os.environ.setdefault("LUMA_STATIC_DIR", str(ROOT / "frontend" / "dist"))
os.environ.pop("LLM_API_KEY", None)
os.environ.pop("ELEVENLABS_API_KEY", None)
SHOTS = os.environ.get("LUMA_SCREENSHOTS")


@pytest.fixture(scope="session")
def server():
    import socket

    import uvicorn

    from app.config import reload_config

    reload_config()
    from app.main import app

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", ws="websockets"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.05)
    yield {"url": f"http://127.0.0.1:{port}", "data": Path(_DATA)}
    srv.should_exit = True
    th.join(10)


@pytest.fixture(scope="session")
def browser():
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        exe = os.environ.get("LUMA_CHROMIUM")
        b = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        yield b
        b.close()


def shot(page, name):
    if SHOTS:
        page.wait_for_timeout(600)  # let open/close animations settle
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"))
