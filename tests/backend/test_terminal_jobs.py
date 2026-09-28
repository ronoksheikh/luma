"""Persistent terminal, detached jobs, redaction."""
import asyncio
import json
import os
import time
from pathlib import Path

import pytest
from conftest import wait_for


@pytest.fixture()
def pdir(server, project):
    from app.routes_projects import ensure_workspace

    return ensure_workspace(project["id"])


def _ctx(project_id, pdir, run_id="r_test"):
    from app.agent.tools.base import ToolContext
    from app.secrets_store import Credentials

    return ToolContext(run_id, project_id, pdir, Credentials(), {"project": {}}, False, None, tool_call_id="tc_test")


def test_terminal_state_persists_across_calls(pdir, project):
    from app.terminal import TerminalSession

    async def go():
        t = TerminalSession(project["id"] + "x", str(pdir))
        t.start()
        try:
            r1 = await t.run("mkdir -p /tmp/luma_t && cd /tmp/luma_t && export LUMA_T=42")
            r2 = await t.run("pwd; echo $LUMA_T")
            r3 = await t.run("exit_code_test() { return 3; }; exit_code_test")
            r4 = await t.run("sleep 20", timeout=1)
            r5 = await t.run("echo still-alive; pwd")
            return r1, r2, r3, r4, r5
        finally:
            t.close()

    r1, r2, r3, r4, r5 = asyncio.run(go())
    assert r1.exit_code == 0
    assert r2.output.splitlines() == ["/tmp/luma_t", "42"]
    assert r3.exit_code == 3
    assert r4.timed_out and r4.seconds < 8
    assert r5.output.splitlines() == ["still-alive", "/tmp/luma_t"]  # shell survived the Ctrl-C


def test_terminal_tools_and_env_is_scrubbed(pdir, project, monkeypatch):
    from app.agent.tools import available_tools
    from app.secrets_store import register_secret

    monkeypatch.setenv("LLM_API_KEY", "sk-should-never-leak-0123456789abcdef")
    register_secret("sk-should-never-leak-0123456789abcdef")
    T = available_tools(False)
    ctx = _ctx(project["id"], pdir)

    async def go():
        a = await T["terminal_run"].handler(ctx, {"command": "cd work && pwd"})
        b = await T["terminal_run"].handler(ctx, {"command": "pwd && env | grep -c -i -E 'api_key|sk-should' || true"})
        c = await T["terminal_run"].handler(ctx, {"command": "echo sk-should-never-leak-0123456789abcdef"})
        return a, b, c

    a, b, c = asyncio.run(go())
    assert a.content.strip().endswith("/work")
    lines = b.content.splitlines()
    assert lines[1].endswith("/work") and lines[2] == "0"  # cwd persisted; no secrets in the env
    assert "sk-should-never-leak" not in c.content and "[REDACTED]" in c.content
    from app.terminal import terminals

    assert "sk-should-never-leak" not in terminals.get(project["id"]).scrollback


def test_spawned_job_survives_tool_call_and_can_be_killed(pdir, project):
    from app.agent.tools import available_tools
    from app.jobs import _alive, jobs

    T = available_tools(False)
    ctx = _ctx(project["id"], pdir)

    async def spawn():
        return await T["terminal_spawn"].handler(ctx, {"name": "sleeper", "command": "echo started; sleep 60"})

    out = asyncio.run(spawn())
    info = json.loads(out.content)
    assert info["status"] == "running" and info["pid"]
    time.sleep(1.5)  # the tool call is long over
    j = jobs.get(project["id"], "sleeper")
    assert j.status == "running" and _alive(j.pid)
    assert "started" in jobs.poll(project["id"], "sleeper")["log_tail"]
    killed = asyncio.run(T["terminal_kill"].handler(ctx, {"name": "sleeper"}))
    assert json.loads(killed.content)["status"] == "killed"
    wait_for(lambda: not _alive(j.pid), timeout=10)


