"""End-to-end against a running container.

    docker compose up -d --build
    LUMA_E2E_URL=http://127.0.0.1:8080 pytest -m e2e tests/e2e -v

Asserts /healthz, that the API requires a login, the keyless demo (300-frame, 5.000 s,
60 fps MP4 with audio, QC pass), the sandbox (user, scrubbed env, sudo, API port blocked)
and the terminal WebSocket.
"""
import asyncio
import json
import os
import subprocess
import tempfile
import time

import httpx
import pytest

pytestmark = pytest.mark.e2e
BASE = os.environ.get("LUMA_E2E_URL", "http://127.0.0.1:8080")
H = {"X-Luma-Client": "1"}
USER = {"username": os.environ.get("LUMA_E2E_USER", "e2e_user"), "password": os.environ.get("LUMA_E2E_PASSWORD", "e2e password 123")}


@pytest.fixture(scope="module")
def client():
    """A signed-in client (signs up on a fresh instance, otherwise logs in)."""
    c = httpx.Client(base_url=BASE, headers=H, timeout=60)
    r = c.post("/api/auth/login", json=USER)
    if r.status_code == 401:
        r = c.post("/api/auth/signup", json=USER)
    assert r.status_code in (200, 201), r.text
    yield c
    c.close()


def _wait(fn, timeout, interval=2.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(interval)
    raise AssertionError("timeout")


def test_healthz():
    r = httpx.get(f"{BASE}/healthz", timeout=10)
    assert r.status_code == 200 and r.json()["status"] == "ok" and r.json()["ffmpeg"]


def test_spa_is_served():
    r = httpx.get(f"{BASE}/", timeout=10)
    assert r.status_code == 200 and '<div id="root">' in r.text
    assert "Content-Security-Policy" in r.headers


def test_api_requires_login():
    assert httpx.get(f"{BASE}/api/projects", timeout=10).status_code == 401
    assert httpx.post(f"{BASE}/api/demo", headers=H, timeout=10).status_code == 401


def test_demo_renders_300_frames_5s_60fps_with_audio(client):
    r = client.post("/api/demo").json()
    rid, pid = r["run"]["id"], r["project"]["id"]
    run = _wait(lambda: (lambda x: x if x["status"] in ("completed", "failed") else None)(client.get(f"/api/runs/{rid}").json()), 1800, 5)
    assert run["status"] == "completed", run
    with tempfile.TemporaryDirectory() as d:
        mp4 = os.path.join(d, "demo.mp4")
        with open(mp4, "wb") as f:
            f.write(client.get(f"/api/files/{pid}/outputs/luma_demo.mp4", timeout=120).content)
        probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_streams", "-show_format", "-of", "json", mp4],
                                          capture_output=True, text=True, check=True).stdout)
    v = next(s for s in probe["streams"] if s["codec_type"] == "video")
    a = [s for s in probe["streams"] if s["codec_type"] == "audio"]
    assert int(v["nb_read_frames"]) == 300
    assert v["r_frame_rate"] == "60/1"
    assert abs(int(v["nb_read_frames"]) / 60 - 5.000) < 1e-9
    assert (v["width"], v["height"]) == (1920, 1080) and v["codec_name"] == "h264" and v["profile"] == "High"
    assert v.get("color_primaries") == "bt709"
    assert a and a[0]["codec_name"] == "aac"
    qc = client.get(f"/api/files/{pid}/outputs/qc_report.json").json()
    assert qc["pass"], [c for c in qc["checks"] if not c["pass"]]


def test_sandbox_terminal_is_scrubbed_and_isolated(client):
    import websockets

    pid = client.post("/api/projects", json={"name": "e2e sandbox"}).json()["id"]
    cookie = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
    cmd = ("echo USER=$(id -un); env | grep -ci -E 'api_key|elevenlabs' || true; "
           "sudo -n true && echo SUDO_OK; "
           "curl -s -m 3 -o /dev/null -w 'API=%{http_code}\\n' http://127.0.0.1:8080/healthz || echo API=blocked; "
           "cat /data/secrets/fernet.key 2>&1 | head -c 200; echo; ls -ld /data/studio.db; python -c 'import luma_engine; print(\"ENGINE\" + \"_OK\")'\n")

    async def go():
        async with websockets.connect(f"{BASE.replace('http', 'ws')}/ws/terminal/{pid}", additional_headers={"Cookie": cookie, "Origin": BASE}) as ws:
            await ws.send(json.dumps({"type": "takeover", "on": True}))
            await ws.send(json.dumps({"type": "input", "data": cmd}))
            out, t0 = "", time.time()
            while "ENGINE_OK" not in out and time.time() - t0 < 30:
                m = json.loads(await asyncio.wait_for(ws.recv(), 30))
                if m["type"] == "output":
                    out += m["data"]
            return out

    out = asyncio.run(go())
    assert "USER=luma" in out
    assert "\n0\r\n" in out or "\n0\n" in out  # no key-like variables in the sandbox env
    assert "SUDO_OK" in out  # passwordless sudo inside the container
    assert "API=blocked" in out or "API=000" in out  # the agent cannot call Luma's own API
    assert "Permission denied" in out  # secrets are not readable by the sandbox user
    assert "ENGINE_OK" in out
