"""The agent loop against a mocked OpenAI-compatible server."""
import json

import httpx
from conftest import events, run_status, wait_for
from mock_llm import MockServer, call


def headers(mock: MockServer, key: str = "sk-test-agentkey-0123456789abcdef"):
    return {"X-LLM-Base-Url": mock.url, "X-LLM-Model": "mock-director", "X-LLM-Api-Key": key}


def start(client, project, mock, text="make a video", key="sk-test-agentkey-0123456789abcdef"):
    r = client.post(f"/api/projects/{project['id']}/runs", json={"text": text}, headers=headers(mock, key))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def finished(client, rid, states=("completed", "idle", "failed", "stopped", "cancelled")):
    return wait_for(lambda: run_status(client, rid) in states and run_status(client, rid), timeout=90)


def test_streamed_parallel_and_malformed_tool_calls(client, project, server):
    script = [
        {"reasoning": "thinking about the brand", "text": "Looking around first.",
         "tool_calls": [call("list_files", path="."), call("write_file", path="work/a.txt", content="hello"), call("terminal_run", command="echo par")]},
        {"text": "Next.", "tool_calls": [{"name": "write_file", "arguments": '{"path": "work/b.txt", "content": "unterminated'}]},
        {"tool_calls": [{"name": "write_file", "arguments": '```json\n{"path": "work/b.txt", "content": "fixed",}\n```'}]},
        {"tool_calls": [call("no_such_tool", x=1), call("read_file")]},
        {"text": "All done — here is what I did."},
    ]
    with MockServer(script) as mock:
        rid = start(client, project, mock)
        assert finished(client, rid) == "idle"
        reqs = mock.state["requests"]
    evs = events(client, rid)
    starts = [e for e in evs if e["type"] == "tool_call_start"]
    results = {e["data"]["id"]: e["data"] for e in evs if e["type"] == "tool_result"}
    assert len(starts) == 7
    by_name = [(s["data"]["name"], results[s["data"]["id"]]["status"]) for s in starts]
    assert by_name[:3] == [("list_files", "success"), ("write_file", "success"), ("terminal_run", "success")]
    assert by_name[3] == ("write_file", "error")  # malformed JSON → helpful error, loop continues
    assert "not valid JSON" in results[starts[3]["data"]["id"]]["output"]
    assert by_name[4] == ("write_file", "success")  # repaired (code fences + trailing comma)
    assert by_name[5] == ("no_such_tool", "error") and by_name[6] == ("read_file", "error")
    pdir = server["data"] / "projects" / project["id"]
    assert (pdir / "work/a.txt").read_text() == "hello" and (pdir / "work/b.txt").read_text() == "fixed"
    # streamed deltas were emitted and reassembled
    text = "".join(e["data"]["delta"] for e in evs if e["type"] == "text_delta" and e["data"].get("kind") != "reasoning")
    assert "Looking around first." in text and "All done" in text
    assert any(e["type"] == "text_delta" and e["data"].get("kind") == "reasoning" for e in evs)
    assert sum(1 for e in evs if e["type"] == "tool_args_delta") >= 7
    # the model received tool results for every parallel call, in order
    second = reqs[1]["messages"]
    tool_msgs = [m for m in second if m["role"] == "tool"]
    assert len(tool_msgs) == 3 and "exit_code: 0" in tool_msgs[2]["content"]
    assert reqs[0]["tools"] and reqs[0]["stream"] is True
    # pinned facts are in the system prompt
    assert "Pinned project facts" in reqs[0]["messages"][0]["content"]
    usage = [e for e in evs if e["type"] == "usage"][-1]["data"]
    assert usage["steps"] == 5 and usage["prompt_tokens"] == 5000


def test_follow_up_keeps_history_and_workspace(client, project):
    with MockServer([{"tool_calls": [call("terminal_run", command="cd work && export X=1")]}, {"text": "first done"},
                     {"tool_calls": [call("terminal_run", command="pwd && echo $X")]}, {"text": "second done"}]) as mock:
        rid = start(client, project, mock)
        finished(client, rid)
        client.post(f"/api/runs/{rid}/message", json={"text": "now change it"}, headers=headers(mock))
        wait_for(lambda: len(mock.state["requests"]) >= 4 and run_status(client, rid) == "idle", timeout=60)
        last = mock.state["requests"][-1]["messages"]
    contents = json.dumps(last)
    assert "make a video" in contents and "first done" in contents and "now change it" in contents  # full history
    tool_out = [m for m in last if m["role"] == "tool"][-1]["content"]
    assert tool_out.splitlines()[1].endswith("/work") and tool_out.splitlines()[2] == "1"


