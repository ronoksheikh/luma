"""Definition of done #2 against the container: a mocked-director run shows live todos → storyboard →
approval → render progress, SURVIVES `docker restart` mid-render, resumes (the render is re-queued and
keeps its finished frames) and goes on to present the video + files, pass QC and finish.

    docker compose up -d --build
    LUMA_E2E_URL=http://127.0.0.1:8080 pytest -m e2e tests/e2e/test_resume_e2e.py -v
"""
import json
import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.e2e
BASE = os.environ.get("LUMA_E2E_URL", "http://127.0.0.1:8080")
CONTAINER = os.environ.get("LUMA_E2E_CONTAINER", "luma-studio-luma-1")
DATA = Path(os.environ.get("LUMA_E2E_DATA", Path(__file__).resolve().parents[2] / "data"))
ROOT = Path(__file__).resolve().parents[2]
PORT = 9911


def sh(*args, check=True, **kw):
    return subprocess.run(list(args), check=check, capture_output=True, text=True, **kw)


def start_mock(part: str):
    sh("docker", "exec", CONTAINER, "mkdir", "-p", "/tmp/lumamock")
    for f in ("tests/mock_llm.py", "tests/mock_director.py", "tests/e2e/mock_in_container.py"):
        sh("docker", "cp", str(ROOT / f), f"{CONTAINER}:/tmp/lumamock/")
    sh("docker", "exec", CONTAINER, "chmod", "-R", "a+rX", "/tmp/lumamock")
    sh("docker", "exec", "-d", "-u", "studio", CONTAINER, "/opt/venv/bin/python", "/tmp/lumamock/mock_in_container.py", part, str(PORT))
    for _ in range(60):
        r = sh("docker", "exec", CONTAINER, "curl", "-s", f"http://127.0.0.1:{PORT}/v1/models", check=False)
        if "mock-director" in r.stdout:
            return
        time.sleep(0.5)
    raise AssertionError("mock director did not start in the container")


def wait(fn, timeout, interval=1.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(interval)
    raise AssertionError("timeout")


def healthy():
    r = sh("docker", "inspect", "-f", "{{.State.Health.Status}}", CONTAINER, check=False)
    return r.stdout.strip() == "healthy"


def test_restart_mid_render_and_resume():
    c = httpx.Client(base_url=BASE, headers={"X-Luma-Client": "1"}, timeout=60)
    user = {"username": f"resume_{int(time.time())}", "password": "resume password 1"}
    assert c.post("/api/auth/signup", json=user).status_code == 201
    p = c.post("/api/projects", json={"name": "Resume e2e"}).json()
    c.patch(f"/api/projects/{p['id']}", json={"settings": {**p["settings"], "width": 1080, "height": 1080, "fps": 24, "duration": 3}})
    with open(ROOT / "samples/veyra-symbol.svg", "rb") as f:
        c.post(f"/api/projects/{p['id']}/assets", files={"files": ("veyra-symbol.svg", f, "image/svg+xml")})
    url = f"http://127.0.0.1:{PORT}/v1"
    c.post("/api/settings/remember", json={"llm_api_key": "sk-mock-e2e-0123456789", "llm_base_url": url, "llm_model": "mock-director"})
    start_mock("head")
    rid = c.post(f"/api/projects/{p['id']}/runs", json={"text": "Make a 3 s square outro"}).json()["id"]

    # storyboard → approval (answered like the user would)
    q = wait(lambda: next(iter(c.get(f"/api/runs/{rid}/requests", params={"status": "pending"}).json()), None), 180)
    assert q["kind"] == "approval" and q["payload"]["artifacts"][0]["type"] == "storyboard"
    c.post(f"/api/runs/{rid}/requests/{q['id']}/answer", json={"choice": "Approve"})

    # render running with some frames on disk → restart the container
    frames = DATA / "projects" / p["id"] / "renders" / "final" / "frames"
    wait(lambda: frames.exists() and len(list(frames.glob("frame_*.png"))) >= 8, 600, 0.5)
    early = sorted(frames.glob("frame_*.png"))[:5]
    mtimes = {f.name: f.stat().st_mtime for f in early}
    plan_before = c.get(f"/api/runs/{rid}/plan").json()["progress"]
    sh("docker", "restart", "-t", "5", CONTAINER)
    wait(healthy, 180)
    c = httpx.Client(base_url=BASE, headers={"X-Luma-Client": "1"}, timeout=60, cookies=c.cookies)
    run = c.get(f"/api/runs/{rid}").json()
    assert run["status"] == "interrupted", run
    jobs = {j["name"]: j for j in c.get(f"/api/projects/{p['id']}/jobs").json()}
    assert jobs["render-final"]["status"] == "lost"

    start_mock("tail")
    r = c.post(f"/api/runs/{rid}/resume")
    assert r.status_code == 200, r.text
    assert r.json()["jobs_requeued"] == ["render-final"] and r.json()["closed_calls"]
    status = wait(lambda: (lambda s: s if s in ("completed", "failed", "idle", "stopped") else None)(c.get(f"/api/runs/{rid}").json()["status"]), 900, 2)
    assert status == "completed", [e for e in _events(c, rid) if e["type"] == "error"]

    # finished frames were kept (resume skips them), the film is complete and QC passes
    for name, mt in mtimes.items():
        assert (frames / name).stat().st_mtime == mt, f"{name} was re-rendered"
    out = DATA / "projects" / p["id"] / "outputs"
    probe = json.loads(sh("ffprobe", "-v", "error", "-count_frames", "-show_streams", "-of", "json", str(out / "veyra_outro.mp4")).stdout)
    v = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert int(v["nb_read_frames"]) == 72 and (v["width"], v["height"]) == (1080, 1080)
    assert json.loads((out / "qc_veyra_outro.json").read_text())["pass"]
    evs = _events(c, rid)
    types = [e["type"] for e in evs]
    for t in ("todo_update", "present", "approval_request", "approval_result", "progress", "checkpoint", "system_note", "notify"):
        assert t in types, t
    video = next(e["data"]["artifact"] for e in evs if e["type"] == "present" and e["data"]["card"] == "video")
    assert [ch["label"] for ch in video["meta"]["chapters"]] == ["Seed", "Unfold", "Lockup"]
    assert c.get(f"/api/runs/{rid}/plan").json()["progress"]["done"] == 4 and plan_before["done"] < 4


def _events(c, rid):
    out = []
    try:
        with c.stream("GET", f"/api/runs/{rid}/events", timeout=httpx.Timeout(3.0)) as r:
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
