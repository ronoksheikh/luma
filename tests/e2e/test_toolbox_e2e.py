"""Toolbox in the container:

* the seeded global tools are tested and registered at boot and the seeded skills are indexed;
* a user-authored tool with a real pinned dependency (six==1.16.0) is installed into the persistent /data/venv and
  recorded in /data/toolbox/requirements.lock;
* network per manifest is enforced by the kernel: a `network: false` tool runs with the primary group `lumanonet`,
  whose sockets the firewall rejects (even `curl` from a subprocess, which the in-process guard cannot see);
  with `network: true` AND the Settings toggle on, the same request goes through;
* tools, the venv and skills survive re-creating the container from a rebuilt image (`docker compose up -d --build
  --force-recreate`): the image is immutable, /data persists.

    docker compose up -d --build
    LUMA_E2E_URL=http://127.0.0.1:8080 pytest -m e2e tests/e2e/test_toolbox_e2e.py -v
    (set LUMA_E2E_BASE_IMAGE=luma-base-ubuntu:24.04 to rebuild on the fallback base)
"""
import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.e2e
BASE = os.environ.get("LUMA_E2E_URL", "http://127.0.0.1:8080")
CONTAINER = os.environ.get("LUMA_E2E_CONTAINER", "luma-studio-luma-1")
ROOT = Path(__file__).resolve().parents[2]
H = {"X-Luma-Client": "1"}
USER = {"username": "toolbox_e2e", "password": "toolbox e2e password"}
HTTP_PORT = 8765

MAIN = '''import subprocess

import six


def run(params, ctx):
    r = subprocess.run(["curl", "-s", "-m", "5", "-o", "/dev/null", "-w", "%{http_code}", params["url"]], capture_output=True, text=True)
    ctx.log(f"curl exit {r.returncode}")
    return {"six": six.__version__, "http": r.stdout.strip() or "000", "curl_rc": r.returncode}
'''
TEST = '''import six
from main import run

from luma_engine.toolkit import make_test_ctx


def test_dependency_and_result_shape(tmp_path):
    out = run({"url": "http://127.0.0.1:9/"}, make_test_ctx(tmp_path))
    assert out["six"] == six.__version__ == "1.16.0" and out["http"] == "000"
'''
MANIFEST = {"description": "Fetches a URL with curl and reports the HTTP status; used to prove per-manifest network isolation.",
            "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
            "returns": {"type": "object", "properties": {"six": {"type": "string"}, "http": {"type": "string"}}, "required": ["six", "http"]},
            "dependencies": ["six==1.16.0"], "network": False, "timeout_s": 60, "tags": ["e2e", "network"]}
README = "# net_probe\n\nFetches a URL with curl (what) to prove network isolation per manifest (why), via a subprocess (how).\n\nLimitations: e2e only.\n"
SKILL = ("---\nname: e2e_persistence_check\ndescription: Checking that the toolbox survives container rebuilds (end-to-end test skill).\n"
         "tags: [e2e]\n---\n## When to use\nAfter upgrading the image.\n\n## Steps\n1. Rebuild and recreate the container.\n2. List tools and skills.\n\n"
         "## Pitfalls\n- Anything installed outside /data is gone after a rebuild.\n\n## Verification\n- Tools, venv packages and skills are still there.\n\n"
         "## Example\nnet_probe still imports six after `docker compose up -d --build --force-recreate`.\n")


def sh(*args, check=True, **kw):
    return subprocess.run(list(args), capture_output=True, text=True, check=check, **kw)


