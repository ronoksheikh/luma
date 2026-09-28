"""Accounts, sessions and per-user isolation."""
import asyncio
import json

import httpx
import pytest
from conftest import cookie_header, login_client


def test_signup_login_logout_and_errors(server):
    base = server["url"]
    with httpx.Client(base_url=base, headers={"X-Luma-Client": "1"}) as c:
        st = c.get("/api/auth/state").json()
        assert st["signup_open"] and st["user"] is None
        assert c.get("/api/projects").status_code == 401
        assert c.post("/api/auth/signup", json={"username": "ab", "password": "longenough1"}).status_code == 422
        assert c.post("/api/auth/signup", json={"username": "alice", "password": "short"}).status_code == 422
        r = c.post("/api/auth/signup", json={"username": "alice", "password": "alice-password-1"})
        assert r.status_code == 201 and r.json()["user"]["username"] == "alice"
        cookie = r.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=lax" in cookie
        assert c.get("/api/auth/state").json()["user"]["username"] == "alice"
        assert c.post("/api/auth/signup", json={"username": "ALICE", "password": "whatever-123"}).status_code == 409
        assert c.post("/api/auth/logout").status_code == 200
        assert c.get("/api/projects").status_code == 401
        assert c.post("/api/auth/login", json={"username": "alice", "password": "wrong-password"}).status_code == 401
        assert c.post("/api/auth/login", json={"username": "Alice", "password": "alice-password-1"}).status_code == 200
        assert c.get("/api/projects").status_code == 200
    raw = (server["data"] / "studio.db").read_bytes()
    assert b"alice-password-1" not in raw  # only scrypt hashes are stored


def test_users_are_isolated(server):
    a = login_client(server, "owner1", "owner1-password")
    b = login_client(server, "intruder", "intruder-password")
    p = a.post("/api/projects", json={"name": "secret project"}).json()
    a.post(f"/api/projects/{p['id']}/assets", files=[("files", ("l.svg", b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="5" height="5"/></svg>', "image/svg+xml"))])
    assert p["id"] not in [x["id"] for x in b.get("/api/projects").json()]
    assert b.get(f"/api/projects/{p['id']}").status_code == 404
    assert b.get(f"/api/projects/{p['id']}/assets").status_code == 404
    assert b.get(f"/api/files/{p['id']}/assets/l.svg").status_code == 404
    assert a.get(f"/api/files/{p['id']}/assets/l.svg").status_code == 200
    assert b.delete(f"/api/projects/{p['id']}").status_code == 404
    from app.agent.runner import runs

    r = runs.create(p["id"], "agent")
    assert b.get(f"/api/runs/{r.id}").status_code == 404
    assert b.post(f"/api/runs/{r.id}/cancel").status_code == 404
    assert a.get(f"/api/runs/{r.id}").status_code == 200

    import websockets

    async def ws(client):
        async with websockets.connect(f"ws://127.0.0.1:{server['port']}/ws/terminal/{p['id']}", additional_headers=cookie_header(client)) as w:
            return json.loads(await asyncio.wait_for(w.recv(), 10))

    with pytest.raises(Exception):
        asyncio.run(ws(b))
    assert asyncio.run(ws(a))["type"] == "output"
    a.close()
    b.close()


def test_saved_keys_follow_the_user_to_another_browser(server):
    first = login_client(server, "roamer", "roamer-password")
    first.post("/api/settings/remember", json={"llm_api_key": "sk-roaming-key-0123456789abcdef", "llm_base_url": "http://example.invalid/v1", "llm_model": "m1"})
    first.close()
    second = login_client(server, "roamer", "roamer-password")  # a fresh browser: no local storage
    s = second.get("/api/settings").json()
    assert s["llm"]["configured"] and s["llm"]["model"] == "m1" and s["llm"]["key_source"] == "server"
    assert "sk-roaming-key" not in json.dumps(s)
    other = login_client(server, "someone-else", "someone-password")
    assert other.get("/api/settings").json()["llm"]["key_source"] == "none"  # per user
    second.close()
    other.close()


def test_change_password_signs_out_everywhere(server):
    c = login_client(server, "changer", "old-password-1")
    assert c.post("/api/auth/password", json={"current_password": "nope-nope", "new_password": "new-password-1"}).status_code == 401
    assert c.post("/api/auth/password", json={"current_password": "old-password-1", "new_password": "new-password-1"}).status_code == 200
    assert c.get("/api/projects").status_code == 401
    c.close()
    login_client(server, "changer", "new-password-1").close()
