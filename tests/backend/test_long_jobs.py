"""Long jobs: plan rules, memory, notes, compaction, checkpoints, resume, migrations."""
import json
import os
import signal
import sqlite3
import time

from conftest import events, run_status, wait_for
from mock_llm import MockServer, call
from test_agent import finished, headers, start

PLAN = [{"title": "Discovery", "acceptance_criteria": "brand.json written", "children": [
            {"title": "Inspect logo", "acceptance_criteria": "analysis read"},
            {"title": "Write brand.json", "acceptance_criteria": "work/brand.json exists"}]},
        {"title": "Build", "children": [{"title": "Scene", "priority": "high", "acceptance_criteria": "contact sheet reviewed"}]}]


def tool_results(mock):
    """tool messages seen by the model in its last request."""
    return [m["content"] for m in mock.state["requests"][-1]["messages"] if m["role"] == "tool"]


def test_todo_rules_single_in_progress_and_evidence(client, project):
    script = [
        {"text": "Planning.", "tool_calls": [call("todo_write", items=PLAN)]},
        {"tool_calls": [call("todo_update", id="Inspect logo", status="in_progress")]},
        {"tool_calls": [call("todo_update", id="Write brand.json", status="in_progress")]},  # second in_progress → rejected
        {"tool_calls": [call("todo_update", id="Inspect logo", status="done")]},  # no evidence → rejected
        {"tool_calls": [call("todo_update", id="Inspect logo", status="done", evidence=["work/missing.json"])]},  # bad path → rejected
        {"tool_calls": [call("write_file", path="work/brand.json", content="{}"),
                        call("todo_update", id="Inspect logo", status="done", note="analysis read in the previous step")]},
        {"tool_calls": [call("todo_update", id="Write brand.json", status="in_progress")]},
        {"tool_calls": [call("todo_update", id="Write brand.json", status="done", evidence=["work/brand.json"])]},
        {"tool_calls": [call("todo_update", id="Scene", status="skipped")]},  # skip needs a reason → rejected
        {"tool_calls": [call("todo_write", items=[{"title": "Only one thing"}])]},  # re-plan without reason → rejected
        {"text": "ok"},
    ]
    with MockServer(script) as mock:
        rid = start(client, project, mock)
        finished(client, rid)
        reqs = mock.state["requests"]
    outs = [[m for m in r["messages"] if m["role"] == "tool"][-1]["content"] for r in reqs[1:]]
    assert "Discovery" in outs[0] and "0/3 done" in outs[0]
    assert "→ in_progress" in outs[1]
    assert "Only ONE item may be in progress" in outs[2]
    assert "cannot mark done without evidence" in outs[3]
    assert "does not exist" in outs[4]
    assert "→ done" in outs[5]
    assert "→ done" in outs[7] and "2/3 done" in outs[7]
    assert "needs a reason" in outs[8]
    assert "pass `reason`" in outs[9]
    plan = client.get(f"/api/runs/{rid}/plan").json()
    disc = plan["tree"][0]
    assert disc["status"] == "done"  # the phase follows its tasks
    assert disc["children"][1]["evidence"] == ["work/brand.json"]
    assert disc["children"][0]["started_at"] and disc["children"][0]["completed_at"]
    assert plan["progress"] == {"done": 2, "total": 3, "current": None}
    # the plan is pinned into the system prompt of later requests
    assert "Plan — 2/3 done" in reqs[-1]["messages"][0]["content"]
    evs = [e for e in events(client, rid) if e["type"] == "todo_update"]
    assert evs and evs[-1]["data"]["progress"]["done"] == 2


