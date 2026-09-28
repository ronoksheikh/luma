"""Quality gates and the full mocked long-job flow; sub-agents."""
import json
import re
import shutil
from pathlib import Path

from conftest import events, run_status, wait_for
from mock_llm import MockServer, call
from test_agent import finished, headers, start
from test_power import outs_by_name

ROOT = Path(__file__).resolve().parents[2]
FAN = """
from luma_engine.templates.fan_unfold import FanUnfold
scene = FanUnfold(logo="assets/veyra-symbol.svg", wordmark="Veyra", background="#0B0F2A", width=1080, height=1080, fps=24, duration=3)
"""


def tool_texts(body):
    return "\n".join(m["content"] for m in body["messages"] if m["role"] == "tool")


def art_ids(body):
    return re.findall(r"as artifact (art_[0-9a-f]+)", tool_texts(body))


def test_qc_failures_become_blocked_todos_and_self_review(client, project, server):
    media = ("ffmpeg -v error -y -f lavfi -i testsrc=size=320x180:rate=60:duration=5 -f lavfi -i sine=f=440:d=5 -shortest "
             "-c:v libx264 -pix_fmt yuv420p -c:a aac outputs/bad.mp4")
    script = [
        {"tool_calls": [call("todo_write", items=[{"title": "Deliver"}]), call("terminal_run", command=media, timeout_s=120)]},
        {"tool_calls": [call("qc_report", output="outputs/bad.mp4")]},
        {"tool_calls": [call("self_review", checklist_name="audio_quality"), call("self_review", checklist_name="delivery")]},
        {"tool_calls": [call("finish", summary="done", outputs=["outputs/bad.mp4"])]},
        {"text": "ok"},
    ]
    with MockServer(script) as mock:
        rid = start(client, project, mock)
        finished(client, rid)
        res = outs_by_name(mock)
    assert "added to the plan as BLOCKED" in res["qc_report"][0]
    tree = client.get(f"/api/runs/{rid}/plan").json()["tree"]
    qc_phase = next(t for t in tree if t["title"] == "Fix QC failures")
    assert qc_phase["status"] == "blocked" and any(c["title"] == "QC: audio loudness" and c["status"] == "blocked" for c in qc_phase["children"])
    assert "passes" in qc_phase["children"][0]["acceptance_criteria"]
    audio_rev = res["self_review"][0] if "loudness" in res["self_review"][0] else res["self_review"][1]
    assert re.search(r"FAIL\s+Integrated loudness", audio_rev) and "CHECK" in audio_rev
    deliv = res["self_review"][1] if audio_rev is res["self_review"][0] else res["self_review"][0]
    assert re.search(r"FAIL\s+The final video and deliverables were presented", deliv)
    fin = res["finish"][0]
    assert fin.startswith("Error: Not ready to finish") and "open plan items" in fin and "fails: " in fin and "present_video" in fin
    assert run_status(client, rid) == "idle"


