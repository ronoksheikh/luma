"""Collaboration: ask_user (options, multi-select, free text, timeout), approvals + gates + Autopilot,
present_options, notify, report_progress, budget caps."""
import json

from conftest import events, run_status, wait_for
from mock_llm import MockServer, call
from test_agent import finished, headers, start


def pending(client, rid):
    return client.get(f"/api/runs/{rid}/requests", params={"status": "pending"}).json()


def wait_pending(client, rid, kind):
    return wait_for(lambda: next((q for q in pending(client, rid) if q["kind"] == kind), None), timeout=30)


def last_tool(mock):
    return [m for m in mock.state["requests"][-1]["messages"] if m["role"] == "tool"][-1]["content"]


def test_ask_user_options_multiselect_free_text_and_timeout(client, project):
    script = [
        {"tool_calls": [call("ask_user", question="Which moods?", options=["calm", "bold", "playful"], multi_select=True)]},
        {"tool_calls": [call("ask_user", question="Pick one", options=["a", "b"], allow_free_text=False)]},
        {"tool_calls": [call("ask_user", question="Voice?", options=["warm", "crisp"], timeout_s=1, default="crisp")]},
        {"text": "ok"},
    ]
    with MockServer(script) as mock:
        rid = start(client, project, mock)
        q = wait_pending(client, rid, "ask")
        assert q["payload"]["multi_select"] and q["payload"]["options"] == ["calm", "bold", "playful"]
        ev = [e for e in events(client, rid) if e["type"] == "ask_user"][-1]["data"]
        assert ev["request_id"] == q["id"] and ev["question"] == "Which moods?"
        assert client.post(f"/api/runs/{rid}/requests/{q['id']}/answer", json={"selections": ["nope"]}).status_code == 400
        r = client.post(f"/api/runs/{rid}/requests/{q['id']}/answer", json={"selections": ["calm", "bold"], "text": "and a bit of warmth"})
        assert r.status_code == 200
        q2 = wait_for(lambda: next((x for x in pending(client, rid) if x["payload"]["question"] == "Pick one"), None), timeout=30)
        assert client.post(f"/api/runs/{rid}/requests/{q2['id']}/answer", json={"text": "free text"}).status_code == 400
        assert client.post(f"/api/runs/{rid}/requests/{q2['id']}/answer", json={"selections": ["a", "b"]}).status_code == 400
        client.post(f"/api/runs/{rid}/requests/{q2['id']}/answer", json={"selections": ["b"]})
        finished(client, rid)  # the third question times out and uses the default
        msgs = [m["content"] for m in mock.state["requests"][-1]["messages"] if m["role"] == "tool"]
    assert msgs[0] == "The user selected: calm, bold\nThey added: and a bit of warmth"
    assert msgs[1] == "The user answered: b"
    assert msgs[2].startswith("No answer before the timeout; using the default: The user answered: crisp")
    results = [e["data"] for e in events(client, rid) if e["type"] == "approval_result"]
    assert [r["status"] for r in results] == ["answered", "answered", "timeout"]


def test_request_approval_pause_resume_and_render_gate(client, project):
    pid = project["id"]
    client.patch(f"/api/projects/{pid}", json={"settings": {**project["settings"], "width": 3840, "height": 2160}})
    script = [
        {"tool_calls": [call("todo_write", items=[{"title": "Final"}]), call("render_final", scene_path="work/scene.py")]},  # gated
        {"tool_calls": [call("request_approval", title="Final 4K render", summary="~12 min render", choices=["Approve", "Request changes"])]},
        {"tool_calls": [call("request_approval", title="Final 4K render (again)", summary="fixed the hold")]},
        {"tool_calls": [call("render_final", scene_path="work/scene.py")]},  # approved → gate passes (then fails: no scene)
        {"text": "ok"},
    ]
    try:
        with MockServer(script) as mock:
            rid = start(client, project, mock)
            q = wait_pending(client, rid, "approval")
            assert run_status(client, rid) == "waiting_input"
            ev = [e for e in events(client, rid) if e["type"] == "approval_request"][-1]["data"]
            assert ev["title"] == "Final 4K render" and ev["choices"] == ["Approve", "Request changes"]
            assert client.post(f"/api/runs/{rid}/requests/{q['id']}/answer", json={"choice": "Maybe"}).status_code == 400
            client.post(f"/api/runs/{rid}/requests/{q['id']}/answer", json={"choice": "Request changes", "note": "hold longer"})
            q2 = wait_for(lambda: next((x for x in pending(client, rid) if x["id"] != q["id"]), None), timeout=30)
            client.post(f"/api/runs/{rid}/requests/{q2['id']}/answer", json={"choice": "Approve"})
            finished(client, rid)
            outs = [[m for m in r["messages"] if m["role"] == "tool"][-1]["content"] for r in mock.state["requests"][1:]]
        assert "Approval needed before a 4K final render" in outs[0]
        assert outs[1].startswith("NOT APPROVED") and "hold longer" in outs[1]
        assert outs[2].startswith("APPROVED")
        assert "Approval needed" not in outs[3] and "does not exist" in outs[3]
        # Autopilot: no gate, approvals are automatic
        client.patch(f"/api/projects/{pid}", json={"settings": {**project["settings"], "width": 3840, "height": 2160, "autopilot": True}})
        with MockServer([{"tool_calls": [call("render_final", scene_path="work/none.py"), call("request_approval", title="t", summary="s")]},
                         {"text": "ok"}]) as mock:
            rid2 = start(client, project, mock)
            finished(client, rid2)
            outs = sorted(m["content"] for m in mock.state["requests"][-1]["messages"] if m["role"] == "tool")
        assert outs[0].startswith("APPROVED automatically") and "does not exist" in outs[1]
    finally:
        client.patch(f"/api/projects/{pid}", json={"settings": project["settings"]})