def test_job_progress_lines_and_completion(pdir, project):
    from app.jobs import jobs

    script = 'for i in 1 2 3; do echo "{\\"type\\": \\"progress\\", \\"frame\\": $i, \\"total\\": 3}"; sleep 0.2; done; echo finished'
    j = jobs.spawn(project["id"], "prog", script, run_id=None, cwd=str(pdir))
    wait_for(lambda: jobs.by_id(j["id"]).status in ("done", "failed"), timeout=20)
    done = jobs.by_id(j["id"])
    assert done.status == "done" and done.exit_code == 0
    assert done.progress["frame"] == 3 and done.progress["total"] == 3
    assert "finished" in jobs.poll(project["id"], "prog")["log_tail"]


def test_job_concurrency_queue(pdir, project):
    from app import db
    from app.jobs import jobs

    db.set_setting("job_concurrency", 1)
    a = jobs.spawn(project["id"], "q-a", "sleep 1.5", cwd=str(pdir))
    b = jobs.spawn(project["id"], "q-b", "echo b", cwd=str(pdir))
    assert jobs.by_id(b["id"]).status == "queued"
    wait_for(lambda: jobs.by_id(b["id"]).status == "done", timeout=20)
    assert jobs.by_id(a["id"]).finished_at <= jobs.by_id(b["id"]).started_at + 0.5


def test_redaction_patterns_and_registered_secrets():
    from app.secrets_store import redact, redact_obj, register_secret

    register_secret("my-custom-secret-value-123")
    s = redact("key sk-or-v1-abcdefabcdefabcdefabcdefabcdef and my-custom-secret-value-123 and xi-api-key: sk_" + "a" * 48)
    assert "abcdefabcdef" not in s and "my-custom-secret" not in s and "a" * 48 not in s
    assert redact_obj({"a": ["Bearer", "Authorization: Bearer abcdefghijklmnopqrstuvwxyz"]})["a"][1].endswith("[REDACTED]")
    assert redact("nothing secret here") == "nothing secret here"


def test_encrypted_remember_roundtrip(client, server):
    r = client.post("/api/settings/remember", json={"llm_api_key": "sk-remember-me-0123456789abcdef", "llm_base_url": "http://x/v1", "llm_model": "m"})
    assert r.json()["remembered"]["llm_api_key"] is True
    raw = (server["data"] / "studio.db").read_bytes()
    assert b"sk-remember-me" not in raw  # encrypted at rest
    s = client.get("/api/settings").json()
    assert s["llm"]["key_source"] == "server" and "sk-remember-me" not in json.dumps(s)
    key = server["data"] / "secrets" / "fernet.key"
    assert key.exists() and oct(key.stat().st_mode & 0o777) == "0o600"
    client.delete("/api/settings/remember")
    assert client.get("/api/settings").json()["llm"]["key_source"] == "none"


def test_terminal_websocket_mirror_and_takeover(server, project):
    import websockets

    async def go():
        async with websockets.connect(f"ws://127.0.0.1:{server['port']}/ws/terminal/{project['id']}") as ws:
            await ws.send(json.dumps({"type": "input", "data": "echo not-allowed-yet\n"}))  # ignored without take-over
            await ws.send(json.dumps({"type": "takeover", "on": True}))
            await ws.send(json.dumps({"type": "input", "data": "echo taken-over-$((6*7))\n"}))
            out = ""
            t0 = time.time()
            while "taken-over-42" not in out and time.time() - t0 < 10:
                m = json.loads(await asyncio.wait_for(ws.recv(), 10))
                if m["type"] == "output":
                    out += m["data"]
            return out

    out = asyncio.run(go())
    assert "taken-over-42" in out and "not-allowed-yet" not in out

    async def evil():
        async with websockets.connect(f"ws://127.0.0.1:{server['port']}/ws/terminal/{project['id']}",
                                      additional_headers={"Origin": "https://evil.example"}) as ws:
            await ws.recv()

    with pytest.raises(Exception):
        asyncio.run(evil())


def test_sandbox_env_has_no_secrets(monkeypatch):
    from app.terminal import sandbox_env

    monkeypatch.setenv("LLM_API_KEY", "sk-live-secret")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-secret")
    env = sandbox_env("/tmp")
    assert not any("secret" in v for v in env.values())
    assert set(env) >= {"PATH", "HOME", "LUMA_WORKSPACE"}
    assert Path(os.path.dirname(os.sys.executable)).as_posix() in env["PATH"]