def test_replan_keeps_revisions_and_user_edits_reach_the_agent(client, project):
    script = [
        {"tool_calls": [call("todo_write", items=PLAN)]},
        {"tool_calls": [call("todo_write", items=[{"title": "Discovery"}, {"title": "Build it all"}], reason="user wants a simpler plan")]},
        {"tool_calls": [call("terminal_run", command="sleep 2")]},
        {"tool_calls": [call("todo_list")]},
        {"text": "done"},
    ]
    with MockServer(script) as mock:
        rid = start(client, project, mock)
        wait_for(lambda: len(mock.state["requests"]) >= 3, timeout=30)
        tree = client.get(f"/api/runs/{rid}/plan").json()["tree"]
        tid = tree[1]["id"]
        r = client.patch(f"/api/todos/{tid}", json={"status": "skipped", "title": "Build it all (later)"})
        assert r.status_code == 200 and r.json()["status"] == "skipped" and r.json()["note"] == "skipped by the user"
        r = client.post(f"/api/runs/{rid}/todos", json={"title": "Added by user", "acceptance_criteria": "user happy"})
        assert r.status_code == 201
        finished(client, rid)
        seen = json.dumps(mock.state["requests"][-1]["messages"])
    revs = client.get(f"/api/runs/{rid}/plan").json()["revisions"]
    assert len(revs) == 1 and revs[0]["reason"] == "user wants a simpler plan" and len(revs[0]["snapshot"]) == 5
    assert "[System note]" in seen and "The user edited the plan" in seen and "Added by user" in seen
    evs = events(client, rid)
    assert any(e["type"] == "plan_revision" for e in evs) and any(e["type"] == "system_note" for e in evs)


def test_plan_required_after_n_steps(client, project):
    client.put("/api/settings", json={"plan_required_after_steps": 2})
    try:
        script = [{"tool_calls": [call("terminal_run", command="true")]}] * 3 + [
            {"tool_calls": [call("todo_write", items=[{"title": "Do it"}])]},
            {"tool_calls": [call("terminal_run", command="echo allowed")]}, {"text": "ok"}]
        with MockServer(script) as mock:
            rid = start(client, project, mock)
            finished(client, rid)
            reqs = mock.state["requests"]
        last_tool = lambda i: [m for m in reqs[i]["messages"] if m["role"] == "tool"][-1]["content"]  # noqa: E731
        assert "exit_code: 0" in last_tool(1) and "exit_code: 0" in last_tool(2)
        assert "Plan first" in last_tool(3)
        assert "allowed" in last_tool(5)
    finally:
        client.put("/api/settings", json={"plan_required_after_steps": 5})


def test_memory_tools_rest_and_injection_into_new_runs(client, project):
    with MockServer([{"tool_calls": [call("memory_write", key="palette", value="#2970EC primary; never navy"),
                                     call("memory_write", key="voice_id", value="v_123 calm")]},
                     {"tool_calls": [call("memory_search", query="navy")]}, {"text": "ok"}]) as mock:
        rid = start(client, project, mock)
        finished(client, rid)
        assert "never navy" in tool_results(mock)[-1]
    mem = client.get(f"/api/projects/{project['id']}/memory").json()
    assert {m["key"] for m in mem} == {"palette", "voice_id"}
    client.put(f"/api/projects/{project['id']}/memory", json={"key": "spring", "value": "likes overshoot ≈ 4°"})
    assert client.delete(f"/api/projects/{project['id']}/memory/voice_id").status_code == 200
    with MockServer([{"text": "hi"}]) as mock:
        rid2 = start(client, project, mock, text="second run")
        finished(client, rid2)
        sysmsg = mock.state["requests"][0]["messages"][0]["content"]
    assert "Project memory" in sysmsg and "never navy" in sysmsg and "overshoot ≈ 4°" in sysmsg and "v_123" not in sysmsg
    assert any(e["type"] == "memory_update" for e in events(client, rid))


def test_compaction_keeps_pinned_items(client, project):
    long = "x" * 3000
    script = [{"tool_calls": [call("todo_write", items=[{"title": "Keep me in the plan"}]), call("memory_write", key="brand", value="exact #2970EC"),
                              call("notes_append", text="hypothesis: overshoot too big")]}]
    script += [{"text": f"thinking {i} {long}", "tool_calls": [call("terminal_run", command=f"echo step {i}")]} for i in range(18)]
    # after context_compact the next request is the (non-streamed) summarisation, then the model continues
    script += [{"tool_calls": [call("context_compact", reason="long session")]}, {"text": "## Decisions\n- digest"}, {"text": "done"}]
    with MockServer(script) as mock:
        rid = start(client, project, mock, text="Please make it blue and keep the hold at 0.5 s")
        finished(client, rid)
        reqs = mock.state["requests"]
    comp = [e["data"] for e in events(client, rid) if e["type"] == "compaction"]
    assert comp, "no compaction happened"
    assert "plan" in comp[-1]["kept"] and comp[-1]["reason"] == "long session"
    last = reqs[-1]["messages"]
    sysmsg = last[0]["content"]
    assert "Keep me in the plan" in sysmsg and "exact #2970EC" in sysmsg and "overshoot too big" in sysmsg
    assert "keep the hold at 0.5 s" in sysmsg  # the user's request survives even though its turn was summarised
    assert "Summary of earlier work" in json.dumps(last)
    assert len(last) < 20


