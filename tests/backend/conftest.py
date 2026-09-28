"""Backend test harness: a real uvicorn server (in a thread) on a temp data dir."""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tests"))

_DATA = tempfile.mkdtemp(prefix="luma-test-")
os.environ["LUMA_DATA_DIR"] = _DATA
os.environ.setdefault("LUMA_STATIC_DIR", str(ROOT / "frontend" / "dist"))
os.environ.pop("LLM_API_KEY", None)
os.environ.pop("ELEVENLABS_API_KEY", None)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="session")
def server():
    import uvicorn

    from app.config import reload_config

    reload_config()
    from app.main import app

    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", ws="websockets"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.05)
    assert srv.started, "server did not start"
    yield {"url": f"http://127.0.0.1:{port}", "port": port, "data": Path(_DATA)}
    srv.should_exit = True
    th.join(10)


def login_client(server, username="tester", password="correct horse battery"):
    import httpx

    c = httpx.Client(base_url=server["url"], headers={"X-Luma-Client": "1"}, timeout=60)
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    if r.status_code == 401:
        r = c.post("/api/auth/signup", json={"username": username, "password": password})
    assert r.status_code in (200, 201), r.text
    return c


@pytest.fixture()
def client(server):
    c = login_client(server)
    yield c
    c.close()


def cookie_header(c) -> dict:
    return {"Cookie": "; ".join(f"{k}={v}" for k, v in c.cookies.items())}


@pytest.fixture()
def project(client):
    r = client.post("/api/projects", json={"name": "test project"})
    assert r.status_code == 201, r.text
    return r.json()


def wait_for(fn, timeout=60.0, interval=0.1):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(interval)
    raise AssertionError("timed out waiting")


def run_status(client, run_id):
    return client.get(f"/api/runs/{run_id}").json()["status"]


def events(client, run_id, after=0):
    """Read the replayable history (the SSE stream stays open, so read with a short timeout)."""
    import json

    import httpx

    out = []
    try:
        with client.stream("GET", f"/api/runs/{run_id}/events", params={"after": after}, timeout=httpx.Timeout(2.0)) as r:
            buf = ""
            for chunk in r.iter_text():
                buf += chunk
                while "\n\n" in buf:
                    block, buf = buf.split("\n\n", 1)
                    for line in block.splitlines():
                        if line.startswith("data: "):
                            out.append(json.loads(line[6:]))
    except httpx.ReadTimeout:
        pass
    return out