def test_typed_message_answers_a_pending_approval(client, project):
    with MockServer([{"tool_calls": [call("request_approval", title="Storyboard", summary="3 shots")]}, {"text": "ok"}]) as mock:
        rid = start(client, project, mock)
        wait_pending(client, rid, "approval")
        client.post(f"/api/runs/{rid}/message", json={"text": "make shot 2 faster"}, headers=headers(mock))
        finished(client, rid)
        assert "NOT APPROVED — the user replied: make shot 2 faster" in last_tool(mock)


def test_present_options_notify_and_progress(client, project):
    script = [
        {"tool_calls": [call("present_options", title="Pick a direction", options=[
            {"label": "Fan", "description": "blades unfold"}, {"label": "Stroke", "description": "pen writes the mark"}]),
            call("report_progress", stage="Exploring", percent=10, eta_s=600)]},
        {"tool_calls": [call("notify", message="Waiting for your approval", level="warning")]},
        {"text": "ok"},
    ]
    with MockServer(script) as mock:
        rid = start(client, project, mock)
        q = wait_pending(client, rid, "options")
        client.post(f"/api/runs/{rid}/requests/{q['id']}/answer", json={"choice": "Stroke", "note": "gold pen"})
        finished(client, rid)
        outs = [m["content"] for m in mock.state["requests"][1]["messages"] if m["role"] == "tool"]
    assert any("The user picked “Stroke”. Note: gold pen" in o for o in outs)
    evs = events(client, rid)
    stage = [e["data"] for e in evs if e["type"] == "progress_stage"][0]
    assert stage["stage"] == "Exploring" and stage["percent"] == 10
    assert [e["data"]["message"] for e in evs if e["type"] == "notify"] == ["Waiting for your approval"]
    notes = client.get("/api/notifications", params={"unread": True}).json()
    assert notes and notes[0]["message"] == "Waiting for your approval" and notes[0]["level"] == "warning"
    assert client.post("/api/notifications/read", json={}).json()["marked"] >= 1
    assert client.get("/api/notifications", params={"unread": True}).json() == []


def test_budget_caps_stop_runs_gracefully(client, project):
    from app import db

    with MockServer([{"tool_calls": [call("list_files")]}] * 10) as mock:
        pricing = db.get_setting("model_pricing") or {}
        db.set_setting("model_pricing", {**pricing, f"{mock.url}|mock-director": {"prompt": 0.0005, "completion": 0.001}})
        client.put("/api/settings", json={"max_cost_usd": 1.2})
        try:
            rid = start(client, project, mock)
            assert finished(client, rid) == "stopped"
            n = len(mock.state["requests"])
        finally:
            client.put("/api/settings", json={"max_cost_usd": 0})
    err = [e["data"] for e in events(client, rid) if e["type"] == "error"][-1]
    assert err["code"] == "budget" and "cost cap" in err["message"]
    budgets = [e["data"] for e in events(client, rid) if e["type"] == "budget"]
    assert budgets[-1]["cost_usd"] >= 1.2 and budgets[-1]["pricing_known"] and n < 10
    b = client.get(f"/api/runs/{rid}/budget").json()
    assert b["steps"] == n and b["cost_usd"] == budgets[-1]["cost_usd"]
    # token cap
    client.put("/api/settings", json={"max_tokens": 2500})
    try:
        with MockServer([{"tool_calls": [call("list_files")]}] * 10) as mock:
            rid = start(client, project, mock)
            assert finished(client, rid) == "stopped"
        assert "token cap" in [e["data"] for e in events(client, rid) if e["type"] == "error"][-1]["message"]
    finally:
        client.put("/api/settings", json={"max_tokens": 0})
    assert json  # keep import