def test_checkpoint_create_restore_rest_and_tools(client, project, server):
    pdir = server["data"] / "projects" / project["id"]
    script = [
        {"tool_calls": [call("todo_write", items=[{"title": "A"}, {"title": "B"}]), call("write_file", path="work/scene.py", content="v1")]},
        {"tool_calls": [call("checkpoint_create", label="first draft")]},
        {"tool_calls": [call("write_file", path="work/scene.py", content="v2"), call("write_file", path="work/extra.py", content="new"),
                        call("todo_update", id="A", status="in_progress")]},
        {"tool_calls": [call("checkpoint_list")]},
        {"text": "ok"},
    ]
    with MockServer(script) as mock:
        rid = start(client, project, mock)
        finished(client, rid)
    cps = client.get(f"/api/projects/{project['id']}/checkpoints").json()
    first = next(c for c in cps if c["label"] == "first draft")
    client.patch(f"/api/projects/{project['id']}", json={"settings": {**project["settings"], "fps": 30}})
    r = client.post(f"/api/projects/{project['id']}/checkpoints/{first['id']}/restore")
    assert r.status_code == 200, r.text
    assert (pdir / "work/scene.py").read_text() == "v1" and not (pdir / "work/extra.py").exists()
    assert client.get(f"/api/projects/{project['id']}").json()["settings"]["fps"] == 60
    assert all(t["status"] == "pending" for t in client.get(f"/api/runs/{rid}/plan").json()["tree"])
    assert "work/scene.py" in client.get(f"/api/projects/{project['id']}/checkpoints/{first['id']}/files").json()
    # the git repo is outside the workspace; undo via the safety checkpoint
    assert not (pdir / ".git").exists()
    safety = r.json()["safety_checkpoint"]
    client.post(f"/api/projects/{project['id']}/checkpoints/{safety}/restore")
    assert (pdir / "work/scene.py").read_text() == "v2" and (pdir / "work/extra.py").read_text() == "new"
    assert any(e["type"] == "checkpoint" for e in events(client, rid))


SLOW_SCENE = """
import time
from luma_engine import Scene
class Slow(Scene):
    bloom = None
    max_blur_samples = 1
    def draw(self, f, t):
        time.sleep(0.25)
        f.fill("#123456")
scene = Slow(width=64, height=36, fps=10, duration=3)
"""


def test_resume_after_simulated_restart_requeues_render(client, project, server):
    from app.agent.runner import runs
    from app.jobs import jobs

    pdir = server["data"] / "projects" / project["id"]
    cmd = "python -m luma_engine render work/slow.py --out renders/slow --workers 1"
    script = [
        {"tool_calls": [call("todo_write", items=[{"title": "Render"}]), call("write_file", path="work/slow.py", content=SLOW_SCENE)]},
        {"tool_calls": [call("terminal_spawn", name="render-slow", command=cmd.replace("work/", "../work/").replace("renders/", "../renders/"))]},
        {"tool_calls": [call("terminal_run", command="sleep 60", timeout_s=120)]},
    ]
    with MockServer(script) as mock:
        rid = start(client, project, mock)
        wait_for(lambda: len(list((pdir / "renders/slow").glob("*.png"))) >= 3 if (pdir / "renders/slow").exists() else False, timeout=60)
        # --- simulated container restart: the job dies, the loop dies, the server restarts
        job = jobs.get(project["id"], "render-slow")
        os.killpg(job.pid, signal.SIGKILL)
        runs.tasks[rid].cancel()
        wait_for(lambda: run_status(client, rid) == "cancelled", timeout=20)
        from app import db

        with db.session() as s:
            s.get(db.Run, rid).status = "running"
        wait_for(lambda: jobs.get(project["id"], "render-slow").status != "running", timeout=10)
        with db.session() as s:
            s.get(db.Job, job.id).status = "running"  # as the DB looked when the container died
        jobs.recover()
        runs.recover()
        assert run_status(client, rid) == "interrupted"
        assert jobs.get(project["id"], "render-slow").status == "lost"
        done_before = len(list((pdir / "renders/slow").glob("*.png")))
        mock.push({"tool_calls": [call("terminal_poll", name="render-slow")]}, {"text": "resumed fine"})
        r = client.post(f"/api/runs/{rid}/resume", headers=headers(mock))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["jobs_requeued"] == ["render-slow"] and body["closed_calls"]
        finished(client, rid)
        wait_for(lambda: jobs.get(project["id"], "render-slow").status == "done", timeout=60)
        seen = json.dumps(mock.state["requests"][-1]["messages"], ensure_ascii=False)
    frames = sorted((pdir / "renders/slow").glob("*.png"))
    assert len(frames) == 30 and done_before < 30
    assert "RESUMED" in seen and "Renders re-queued" in seen and "interrupted — the server restarted" in seen
    assert run_status(client, rid) == "idle"
    assert client.post(f"/api/runs/{rid}/resume", headers=headers(mock)).status_code == 409  # not resumable when idle