def wait(fn, timeout, interval=2.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            v = fn()
        except (httpx.HTTPError, ValueError):
            v = None
        if v:
            return v
        time.sleep(interval)
    raise AssertionError("timed out")


def client() -> httpx.Client:
    c = httpx.Client(base_url=BASE, headers=H, timeout=300)
    r = c.post("/api/auth/login", json=USER)
    if r.status_code == 401:
        r = c.post("/api/auth/signup", json=USER)
    assert r.status_code in (200, 201), r.text
    return c


def booted(c):
    s = c.get("/api/toolbox/summary").json()
    return s if s["boot"].get("done") and s["venv"]["status"] in ("ok", "degraded") else None


def start_http():
    sh("docker", "exec", "-d", "-u", "studio", CONTAINER, "/opt/venv/bin/python", "-m", "http.server", str(HTTP_PORT), "--bind", "127.0.0.1",
       "--directory", "/tmp")
    wait(lambda: sh("docker", "exec", CONTAINER, "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"http://127.0.0.1:{HTTP_PORT}/",
                    check=False).stdout == "200", 30, 0.5)


def test_toolbox_in_the_container_and_across_a_rebuild():
    c = client()
    s = wait(lambda: booted(c), 600, 3)
    tools = {t["name"]: t for t in c.get("/api/toolbox/tools", params={"scope": "global"}).json()}
    seeds = {"svg_split_exact", "lockup_fit_reference", "board_residual_extract", "audio_event_spectrogram", "reframe_safe_areas",
             "palette_from_image", "final_frame_exactness"}
    assert seeds <= set(tools) and all(tools[n]["status"] == "enabled" for n in seeds), {n: tools[n]["status"] for n in seeds if n in tools}
    assert s["skills"] >= 7
    p = c.post("/api/projects", json={"name": "Toolbox e2e"}).json()
    pid = p["id"]
    body = {"scope": "project", "project_id": pid, "name": "net_probe", "manifest": MANIFEST, "main_py": MAIN, "test_py": TEST, "readme": README}
    r = c.post("/api/toolbox/tools", json=body)
    assert r.status_code == 201, r.text
    res = r.json()
    assert res["deps"]["ok"], res["deps"]["output"]
    assert res["test"]["status"] == "passed", res["test"]["output"]
    assert c.post("/api/toolbox/tools/project/net_probe/enable", params={"project_id": pid}).status_code == 200
    lock = sh("docker", "exec", CONTAINER, "cat", "/data/toolbox/requirements.lock").stdout
    assert "six==1.16.0" in lock
    # the tool runs as the sandbox user, not the backend
    who = sh("docker", "exec", CONTAINER, "stat", "-c", "%U", "/data/venv/bin/python").stdout.strip()
    assert who == "luma"
    # --- network: false → the kernel firewall rejects even a curl subprocess
    start_http()
    url = f"http://127.0.0.1:{HTTP_PORT}/"
    out = c.post("/api/toolbox/tools/project/net_probe/try", json={"project_id": pid, "params": {"url": url}}).json()
    assert out["ok"], out
    assert out["result"]["http"] == "000" and out["result"]["curl_rc"] != 0 and out["result"]["six"] == "1.16.0"
    # --- network: true in the manifest but the Settings toggle off → still no network
    c.put("/api/toolbox/tools/project/net_probe", params={"project_id": pid}, json={"manifest": {"network": True}, "reason": "needs to fetch"})
    c.post("/api/toolbox/tools/project/net_probe/enable", params={"project_id": pid})
    c.put("/api/settings", json={"toolbox_network": False})
    out = c.post("/api/toolbox/tools/project/net_probe/try", json={"project_id": pid, "params": {"url": url}}).json()
    assert out["result"]["http"] == "000", out
    # --- manifest AND toggle → allowed
    c.put("/api/settings", json={"toolbox_network": True})
    out = c.post("/api/toolbox/tools/project/net_probe/try", json={"project_id": pid, "params": {"url": url}}).json()
    assert out["result"]["http"] == "200", out
    c.put("/api/settings", json={"toolbox_network": False})
    assert c.put("/api/toolbox/skills/e2e_persistence_check", json={"content": SKILL}).status_code == 200
    c.close()

    # --- rebuild + recreate the container: the image is immutable, /data persists
    base = os.environ.get("LUMA_E2E_BASE_IMAGE")  # e.g. luma-base-ubuntu:24.04 where Debian mirrors are blocked
    sh("docker", "compose", "build", *(["--build-arg", f"BASE_IMAGE={base}"] if base else []), cwd=str(ROOT), timeout=3600)
    sh("docker", "compose", "up", "-d", "--force-recreate", cwd=str(ROOT), timeout=600)
    wait(lambda: sh("docker", "inspect", "-f", "{{.State.Health.Status}}", CONTAINER, check=False).stdout.strip() == "healthy", 600, 3)
    c = client()
    s = wait(lambda: booted(c), 600, 3)
    assert s["venv"]["status"] == "ok" and not s["venv"]["missing"], s["venv"]
    assert any("matches requirements.lock" in line for line in s["venv"]["log"]), s["venv"]["log"]
    t = c.get("/api/toolbox/tools/project/net_probe", params={"project_id": pid}).json()
    assert t["status"] == "enabled" and t["version"] == "1.0.1"
    out = c.post("/api/toolbox/tools/project/net_probe/try", json={"project_id": pid, "params": {"url": "http://127.0.0.1:9/"}}).json()
    assert out["ok"] and out["result"]["six"] == "1.16.0", out
    assert "e2e_persistence_check" in {k["name"] for k in c.get("/api/toolbox/skills").json()}
    tools = {x["name"]: x for x in c.get("/api/toolbox/tools", params={"scope": "global"}).json()}
    assert all(tools[n]["status"] == "enabled" for n in seeds)
    hist = c.get("/api/toolbox/tools/project/net_probe/history", params={"project_id": pid}).json()
    assert {v for h in hist for v in h["versions"]} >= {"1.0.0", "1.0.1"}
    c.close()