def test_full_mocked_long_job_flow(client, project, server):
    """todos → storyboard → approval → render → present video (chapters) + file → QC → self-review → finish."""
    pid = project["id"]
    pdir = server["data"] / "projects" / pid
    with open(ROOT / "samples/veyra-symbol.svg", "rb") as f:
        client.post(f"/api/projects/{pid}/assets", files={"files": ("veyra-symbol.svg", f, "image/svg+xml")})
    client.patch(f"/api/projects/{pid}", json={"settings": {**project["settings"], "width": 1080, "height": 1080, "fps": 24, "duration": 3}})
    plan = [{"title": "Storyboard", "acceptance_criteria": "approved by the user"},
            {"title": "Render", "acceptance_criteria": "render finished; QC passes"},
            {"title": "Deliver", "acceptance_criteria": "video + files presented"}]

    def approval(body):
        return {"tool_calls": [call("request_approval", title="Storyboard", summary="3 shots, 3 s", artifacts=[art_ids(body)[-1]])]}

    def after_approval(body):
        return {"tool_calls": [call("todo_update", id="Storyboard", status="done", evidence=[art_ids(body)[-1]], note="approved"),
                               call("todo_update", id="Render", status="in_progress")]}

    copy_turn = {"tool_calls": [call("terminal_run", command="cp renders/final/final.mp4 outputs/final.mp4 && cp renders/final/end_card.png outputs/final_end_card.png")]}
    qc_turn = {"tool_calls": [call("qc_report", output="outputs/final.mp4", reference="renders/final/end_card.png", events_path="renders/final/events.json")]}

    def present_turn(body):
        ev = json.loads((pdir / "renders/final/events.json").read_text())["events"]
        chapters = [{"t": round(e["t"], 3), "label": e["name"]} for e in ev[:3]]
        return {"tool_calls": [call("present_video", path="outputs/final.mp4", title="Final", chapters=chapters),
                               call("present_file", path="outputs/final_end_card.png", title="End card")]}

    script = [
        {"text": "Planning the outro.", "tool_calls": [call("todo_write", items=plan), call("write_file", path="work/scene.py", content=FAN),
                                                     call("todo_update", id="Storyboard", status="in_progress")]},
        {"tool_calls": [call("present_storyboard", scene_path="work/scene.py", timings=[0.2, 1.2, 2.6], notes=["seed", "unfold", "lockup"])]},
        approval,
        after_approval,
        {"tool_calls": [call("render_final", scene_path="work/scene.py", wait_s=600)]},
        {"tool_calls": [call("finish", summary="too early", outputs=["renders/final/final.mp4"])]},  # rejected by the gate
        copy_turn,
        qc_turn,
        present_turn,
        {"tool_calls": [call("todo_update", id="Render", status="done", evidence=["outputs/final.mp4", "outputs/qc_final.json"]),
                        call("todo_update", id="Deliver", status="in_progress")]},
        {"tool_calls": [call("todo_update", id="Deliver", status="done", evidence=["outputs/final.mp4"]), call("self_review", checklist_name="delivery")]},
        {"tool_calls": [call("finish", summary="A 3 s fan-unfold outro. QC passes.", outputs=["outputs/final.mp4", "outputs/final_end_card.png"])]},
    ]
    try:
        with MockServer(script) as mock:
            rid = start(client, project, mock, text="Make a 3 s logo outro")
            q = wait_for(lambda: next((x for x in client.get(f"/api/runs/{rid}/requests", params={"status": "pending"}).json()), None), timeout=120)
            assert q["kind"] == "approval" and q["payload"]["artifacts"][0]["type"] == "storyboard"
            client.post(f"/api/runs/{rid}/requests/{q['id']}/answer", json={"choice": "Approve"})
            assert wait_for(lambda: (lambda st: st if st in ("completed", "idle", "failed") else None)(run_status(client, rid)), timeout=600) == "completed", \
                [e["data"] for e in events(client, rid) if e["type"] == "error"]
            res = outs_by_name(mock)
    finally:
        client.patch(f"/api/projects/{pid}", json={"settings": project["settings"]})
    early = res["finish"][0]
    assert early.startswith("Error: Not ready to finish") and "open plan items" in early
    assert '"pass": true' in res["qc_report"][0]
    assert "FAIL" not in res["self_review"][0].split("\n\n")[0]
    evs = events(client, rid)
    types = [e["type"] for e in evs]
    for t in ("todo_update", "present", "approval_request", "approval_result", "progress", "checkpoint", "budget"):
        assert t in types, t
    order = [e["type"] for e in evs if e["type"] in ("approval_result", "progress")]
    assert order.index("approval_result") < order.index("progress")  # rendering started after the sign-off
    video = next(e["data"]["artifact"] for e in evs if e["type"] == "present" and e["data"]["card"] == "video")
    assert video["meta"]["frames"] == 72 and len(video["meta"]["chapters"]) >= 2
    r = client.get(f"/api/runs/{rid}").json()
    assert r["status"] == "completed" and any(o["path"] == "outputs/scene_source.zip" for o in r["outputs"])
    assert client.get(f"/api/runs/{rid}/plan").json()["progress"]["done"] == 3