def test_ask_user_pauses_and_resumes(client, project):
    with MockServer([{"tool_calls": [call("ask_user", question="Fan or stroke?", options=["fan", "stroke"])]}, {"text": "ok"}]) as mock:
        rid = start(client, project, mock)
        wait_for(lambda: run_status(client, rid) == "waiting_input", timeout=30)
        ev = [e for e in events(client, rid) if e["type"] == "run_status"][-1]["data"]
        assert ev["question"] == "Fan or stroke?" and ev["options"] == ["fan", "stroke"]
        client.post(f"/api/runs/{rid}/message", json={"text": "fan"})
        finished(client, rid)
        tool = [m for m in mock.state["requests"][-1]["messages"] if m["role"] == "tool"][-1]
    assert tool["content"] == "The user answered: fan"


def test_retry_on_429_and_5xx_is_visible(client, project):
    with MockServer([{"status": 429, "error": "rate limited"}, {"status": 503}, {"text": "hi"}]) as mock:
        rid = start(client, project, mock)
        assert finished(client, rid) == "idle"
    retries = [e for e in events(client, rid) if e["type"] == "retry"]
    assert len(retries) == 2 and "429" in retries[0]["data"]["error"]


def test_model_without_tool_support_gets_clear_error(client, project):
    with MockServer([{"status": 400, "error": "This model does not support tools"}]) as mock:
        rid = start(client, project, mock)
        assert finished(client, rid) == "failed"
    errs = [e["data"] for e in events(client, rid) if e["type"] == "error"]
    assert errs and errs[0]["code"] == "no_tool_support" and "tool-capable" in errs[0]["message"]


def test_stop_cancels_stream_terminal_and_jobs(client, project):
    from app.jobs import jobs

    script = [{"tool_calls": [call("terminal_spawn", name="bg", command="sleep 120"), call("terminal_run", command="sleep 120", timeout_s=300)]}]
    with MockServer(script) as mock:
        rid = start(client, project, mock)
        wait_for(lambda: jobs.get(project["id"], "bg") is not None and jobs.get(project["id"], "bg").status == "running", timeout=30)
        r = client.post(f"/api/runs/{rid}/cancel").json()
        assert "bg" in r["jobs_killed"]
        assert finished(client, rid) == "cancelled"
    assert jobs.get(project["id"], "bg").status == "killed"
    evs = events(client, rid)
    assert any(e["type"] == "tool_result" and e["data"]["status"] == "cancelled" for e in evs)


def test_max_steps_limit(client, project):
    client.put("/api/settings", json={"max_steps": 2})
    try:
        with MockServer([{"tool_calls": [call("list_files")]}] * 5) as mock:
            rid = start(client, project, mock)
            assert finished(client, rid) == "stopped"
            assert len(mock.state["requests"]) == 2
    finally:
        client.put("/api/settings", json={"max_steps": 120})


def test_large_tool_output_is_truncated_and_saved(client, project, server):
    with MockServer([{"tool_calls": [call("terminal_run", command="python3 -c \"print('x'*50000)\"")]}, {"text": "done"}]) as mock:
        rid = start(client, project, mock)
        finished(client, rid)
        tool = [m for m in mock.state["requests"][-1]["messages"] if m["role"] == "tool"][0]["content"]
    assert len(tool) < 13000 and "truncated" in tool and "work/.luma/logs/" in tool


def test_api_key_never_persisted_or_logged(client, project, server):
    key = "sk-test-supersecret-9f8e7d6c5b4a3f2e1d0c"
    with MockServer([{"tool_calls": [call("terminal_run", command=f"echo {key}")]}, {"text": f"the key is {key}"}]) as mock:
        rid = start(client, project, mock, key=key)
        finished(client, rid)
        seen = mock.state["requests"][0]
    raw = (server["data"] / "studio.db").read_bytes()
    assert key.encode() not in raw
    assert key not in json.dumps(events(client, rid))
    # the model never saw the key either (terminal output is redacted)
    assert key not in json.dumps(seen)


def test_run_without_llm_config_is_rejected(client, project):
    r = client.post(f"/api/projects/{project['id']}/runs", json={"text": "hi"})
    assert r.status_code == 400 and "No LLM configured" in r.json()["detail"]


def test_models_and_connection_test_endpoints(client):
    tool_script = [
        {"text": "pong"},  # streaming check (stream=True)
        {"tool_calls": [call("get_magic_number", seed=7)]},  # tool call
        {"text": "The magic number is 4242."},  # uses the tool result
        {"text": "red"},  # vision
    ]
    with MockServer(tool_script) as mock:
        h = headers(mock)
        models = client.post("/api/models", json={}, headers=h).json()["models"]
        assert models[0]["id"] == "mock-director"
        res = client.post("/api/test-connection", json={}, headers=h).json()
    assert res["ok"], res
    assert [c["status"] for c in res["checks"]] == ["ok", "ok", "ok", "ok"]
    assert res["capabilities"]["vision"] is True
    assert httpx.codes.OK