def test_alembic_upgrades_a_pre_alembic_database_without_data_loss(tmp_path):
    """A 1.0 database (no alembic_version, no owner_id) is stamped and upgraded in place."""
    import sqlalchemy as sa

    from app import db as appdb

    f = tmp_path / "old.db"
    con = sqlite3.connect(f)
    con.executescript("""
        CREATE TABLE settings (key VARCHAR(64) PRIMARY KEY, value JSON);
        CREATE TABLE projects (id VARCHAR(32) PRIMARY KEY, name VARCHAR(200) NOT NULL, brief TEXT NOT NULL, settings JSON NOT NULL,
                               kind VARCHAR(16) NOT NULL, created_at FLOAT NOT NULL, updated_at FLOAT NOT NULL);
        CREATE TABLE assets (id VARCHAR(32) PRIMARY KEY, project_id VARCHAR(32), filename VARCHAR(255), kind VARCHAR(16), size INTEGER,
                             path VARCHAR(512), thumb VARCHAR(512), analysis JSON, created_at FLOAT);
        CREATE TABLE runs (id VARCHAR(32) PRIMARY KEY, project_id VARCHAR(32), kind VARCHAR(16), status VARCHAR(24), model VARCHAR(200),
                           steps INTEGER, prompt_tokens INTEGER, completion_tokens INTEGER, el_chars INTEGER, error TEXT, summary TEXT,
                           outputs JSON, created_at FLOAT, updated_at FLOAT);
        CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id VARCHAR(32), role VARCHAR(16), content JSON, archived INTEGER, created_at FLOAT);
        CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id VARCHAR(32), type VARCHAR(32), data JSON, ts FLOAT);
        CREATE TABLE jobs (id VARCHAR(32) PRIMARY KEY, project_id VARCHAR(32), run_id VARCHAR(32), name VARCHAR(120), command TEXT, pid INTEGER,
                           status VARCHAR(16), exit_code INTEGER, log_path VARCHAR(512), progress JSON, tool_call_id VARCHAR(64),
                           started_at FLOAT, finished_at FLOAT);
        INSERT INTO projects VALUES ('p_old', 'Old film', 'brief', '{"fps": 30}', 'user', 1, 1);
        INSERT INTO runs VALUES ('r_old', 'p_old', 'agent', 'completed', 'm', 3, 10, 5, 0, NULL, 'done', NULL, 1, 1);
        INSERT INTO events (run_id, type, data, ts) VALUES ('r_old', 'run_status', '{"status": "completed"}', 1);
    """)
    con.commit()
    con.close()
    eng = sa.create_engine(f"sqlite:///{f}")
    appdb.migrate(eng)
    appdb.migrate(eng)  # idempotent
    insp = sa.inspect(eng)
    tables = set(insp.get_table_names())
    assert {"todos", "memories", "checkpoints", "artifacts", "user_requests", "notifications", "users", "alembic_version"} <= tables
    assert "owner_id" in {c["name"] for c in insp.get_columns("projects")}
    assert "parent_run_id" in {c["name"] for c in insp.get_columns("runs")}
    with eng.connect() as c:
        assert c.execute(sa.text("select name from projects")).scalar() == "Old film"
        assert c.execute(sa.text("select cost_usd from runs where id='r_old'")).scalar() == 0
        assert c.execute(sa.text("select count(*) from events")).scalar() == 1
        assert c.execute(sa.text("select version_num from alembic_version")).scalar() == "0002"
