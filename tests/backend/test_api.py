"""HTTP API: projects, uploads (limits, magic bytes, sanitising), files, security, SSE."""
import io
import json
import threading
import time

import httpx
import pytest
from conftest import events, wait_for


def png_bytes(color=(255, 0, 0)) -> bytes:
    from PIL import Image

    b = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(b, "PNG")
    return b.getvalue()


def test_healthz_and_settings(client):
    assert client.get("/healthz").json()["status"] == "ok"
    s = client.get("/api/settings").json()
    assert s["llm"]["configured"] is False and s["limits"]["max_files"] == 20
    assert "key_masked" in s["llm"] and s["llm"]["key_masked"] == ""


def test_project_crud_and_validation(client):
    p = client.post("/api/projects", json={"name": "A", "settings": {"width": 1080, "height": 1920, "fps": 30, "duration": 12}}).json()
    assert p["settings"]["height"] == 1920
    assert client.post("/api/projects", json={"name": "B", "settings": {"width": 1000, "height": 1000, "fps": 30, "duration": 5}}).status_code == 422
    assert client.post("/api/projects", json={"name": "B", "settings": {"width": 1920, "height": 1080, "fps": 25, "duration": 5}}).status_code == 422
    assert client.post("/api/projects", json={"name": "B", "settings": {"width": 1920, "height": 1080, "fps": 24, "duration": 200}}).status_code == 422
    r = client.patch(f"/api/projects/{p['id']}", json={"name": "Renamed", "brief": "hello"})
    assert r.json()["name"] == "Renamed"
    d = client.post(f"/api/projects/{p['id']}/duplicate").json()
    assert d["name"] == "Renamed (copy)" and d["brief"] == "hello"
    assert client.delete(f"/api/projects/{d['id']}").status_code == 200
    assert client.get(f"/api/projects/{d['id']}").status_code == 404


def test_twenty_first_upload_is_rejected(client, project):
    pid = project["id"]
    files = [("files", (f"img{i}.png", png_bytes((i * 10, 0, 0)), "image/png")) for i in range(20)]
    r = client.post(f"/api/projects/{pid}/assets", files=files)
    assert r.status_code == 201, r.text
    assert len(client.get(f"/api/projects/{pid}/assets").json()) == 20
    r = client.post(f"/api/projects/{pid}/assets", files=[("files", ("one-more.png", png_bytes(), "image/png"))])
    assert r.status_code == 409 and "at most 20" in r.json()["detail"]
    assert len(client.get(f"/api/projects/{pid}/assets").json()) == 20


def test_upload_validation_by_magic_bytes_and_size(client, project):
    pid = project["id"]
    r = client.post(f"/api/projects/{pid}/assets", files=[("files", ("fake.png", b"#!/bin/sh\necho pwned\n", "image/png"))])
    assert r.status_code == 415
    big = b"\x89PNG\r\n\x1a\n" + b"0" * (25 * 1024 * 1024 + 10)
    r = client.post(f"/api/projects/{pid}/assets", files=[("files", ("big.png", big, "image/png"))])
    assert r.status_code == 413
    # extension lies, content is SVG → accepted as svg and sanitised
    evil = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><script>alert(2)</script><rect width="10" height="10" fill="#FF0000" onclick="x()"/></svg>'
    r = client.post(f"/api/projects/{pid}/assets", files=[("files", ("logo.jpg", evil, "image/jpeg"))])
    assert r.status_code == 201, r.text
    a = r.json()[0]
    assert a["kind"] == "svg" and a["filename"].endswith(".svg")
    stored = client.get(a["url"]).text
    assert "script" not in stored and "onload" not in stored and "onclick" not in stored and "#FF0000" in stored
    assert a["analysis"]["shape_count"] == 1 and a["thumb_url"]