def _dispatch(body):
    """One scripted brain for a parent and its sub-agents (their requests interleave)."""
    sysmsg = body["messages"][0]["content"]
    tools = [m for m in body["messages"] if m["role"] == "tool"]
    if "You are a sub-agent" in sysmsg:
        brief = next(m["content"] for m in body["messages"] if m["role"] == "user")
        label = re.search(r"variant (\w+)", brief).group(1)
        if not tools:
            return {"tool_calls": [call("write_file", path=f"work/sub/{label}.txt", content=label)]}
        if "sleep" in brief and len(tools) == 1:
            return {"tool_calls": [call("terminal_run", command="sleep 60", timeout_s=120)]}
        return {"tool_calls": [call("subagent_report", result={"label": label, "file": f"work/sub/{label}.txt"}, summary=f"made {label}")]}
    if not tools:
        return {"tool_calls": [call("todo_write", items=[{"title": "Delegate"}]),
                               call("spawn_subagent", task="Make variant alpha", tools_allowed=["write_file"], budget={"max_steps": 5}, label="alpha"),
                               call("spawn_subagent", task="Make variant beta", tools_allowed=["write_file"], budget={"max_steps": 5}, label="beta")]}
    if "spawn_subagent" not in json.dumps(body["messages"][-1]) and len(tools) == 3 and "forbidden" not in json.dumps(body):
        return {"tool_calls": [call("spawn_subagent", task="forbidden", tools_allowed=["finish"])]}
    return {"text": "all done"}


def test_subagents_run_in_parallel_report_back_and_are_restricted(client, project, server):
    pid = project["id"]
    client.patch(f"/api/projects/{pid}", json={"settings": {**project["settings"], "subagents": True, "subagent_max_concurrency": 2}})
    try:
        with MockServer([_dispatch] * 12) as mock:
            rid = start(client, project, mock)
            assert finished(client, rid) == "idle"
            reqs = mock.state["requests"]
    finally:
        client.patch(f"/api/projects/{pid}", json={"settings": project["settings"]})
    evs = events(client, rid)
    starts = [e["data"] for e in evs if e["type"] == "subagent_start"]
    ends = [e["data"] for e in evs if e["type"] == "subagent_end"]
    assert sorted(s["label"] for s in starts) == ["alpha", "beta"]
    assert sorted(e["result"]["label"] for e in ends) == ["alpha", "beta"] and all(e["status"] == "completed" for e in ends)
    pdir = server["data"] / "projects" / pid
    assert (pdir / "work/sub/alpha.txt").read_text() == "alpha" and (pdir / "work/sub/beta.txt").read_text() == "beta"
    child_reqs = [r for r in reqs if "You are a sub-agent" in r["messages"][0]["content"]]
    names = {t["function"]["name"] for t in child_reqs[0]["tools"]}
    assert "write_file" in names and "subagent_report" in names and not names & {"finish", "spawn_subagent", "terminal_run", "ask_user"}
    parent_tools = {t["function"]["name"] for t in reqs[0]["tools"]}
    assert "spawn_subagent" in parent_tools and "subagent_report" not in parent_tools
    child = client.get(f"/api/runs/{starts[0]['child_run_id']}").json()
    assert child["kind"] == "subagent" and child["status"] == "completed"
    forbidden = [m["content"] for m in reqs[-1]["messages"] if m["role"] == "tool"][-1]
    assert "sub-agents cannot use ['finish']" in forbidden


def test_subagents_are_cancelled_with_the_parent(client, project):
    pid = project["id"]
    client.patch(f"/api/projects/{pid}", json={"settings": {**project["settings"], "subagents": True}})

    def brain(body):
        sysmsg = body["messages"][0]["content"]
        tools = [m for m in body["messages"] if m["role"] == "tool"]
        if "You are a sub-agent" in sysmsg:
            return {"tool_calls": [call("terminal_run", command="sleep 60", timeout_s=120)]} if not tools else {"text": "x"}
        return {"tool_calls": [call("spawn_subagent", task="Make variant slow with sleep", tools_allowed=["terminal_run"], label="slow")]} if not tools else {"text": "x"}

    try:
        with MockServer([brain] * 6) as mock:
            rid = start(client, project, mock)
            child = wait_for(lambda: next((e["data"]["child_run_id"] for e in events(client, rid) if e["type"] == "subagent_start"), None), timeout=30)
            wait_for(lambda: run_status(client, child) == "running" and len(mock.state["requests"]) >= 2, timeout=30)
            client.post(f"/api/runs/{rid}/cancel")
            assert wait_for(lambda: run_status(client, child) in ("cancelled",) and run_status(client, child), timeout=30) == "cancelled"
            assert wait_for(lambda: run_status(client, rid) == "cancelled", timeout=30)
    finally:
        client.patch(f"/api/projects/{pid}", json={"settings": project["settings"]})
    assert shutil and headers  # keep imports used