def test_file_serving_blocks_traversal(client, project, server):
    pid = project["id"]
    (server["data"] / "secret.txt").write_text("top secret")
    assert client.get(f"/api/files/{pid}/../secret.txt").status_code in (403, 404)
    assert client.get(f"/api/files/{pid}/%2e%2e/%2e%2e/secret.txt").status_code in (403, 404)
    assert client.get(f"/api/files/{pid}/..%2f..%2fsecret.txt").status_code in (403, 404)
    assert client.get(f"/api/files/{pid}/assets/nope.png").status_code == 404


def test_csrf_host_and_size_guards(server):
    base = server["url"]
    assert httpx.post(f"{base}/api/projects", json={"name": "x"}).status_code == 403  # no X-Luma-Client
    assert httpx.post(f"{base}/api/projects", json={"name": "x"}, headers={"X-Luma-Client": "1", "Origin": "https://evil.example"}).status_code == 403
    assert httpx.get(f"{base}/api/projects", headers={"Host": "attacker.example"}).status_code == 421
    # declared body larger than the limit → refused before it is read (raw socket: httpx won't lie)
    import socket

    s = socket.create_connection(("127.0.0.1", server["port"]))
    s.sendall(f"POST /api/projects HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Luma-Client: 1\r\nContent-Type: application/json\r\n"
              f"Content-Length: {40 * 1024 * 1024}\r\n\r\n{{}}".encode())
    s.settimeout(10)
    status_line = s.recv(200).split(b"\r\n")[0]
    s.close()
    assert b" 413 " in status_line, status_line


def test_sse_replay_and_resume(client, project):
    from app.agent.runner import runs
    from app.events import bus

    r = runs.create(project["id"], "agent")
    ids = [bus.publish(r.id, "text_delta", {"delta": f"part{i} ", "turn": 1})["id"] for i in range(5)]
    evs = events(client, r.id)
    assert [e["id"] for e in evs] == ids  # full replay, ordered
    assert all(e["type"] == "text_delta" for e in evs)
    # resume with Last-Event-ID: only newer events, then live ones
    got = []

    def reader():
        with client.stream("GET", f"/api/runs/{r.id}/events", headers={"Last-Event-ID": str(ids[2])}, timeout=httpx.Timeout(5.0)) as resp:
            buf = ""
            for chunk in resp.iter_text():
                buf += chunk
                while "\n\n" in buf:
                    block, buf = buf.split("\n\n", 1)
                    for line in block.splitlines():
                        if line.startswith("data: "):
                            got.append(json.loads(line[6:])["id"])
                if len(got) >= 3:
                    return

    th = threading.Thread(target=reader)
    th.start()
    time.sleep(0.8)
    live = bus.publish(r.id, "run_status", {"status": "idle"})["id"]
    th.join(10)
    assert got == [ids[3], ids[4], live]
    assert ids == sorted(ids) and live > ids[-1]  # monotonically increasing


def test_demo_run_renders_small_mp4(client, server):
    from app import db

    db.set_setting("demo_overrides", {"width": 320, "height": 180, "fps": 24, "duration": 3})
    try:
        r = client.post("/api/demo").json()
        rid = r["run"]["id"]
        wait_for(lambda: client.get(f"/api/runs/{rid}").json()["status"] in ("completed", "failed"), timeout=240, interval=1)
        run = client.get(f"/api/runs/{rid}").json()
        assert run["status"] == "completed", run
        files = client.get(f"/api/projects/{r['project']['id']}/files", params={"dir": "outputs"}).json()
        names = {f["path"] for f in files}
        assert "outputs/luma_demo.mp4" in names and "outputs/qc_report.json" in names
        qc = client.get(f"/api/files/{r['project']['id']}/outputs/qc_report.json").json()
        assert qc["pass"], [c for c in qc["checks"] if not c["pass"]]
        types = {e["type"] for e in events(client, rid)}
        assert {"progress", "artifact", "audio", "tool_result", "run_status"} <= types
    finally:
        db.set_setting("demo_overrides", None)


@pytest.mark.parametrize("path", ["/", "/some/deep/link"])
def test_spa_served(client, path):
    r = client.get(path)
    assert r.status_code in (200, 404)  # 404 only when the frontend has not been built
    if r.status_code == 200:
        assert "<div id=\"root\">" in r.text
