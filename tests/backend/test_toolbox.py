"""Self-extending toolbox: manifests, the sandboxed runner (schemas, guards), registration rules, the dynamic
registry, project-over-global overrides, health (auto-disable → todo), the promotion gate + static scan, venv
persistence, plugins, templates, ctx.call_tool budgets, skills, seeds — and the full mocked lifecycle."""
import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import events, login_client, wait_for
from mock_llm import MockServer, call
from test_agent import finished, headers, start
from toolbox_helpers import MAIN, MANIFEST, body

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def booted(server):
    from app.toolbox import boot, venv

    wait_for(lambda: venv.ready.is_set() and boot.state.get("done"), timeout=300, interval=0.5)
    return boot.state


def tool_names(req) -> set:
    return {t["function"]["name"] for t in req.get("tools") or []}


def results(req) -> list[tuple[str, str]]:
    """(tool name, result) in call order, from the last request's messages."""
    names = {}
    for m in req["messages"]:
        for tc in m.get("tool_calls") or []:
            names[tc["id"]] = tc["function"]["name"]
    return [(names.get(m["tool_call_id"], "?"), m["content"]) for m in req["messages"] if m["role"] == "tool"]


# ------------------------------------------------------------------------------------------------ manifests
def test_manifest_validation(server):
    from app.toolbox import manifest as M

    ok = M.validate({"name": "fit_lockup", "version": "1.2.0", "scope": "global", "description": "Fits a lockup to a reference by least squares.",
                     "parameters": {"type": "object", "properties": {"a": {"type": "string"}}}, "returns": {"type": "object"},
                     "dependencies": ["numpy==2.1.*", "scikit-image==0.24.0"]})
    assert ok["timeout_s"] == 300 and ok["network"] is False and ok["resources"]["max_memory_mb"] == 2048
    bad = {"name": "Bad-Name", "version": "1.2", "scope": "everywhere", "description": "short", "parameters": {"type": "array"},
           "returns": {"type": "object", "properties": {"x": {"type": "nonsense"}}}, "dependencies": ["numpy", "requests>=2"], "extra": 1}
    with pytest.raises(M.ManifestError) as e:
        M.validate(bad, reserved={"read_file"})
    msg = str(e.value)
    for frag in ("snake_case", "semver", "scope", "description", "parameters.type must be 'object'", "returns is not a valid JSON Schema",
                 "must be pinned", "Additional properties"):
        assert frag in msg, frag
    with pytest.raises(M.ManifestError, match="clashes with a built-in"):
        M.validate({**ok, "name": "read_file"}, reserved={"read_file"})
    assert M.bump("1.2.3") == "1.2.4" and M.bump("1.2.3", "minor") == "1.3.0" and M.bump("1.2.3", "major") == "2.0.0"


# ------------------------------------------------------------------------------------------------ runner / register rules
def test_create_test_register_try_and_schema_checks(client, project, booted):
    pid = project["id"]
    r = client.post("/api/toolbox/tools", json=body("square_it", project_id=pid))
    assert r.status_code == 201, r.text
    res = r.json()
    assert res["test"]["status"] == "passed" and res["tool"]["status"] == "disabled"  # created ≠ registered
    assert {d["file"] for d in res["diff"]} == {"tool.yaml", "main.py", "test_tool.py", "README.md"}
    assert (Path(os.environ["LUMA_DATA_DIR"]) / "projects" / pid / "work/tools/square_it/tool.yaml").exists()
    # author is user; created_from_run null
    d = client.get("/api/toolbox/tools/project/square_it", params={"project_id": pid}).json()
    assert d["author"] == "user" and d["manifest"]["author"] == "user" and d["files"]["main.py"].startswith("def run")
    # bad params → a clear schema error (not counted as a failure)
    t = client.post("/api/toolbox/tools/project/square_it/try", json={"project_id": pid, "params": {"n": "three"}}).json()
    assert t["kind"] == "params" and "'three' is not of type 'integer'" in t["error"]
    assert client.post("/api/toolbox/tools/project/square_it/enable", params={"project_id": pid}).status_code == 200
    t = client.post("/api/toolbox/tools/project/square_it/try", json={"project_id": pid, "params": {"n": 7}}).json()
    assert t["ok"] and t["result"] == {"square": 49} and "squaring 7" in t["logs"]
    assert (Path(os.environ["LUMA_DATA_DIR"]) / "projects" / pid / t["out_dir"] / "square.txt").read_text() == "49"
    stats = client.get("/api/toolbox/tools/project/square_it", params={"project_id": pid}).json()["stats"]
    assert stats["calls"] == 1 and stats["ok"] == 1
    # a result that violates `returns`
    bad = body("bad_returns", project_id=pid, main_py="def run(params, ctx):\n    return {'square': 'many'}\n",
               test_py="from main import run\n\n\ndef test_runs():\n    assert run({'n': 1}, None)\n")
    assert client.post("/api/toolbox/tools", json=bad).status_code == 201
    assert client.post("/api/toolbox/tools/project/bad_returns/enable", params={"project_id": pid}).status_code == 200
    t = client.post("/api/toolbox/tools/project/bad_returns/try", json={"project_id": pid, "params": {"n": 2}}).json()
    assert not t["ok"] and t["kind"] == "returns" and "does not match its `returns` schema" in t["error"] and "'many' is not of type 'integer'" in t["error"]


def test_failing_tests_and_static_checks_block_register(client, project, booted):
    pid = project["id"]
    failing = body("always_fails", project_id=pid, test_py="def test_nope():\n    assert 1 + 1 == 3\n")
    res = client.post("/api/toolbox/tools", json=failing).json()
    assert res["test"]["status"] == "failed" and "1 failed" in res["test"]["output"]
    r = client.post("/api/toolbox/tools/project/always_fails/enable", params={"project_id": pid})
    assert r.status_code == 400 and "tests do not pass" in r.json()["detail"]
    # a secret, a /proc/*/environ read and network code with network: false are blocked by the static scan
    leaky = MAIN.replace("def run(params, ctx):", 'API_KEY = "sk-live-abcdefghijklmnopqrstuvwxyz0123"\nimport urllib.request\n\n\n'
                         'def env():\n    return open("/proc/self/environ").read()\n\n\ndef run(params, ctx):')
    res = client.post("/api/toolbox/tools", json=body("leaky_tool", project_id=pid, main_py=leaky)).json()
    rules = {f["rule"] for f in res["scan"]["blocking"]}
    assert {"secret", "proc-environ", "network"} <= rules, res["scan"]
    r = client.post("/api/toolbox/tools/project/leaky_tool/enable", params={"project_id": pid})
    assert r.status_code == 400 and "static checks" in r.json()["detail"] and "[secret]" in r.json()["detail"]
    # the key never reaches events / the API response in clear text
    assert "sk-live-abcdefghijklmnopqrstuvwxyz0123" not in json.dumps(client.get("/api/toolbox/audit").json())


def test_sandbox_guards_writes_and_network(client, project, booted):
    pid = project["id"]
    main = '''import socket


def run(params, ctx):
    out = {}
    try:
        open("/etc/luma_evil", "w")
    except PermissionError as e:
        out["write"] = str(e)
    try:
        socket.create_connection(("127.0.0.1", 9), timeout=1)
    except PermissionError as e:
        out["net"] = str(e)
    try:
        open(str(ctx.workspace) + "/../../secrets/x")
    except (PermissionError, FileNotFoundError) as e:
        out["secrets"] = type(e).__name__
    return out
'''
    m = {**MANIFEST, "returns": {"type": "object"}}
    res = client.post("/api/toolbox/tools", json=body("guard_probe", project_id=pid, manifest=m, main_py=main,
                                                      test_py="def test_ok():\n    assert True\n")).json()
    # importing socket with network: false is itself flagged → force-register through the lifecycle to test the runtime guards
    assert any(f["rule"] == "network" for f in res["scan"]["blocking"])
    from app.toolbox import store
    from app.toolbox.execute import run_tool

    row = store.get("project", pid, "guard_probe")
    pdir = Path(os.environ["LUMA_DATA_DIR"]) / "projects" / pid
    out = asyncio.run(run_tool(row, {"n": 1}, pdir))
    assert out.ok, out.error
    assert "may only write inside the workspace" in out.result["write"]
    assert "network access is off" in out.result["net"]
    assert out.result["secrets"] == "PermissionError"


def test_crash_timeout_and_memory_never_hurt_the_backend(client, project, booted):
    pid = project["id"]
    from app.toolbox import store
    from app.toolbox.execute import run_tool

    pdir = Path(os.environ["LUMA_DATA_DIR"]) / "projects" / pid
    cases = {"crashy": "import os\n\n\ndef run(params, ctx):\n    os._exit(3)\n",
             "sleepy": "import time\n\n\ndef run(params, ctx):\n    time.sleep(30)\n    return {}\n",
             "hungry": "def run(params, ctx):\n    x = bytearray(900 * 1024 * 1024)\n    return {'n': len(x)}\n"}
    for name, main in cases.items():
        m = {**MANIFEST, "returns": {"type": "object"}, "timeout_s": 2, "resources": {"max_memory_mb": 256}}
        client.post("/api/toolbox/tools", json=body(name, project_id=pid, manifest=m, main_py=main, test_py="def test_ok():\n    assert True\n"))
        out = asyncio.run(run_tool(store.get("project", pid, name), {"n": 1}, pdir))
        assert not out.ok, name
        if name == "crashy":
            assert "exited with code 3" in out.error
        if name == "sleepy":
            assert out.kind == "timeout" and "timed out after 2s" in out.error
        if name == "hungry":
            assert "MemoryError" in out.error
    assert client.get("/healthz").status_code == 200


# ------------------------------------------------------------------------------------------------ registry
def test_project_tool_overrides_global(client, project, booted):
    pid = project["id"]
    g = body("shadow_me", scope="global", main_py=MAIN.replace("n * n}", "n * n, 'from': 'global'}").replace('"square": n * n,', '"square": n * n,'))
    g["manifest"] = {**MANIFEST, "returns": {"type": "object"}}
    g["main_py"] = "def run(params, ctx):\n    return {'from': 'global'}\n"
    g["test_py"] = "from main import run\n\n\ndef test_it():\n    assert run({}, None) == {'from': 'global'}\n"
    assert client.post("/api/toolbox/tools", json=g).status_code == 201
    assert client.post("/api/toolbox/tools/global/shadow_me/enable").status_code == 200
    p = dict(g, scope="project", project_id=pid, main_py="def run(params, ctx):\n    return {'from': 'project'}\n",
             test_py="from main import run\n\n\ndef test_it():\n    assert run({}, None) == {'from': 'project'}\n")
    assert client.post("/api/toolbox/tools", json=p).status_code == 201
    assert client.post("/api/toolbox/tools/project/shadow_me/enable", params={"project_id": pid}).status_code == 200
    from app.toolbox import store

    assert store.resolve("shadow_me", pid).scope == "project"
    assert store.resolve("shadow_me", None).scope == "global"
    lst = {(t["name"], t["scope"]): t for t in client.get("/api/toolbox/tools", params={"project_id": pid}).json()}
    assert lst[("shadow_me", "project")]["overrides_global"] and lst[("shadow_me", "global")]["shadowed"]
    script = [{"tool_calls": [call("toolbox_call", name="shadow_me", params={"n": 1})]}, {"text": "ok"}]
    with MockServer(script) as mock:
        rid = start(client, project, mock)
        finished(client, rid)
        out = [m["content"] for m in mock.state["requests"][-1]["messages"] if m["role"] == "tool"][0]
    assert '"from": "project"' in out
    assert "overrides the global tool" in client.get("/api/toolbox/summary", params={"project_id": pid}).text or \
        "shadow_me" in client.get("/api/toolbox/summary", params={"project_id": pid}).json()["overrides"]


def test_auto_disable_after_three_failures_creates_a_blocked_todo(client, project, booted):
    pid = project["id"]
    flaky = body("flaky_tool", project_id=pid, main_py="def run(params, ctx):\n    if params['n'] < 0:\n        raise RuntimeError('negative input broke me')\n"
                                                         "    return {'square': params['n'] ** 2}\n",
                 test_py="from main import run\n\n\ndef test_ok():\n    assert run({'n': 2}, None)['square'] == 4\n")
    client.post("/api/toolbox/tools", json=flaky)
    assert client.post("/api/toolbox/tools/project/flaky_tool/enable", params={"project_id": pid}).status_code == 200
    script = [{"tool_calls": [call("todo_write", items=[{"title": "Use the flaky tool"}])]}] + \
        [{"tool_calls": [call("flaky_tool", n=-1)]} for _ in range(3)] + [{"tool_calls": [call("flaky_tool", n=2)]}, {"text": "done"}]
    with MockServer(script) as mock:
        rid = start(client, project, mock)
        finished(client, rid)
        reqs = mock.state["requests"]
        tools_msgs = [m["content"] for m in reqs[-1]["messages"] if m["role"] == "tool"]
    assert "flaky_tool" in tool_names(reqs[1]) and "flaky_tool" not in tool_names(reqs[4])  # gone from the list once disabled
    assert "negative input broke me" in tools_msgs[1] and "was disabled" in tools_msgs[3]
    assert "is failing" in tools_msgs[4] or "not registered" in tools_msgs[4]
    from app import plan

    fix = [t for t in plan.todos(rid) if t["title"] == "Fix tool flaky_tool"]
    assert fix and fix[0]["status"] == "blocked" and "negative input broke me" in fix[0]["detail"]
    ev = [e["data"] for e in events(client, rid) if e["type"] == "tool_disabled"]
    assert ev and ev[0]["name"] == "flaky_tool" and len(ev[0]["traces"]) == 3
    assert any("flaky_tool was disabled" in n["message"] for n in client.get("/api/notifications").json())
    t = client.get("/api/toolbox/tools/project/flaky_tool", params={"project_id": pid}).json()
    assert t["status"] == "failing" and t["stats"]["fail_streak"] == 3 and len(t["recent_calls"]) == 3


# ------------------------------------------------------------------------------------------------ promotion
def test_promotion_scan_blocks_hard_coded_paths_and_brand_values(client, project, booted):
    pid = project["id"]
    client.patch(f"/api/projects/{pid}", json={"name": "Veyra launch"})
    from app import memory

    memory.write(pid, "palette", "#FF5A5F → #FFB547 on #0B0F2A")
    main = ('LOGO = "/data/projects/' + pid + '/assets/veyra-symbol.svg"\nCORAL = "#FF5A5F"\n\n\n'
            'def run(params, ctx):\n    return {"square": params["n"] ** 2, "logo": LOGO, "c": CORAL, "brand": "Veyra"}\n')
    client.post("/api/toolbox/tools", json=body("brandy_tool", project_id=pid, main_py=main,
                                                test_py="from main import run\n\n\ndef test_it():\n    assert run({'n': 2}, None)['square'] == 4\n"))
    assert client.post("/api/toolbox/tools/project/brandy_tool/enable", params={"project_id": pid}).status_code == 200
    prev = client.get("/api/toolbox/tools/project/brandy_tool/promotion", params={"project_id": pid}).json()
    rules = {f["rule"] for f in prev["findings"]}
    assert {"hard-coded-path", "project-id", "brand-value"} <= rules, prev["findings"]
    r = client.post("/api/toolbox/tools/project/brandy_tool/promote", params={"project_id": pid})
    assert r.status_code == 400 and "Parameterize them" in r.json()["detail"] and "#FF5A5F" in r.json()["detail"]
    # a fake secret also blocks promotion (and registration)
    sec = MAIN.replace("def run(params, ctx):", 'TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"\n\n\ndef run(params, ctx):')
    client.post("/api/toolbox/tools", json=body("secret_tool", project_id=pid, main_py=sec))
    prev = client.get("/api/toolbox/tools/project/secret_tool/promotion", params={"project_id": pid}).json()
    assert "secret" in {f["rule"] for f in prev["findings"]}
    assert client.post("/api/toolbox/tools/project/secret_tool/promote", params={"project_id": pid}).status_code == 400
    assert not (Path(os.environ["LUMA_DATA_DIR"]) / "toolbox/tools/brandy_tool").exists()


# ------------------------------------------------------------------------------------------------ versions
def test_update_versions_diff_and_revert(client, project, booted):
    pid = project["id"]
    client.post("/api/toolbox/tools", json=body("versioned", project_id=pid))
    client.post("/api/toolbox/tools/project/versioned/enable", params={"project_id": pid})
    r = client.put("/api/toolbox/tools/project/versioned", params={"project_id": pid},
                   json={"main_py": MAIN.replace("n * n}", "n * n + 0}"), "reason": "no-op tweak", "bump": "minor"}).json()
    assert r["version"] == "1.1.0" and r["registered"] and any(d["file"] == "main.py" for d in r["diff"])
    hist = client.get("/api/toolbox/tools/project/versioned/history", params={"project_id": pid}).json()
    vers = [v for h in hist for v in h["versions"]]
    assert {"1.0.0", "1.1.0"} <= set(vers)
    d = client.get("/api/toolbox/tools/project/versioned/diff", params={"project_id": pid, "a": "1.0.0", "b": "1.1.0"}).json()["diff"]
    assert "+    return {\"square\": n * n + 0}" in d
    rv = client.post("/api/toolbox/tools/project/versioned/revert", params={"project_id": pid}, json={"version": "1.0.0"}).json()
    assert rv["version"] == "1.1.1" and rv["registered"]
    t = client.get("/api/toolbox/tools/project/versioned", params={"project_id": pid}).json()
    assert "n * n + 0" not in t["files"]["main.py"] and t["version"] == "1.1.1"
    # editing the files by hand makes a registered tool "modified" until it is re-tested
    f = Path(os.environ["LUMA_DATA_DIR"]) / "projects" / pid / "work/tools/versioned/main.py"
    f.write_text(f.read_text() + "\n# hand edit\n")
    assert client.get("/api/toolbox/tools/project/versioned", params={"project_id": pid}).json()["status"] == "modified"
    client.post("/api/toolbox/tools/project/versioned/test", params={"project_id": pid})
    client.post("/api/toolbox/tools/project/versioned/enable", params={"project_id": pid})
    assert client.get("/api/toolbox/tools/project/versioned", params={"project_id": pid}).json()["status"] == "enabled"


# ------------------------------------------------------------------------------------------------ venv
def _toy_wheel(tmp: Path) -> Path:
    src = tmp / "lumatoy"
    (src / "lumatoy").mkdir(parents=True)
    (src / "lumatoy" / "__init__.py").write_text("VALUE = 42\n")
    (src / "pyproject.toml").write_text('[build-system]\nrequires = ["setuptools"]\nbuild-backend = "setuptools.build_meta"\n'
                                        '[project]\nname = "lumatoy"\nversion = "0.1.0"\n')
    wh = tmp / "wheels"
    subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "-w", str(wh), str(src)], check=True,
                   capture_output=True)
    return wh


def test_venv_persists_and_is_rebuilt_from_the_lock(client, project, booted, tmp_path, monkeypatch):
    from app.toolbox import venv

    wh = _toy_wheel(tmp_path)
    monkeypatch.setenv("PIP_FIND_LINKS", str(wh))
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    pid = project["id"]
    m = {**MANIFEST, "dependencies": ["lumatoy==0.1.0"]}
    main = "import lumatoy\n\n\ndef run(params, ctx):\n    return {'square': lumatoy.VALUE}\n"
    test = "from main import run\n\n\ndef test_dep():\n    assert run({'n': 1}, None)['square'] == 42\n"
    res = client.post("/api/toolbox/tools", json=body("uses_dep", project_id=pid, manifest=m, main_py=main, test_py=test)).json()
    assert res["deps"]["ok"], res["deps"]
    assert res["test"]["status"] == "passed", res["test"]
    lock = (Path(os.environ["LUMA_DATA_DIR"]) / "toolbox/requirements.lock").read_text()
    assert "lumatoy==0.1.0" in lock
    vd = Path(os.environ["LUMA_DATA_DIR"]) / "venv"
    # (1) a container rebuild keeps /data: the venv's interpreter link is re-created, installed packages survive
    (vd / "bin" / "python").unlink()
    st = venv.ensure()
    assert st["status"] == "ok" and not st["missing"]
    assert any("repairing" in line for line in st["log"])
    # (2) the venv lost entirely (new volume, python upgrade): rebuilt and reinstalled from the lock
    shutil.rmtree(vd)
    st = venv.ensure()
    assert st["status"] == "ok" and any("reinstalled: lumatoy==0.1.0" in line for line in st["log"]), st["log"]
    out = subprocess.run([str(vd / "bin/python"), "-c", "import lumatoy, luma_engine; print(lumatoy.VALUE)"], capture_output=True, text=True)
    assert out.stdout.strip() == "42", out.stderr
    assert client.post("/api/toolbox/tools/project/uses_dep/enable", params={"project_id": pid}).status_code == 200
    t = client.post("/api/toolbox/tools/project/uses_dep/try", json={"project_id": pid, "params": {"n": 1}}).json()
    assert t["ok"] and t["result"] == {"square": 42}


# ------------------------------------------------------------------------------------------------ plugins + templates
FX_CODE = '''import numpy as np


def apply(frame, t, color=(0.9, 0.95, 1.0), width=0.12, strength=2.0):
    """Liquid chrome sweep: a soft diagonal light band crossing the frame as t goes 0 → 1."""
    h, w = frame.h, frame.w
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    u = (xx / w + yy / h) / 2.0
    band = np.exp(-(((u - t) / width) ** 2)) * strength
    light = np.stack([band * c for c in color], axis=-1).astype(np.float32)
    frame.add_light(light)
    return float(band.max())
'''
FX_TEST = '''import numpy as np


def test_sweep_adds_light_where_the_band_is():
    from luma_engine.layers import Frame
    from luma_engine.plugins import chrome_sweep_fx

    f = Frame(64, 36)
    peak = chrome_sweep_fx.apply(f, 0.5)
    assert peak > 1.5
    img = f.resolve_linear()
    assert img[18, 32].sum() > img[0, 0].sum()
'''


def test_plugin_discovery_and_scene_import(client, booted):
    from app.toolbox import plugins as P

    res = asyncio.run(P.create("fx", "chrome_sweep_fx", FX_CODE, FX_TEST, "A liquid chrome light sweep across the frame (test plugin).",
                               actor="user:tester", author="user"))
    assert res["enabled"], res["test"]["output"]
    reg = json.loads((Path(os.environ["LUMA_DATA_DIR"]) / "toolbox/plugins/registry.json").read_text())
    assert reg["chrome_sweep_fx"]["enabled"] and reg["chrome_sweep_fx"]["kind"] == "fx"
    tmp = Path(os.environ["LUMA_DATA_DIR"]) / "scratch_scene"
    tmp.mkdir(exist_ok=True)
    (tmp / "scene.py").write_text(
        "from luma_engine.scene import Scene\nfrom luma_engine.plugins import chrome_sweep_fx\n\n\n"
        "class Sweep(Scene):\n    def draw(self, frame, t):\n        frame.fill('#0B0F2A')\n        chrome_sweep_fx.apply(frame, t / self.duration)\n\n\n"
        "scene = Sweep(width=96, height=54, fps=12, duration=1)\n")
    env = {**os.environ, "LUMA_PLUGIN_DIRS": str(Path(os.environ["LUMA_DATA_DIR"]) / "toolbox/plugins")}
    out = subprocess.run([sys.executable, "-m", "luma_engine", "preview", str(tmp / "scene.py"), "--times", "0.5", "--scale", "1", "--out", str(tmp / "still.png")],
                         capture_output=True, text=True, env=env, cwd=str(tmp))
    assert out.returncode == 0, out.stdout + out.stderr
    assert (tmp / "still.png").exists()
    assert any(p["name"] == "chrome_sweep_fx" for p in client.get("/api/toolbox/plugins").json())
    # disabled plugins do not import
    asyncio.run(P.set_enabled("chrome_sweep_fx", False))
    out = subprocess.run([sys.executable, "-c", "from luma_engine.plugins import chrome_sweep_fx"], capture_output=True, text=True, env=env)
    assert out.returncode != 0 and "disabled" in out.stderr
    asyncio.run(P.set_enabled("chrome_sweep_fx", True))


def test_template_save_then_new_project_from_template_renders(client, project, booted):
    pid = project["id"]
    pdir = Path(os.environ["LUMA_DATA_DIR"]) / "projects" / pid
    with open(ROOT / "samples/veyra-symbol.svg", "rb") as f:
        client.post(f"/api/projects/{pid}/assets", files={"files": ("veyra-symbol.svg", f, "image/svg+xml")})
    (pdir / "work/scene.py").write_text("from luma_engine.templates.fan_unfold import FanUnfold\n"
                                       "scene = FanUnfold(logo='assets/veyra-symbol.svg', wordmark='Veyra', background='#0B0F2A', "
                                       "width=320, height=180, fps=12, duration=2, sound=False)\n")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x0B0F2A:s=320x180:d=1", "-pix_fmt", "yuv420p",
                    str(pdir / "outputs/preview.mp4")], check=True)
    from app.toolbox import plugins as P

    schema = {"type": "object", "properties": {"logo": {"type": "string", "default": "assets/veyra-symbol.svg"},
                                               "wordmark": {"type": ["string", "null"], "default": "Veyra"},
                                               "background": {"type": "string", "default": "#0B0F2A"},
                                               "spring_freq": {"type": "number", "default": 2.1}}}
    with pytest.raises(Exception, match="needs a default"):
        asyncio.run(P.template_save(pdir, pid, "bad_tpl", "work/scene.py", {"type": "object", "properties": {"logo": {"type": "string"}}},
                                    None, "A test template without defaults."))
    res = asyncio.run(P.template_save(pdir, pid, "veyra_fan_outro", "work/scene.py", schema, "outputs/preview.mp4",
                                      "Fan unfold outro saved from a test project.", "Fan outro"))
    assert res["enabled"], res["test"]["output"]
    assert res["template"]["assets"] == ["assets/veyra-symbol.svg"]
    tpls = {t["name"]: t for t in client.get("/api/toolbox/templates").json()}
    assert tpls["veyra_fan_outro"]["thumbnail"] and tpls["fan_unfold"]["source"] == "builtin"
    assert client.get(tpls["veyra_fan_outro"]["thumbnail"]).status_code == 200
    r = client.post("/api/toolbox/templates/veyra_fan_outro/use", json={"name": "From template", "params": {"background": "#101A3A"}})
    assert r.status_code == 201, r.text
    new = r.json()
    npdir = Path(os.environ["LUMA_DATA_DIR"]) / "projects" / new["project_id"]
    assert (npdir / "assets/veyra-symbol.svg").exists() and new["params"]["background"] == "#101A3A"
    assert "from luma_engine.plugins import veyra_fan_outro" in (npdir / "work/scene.py").read_text()
    env = {**os.environ, "LUMA_PLUGIN_DIRS": str(Path(os.environ["LUMA_DATA_DIR"]) / "toolbox/plugins")}
    out = subprocess.run([sys.executable, "-m", "luma_engine", "pipeline", "work/scene.py", "--out", "renders/final", "--name", "final",
                          "--workers", "1"], capture_output=True, text=True, env=env, cwd=str(npdir))
    assert (npdir / "renders/final/final.mp4").exists(), out.stdout[-2000:] + out.stderr[-2000:]
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(npdir / "renders/final/final.mp4")],
                                      capture_output=True, text=True).stdout)
    v = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (320, 180)


# ------------------------------------------------------------------------------------------------ ctx.call_tool
def test_call_tool_goes_through_the_elevenlabs_budget(client, project, booted, server):
    from app.config import config
    from mock_elevenlabs import MockElevenLabs

    pid = project["id"]
    main = '''from luma_engine.toolkit import ToolCallError


def run(params, ctx):
    try:
        r = ctx.call_tool("el_tts", {"text": params["text"], "voice_id": "voice_calm_f", "name": params["name"]})
        return {"ok": True, "result": str(r)[:300]}
    except ToolCallError as e:
        return {"ok": False, "refused": str(e)}
'''
    m = {**MANIFEST, "parameters": {"type": "object", "properties": {"text": {"type": "string"}, "name": {"type": "string"}}, "required": ["text", "name"]},
         "returns": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}}
    test = "from main import run\nfrom luma_engine.toolkit import make_test_ctx\n\n\ndef test_fake(tmp_path):\n" \
           "    ctx = make_test_ctx(tmp_path, tools={'el_tts': lambda p: 'audio/x.wav'})\n    assert run({'text': 'hi', 'name': 'x'}, ctx)['ok']\n"
    res = client.post("/api/toolbox/tools", json=body("voice_line", project_id=pid, manifest=m, main_py=main, test_py=test)).json()
    assert res["test"]["status"] == "passed", res["test"]
    assert client.post("/api/toolbox/tools/project/voice_line/enable", params={"project_id": pid}).status_code == 200
    client.put("/api/settings", json={"el_char_budget": 40})
    script = [{"tool_calls": [call("voice_line", text="Meet Veyra.", name="vo_ok")]},
              {"tool_calls": [call("voice_line", text="x" * 200, name="vo_big")]}, {"text": "ok"}]
    try:
        with MockElevenLabs() as el, MockServer(script) as mock:
            old = config.elevenlabs_base_url
            config.elevenlabs_base_url = el.url
            try:
                h = {**headers(mock), "X-ElevenLabs-Api-Key": "el-test-key-123456"}
                r = client.post(f"/api/projects/{pid}/runs", json={"text": "voice it"}, headers=h)
                rid = r.json()["id"]
                finished(client, rid)
                outs = [m["content"] for m in mock.state["requests"][-1]["messages"] if m["role"] == "tool"]
                calls = el.state["calls"]["tts"]
            finally:
                config.elevenlabs_base_url = old
    finally:
        client.put("/api/settings", json={"el_char_budget": 5000})
    assert '"ok": true' in outs[0] and "vo_ok" in outs[0]
    assert '"ok": false' in outs[1] and "budget" in outs[1]
    assert calls == 1  # the over-budget request never reached ElevenLabs
    run = client.get(f"/api/runs/{rid}").json()
    assert run["el_chars"] == 11
    assert "el-test-key-123456" not in json.dumps(events(client, rid))


# ------------------------------------------------------------------------------------------------ skills + seeds
def test_seeded_tools_and_skills(client, booted):
    tools = {t["name"]: t for t in client.get("/api/toolbox/tools", params={"scope": "global"}).json()}
    seeds = {"svg_split_exact", "lockup_fit_reference", "board_residual_extract", "audio_event_spectrogram", "reframe_safe_areas",
             "palette_from_image", "final_frame_exactness"}
    assert seeds <= set(tools)
    assert all(tools[n]["status"] == "enabled" and tools[n]["test_status"] == "passed" for n in seeds), {n: tools[n]["status"] for n in seeds}
    skills = {s["name"] for s in client.get("/api/toolbox/skills").json()}
    assert {"brand_discovery", "logo_split_exact", "lockup_fit_from_board", "hidden_board_elements_extraction", "motion_blur_and_hdr_compositing",
            "sound_design_event_sync", "final_frame_exactness_qc"} <= skills
    s = client.get("/api/toolbox/skills/motion_blur_and_hdr_compositing").json()
    assert "alphaType=kPremul" in s["content"]
    hits = client.get("/api/toolbox/search", params={"q": "split a logo into parts exactly"}).json()
    assert hits[0]["name"] in ("svg_split_exact", "logo_split_exact")


def test_skill_validation_and_user_edit(client, booted):
    bad = client.put("/api/toolbox/skills/half_skill", json={"content": "---\nname: half_skill\ndescription: x\n---\n## Steps\n1. do it\n"})
    assert bad.status_code == 400 and "missing sections" in bad.json()["detail"] and "description" in bad.json()["detail"]
    good = ("---\nname: user_playbook\ndescription: How the user likes captions to be timed on brand films.\ntags: [captions]\n---\n"
            "## When to use\nAny film with captions.\n\n## Steps\n1. Split at phrases, max 32 chars.\n2. Highlight the active word.\n\n"
            "## Pitfalls\n- Two-line captions cover the lockup: keep them above the title-safe bottom.\n\n"
            "## Verification\n- No caption overlaps the logo box in any frame.\n\n## Example\nOutro VO: 3 cues, 1.1 s each.\n")
    r = client.put("/api/toolbox/skills/user_playbook", json={"content": good})
    assert r.status_code == 200 and r.json()["skill"]["author"] == "user"
    assert client.get("/api/toolbox/skills", params={"q": "caption timing"}).json()[0]["name"] == "user_playbook"
    audit = client.get("/api/toolbox/audit").json()
    assert any(a["target"] == "skill:user_playbook" and a["actor"].startswith("user:") for a in audit)


def test_relevant_skills_are_pinned_into_the_run(client, project, booted):
    pid = project["id"]
    client.patch(f"/api/projects/{pid}", json={"brief": "Split the logo into blades and unfold them; the final frame must match the board exactly."})
    with MockServer([{"text": "ok"}]) as mock:
        rid = start(client, project, mock, text="make it")
        finished(client, rid)
        system = mock.state["requests"][0]["messages"][0]["content"]
    assert "## Relevant skills" in system and "logo_split_exact" in system
    assert "## Build your own tools" in system and "registered toolbox tool(s) in reach" in system


# ------------------------------------------------------------------------------------------------ the full lifecycle
SHARP_MAIN = '''import cv2
import numpy as np


def run(params, ctx):
    img = cv2.imread(str(ctx.path(params["image"])), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError(f"cannot read {params['image']}")
    score = float(cv2.Laplacian(img.astype(np.float64), cv2.CV_64F).var())
    ctx.log(f"laplacian variance {score:.2f}")
    return {"sharpness": score, "blurry": score < float(params.get("threshold", 50.0))}
'''
SHARP_TEST = '''import cv2
import numpy as np
from main import run

from luma_engine.toolkit import make_test_ctx


def test_sharp_beats_blurred(tmp_path):
    rng = np.random.default_rng(0)
    img = (rng.random((64, 64)) * 255).astype(np.uint8)
    cv2.imwrite(str(tmp_path / "sharp.png"), img)
    cv2.imwrite(str(tmp_path / "soft.png"), cv2.GaussianBlur(img, (0, 0), 3))
    ctx = make_test_ctx(tmp_path, workspace=tmp_path)
    a, b = run({"image": "sharp.png"}, ctx), run({"image": "soft.png"}, ctx)
    assert a["sharpness"] > 10 * b["sharpness"] and b["blurry"] and not a["blurry"]
'''
SHARP_MANIFEST = {"description": "Scores image sharpness as the variance of the Laplacian (focus measure) and flags blurry frames.",
                  "parameters": {"type": "object", "properties": {"image": {"type": "string"}, "threshold": {"type": "number", "default": 50}},
                                 "required": ["image"]},
                  "returns": {"type": "object", "properties": {"sharpness": {"type": "number"}, "blurry": {"type": "boolean"}},
                              "required": ["sharpness", "blurry"]},
                  "tags": ["qc", "sharpness", "focus"], "timeout_s": 60}
SHARP_README = ("# laplacian_sharpness\n\nScores how sharp an image is (variance of the Laplacian) so preview frames that came out soft "
                "are caught before delivery.\n\nExample: {\"image\": \"work/previews/f_0120.png\"} → {sharpness: 812.4, blurry: false}\n\n"
                "Limitations: depends on content; compare frames of the same shot.\n")
SKILL = ("---\nname: soft_frame_detection\ndescription: Catching soft (out of focus / over-blurred) preview frames before the final render.\n"
         "tags: [qc, sharpness]\ntools_used: [laplacian_sharpness]\n---\n## When to use\nAfter motion-blur or DoF changes.\n\n"
         "## Steps\n1. Extract frames at the fastest moments.\n2. Run laplacian_sharpness on each; compare with the hold frame.\n\n"
         "## Pitfalls\n- Flat brand backgrounds have near-zero variance: score only the region with the mark.\n\n"
         "## Verification\n- Hold frame sharpness within 10 % of the end card.\n\n## Example\nf_0120: 812 vs end card 840 → OK.\n")


def test_mocked_lifecycle_across_two_projects(server, booted):
    c = login_client(server, "toolsmith", "a long password 123")
    pa = c.post("/api/projects", json={"name": "Alpha brand"}).json()
    pb = c.post("/api/projects", json={"name": "Beta brand"}).json()
    pdir_a = Path(os.environ["LUMA_DATA_DIR"]) / "projects" / pa["id"]
    import cv2
    import numpy as np

    (pdir_a / "work").mkdir(exist_ok=True)
    cv2.imwrite(str(pdir_a / "work/frame.png"), (np.random.default_rng(1).random((48, 48)) * 255).astype(np.uint8))

    def after_register(body):
        return {"tool_calls": [call("laplacian_sharpness", image="work/frame.png")]}

    script_a = [
        {"text": "Is there a sharpness checker already?", "tool_calls": [
            call("todo_write", items=[{"title": "Check preview frames for softness", "acceptance_criteria": "sharpness measured"}]),
            call("toolbox_search", query="laplacian variance focus measure")]},
        {"tool_calls": [call("script_write", path="sharpness.py", code="import cv2, sys\nimg = cv2.imread(sys.argv[1], 0)\n"
                                                                        "print(cv2.Laplacian(img, cv2.CV_64F).var())\n")]},
        {"tool_calls": [call("script_run", path="sharpness.py", args=["work/frame.png"])]},
        {"text": "Useful again — making it a tool.", "tool_calls": [call("tool_create", name="laplacian_sharpness", manifest=SHARP_MANIFEST,
                                                                        main_py=SHARP_MAIN, test_py=SHARP_TEST, readme=SHARP_README)]},
        {"tool_calls": [call("tool_register", name="laplacian_sharpness")]},
        after_register,
        {"tool_calls": [call("skill_write", name="soft_frame_detection", content=SKILL)]},
        {"tool_calls": [call("tool_promote", name="laplacian_sharpness", reason="every project previews frames")]},
        {"text": "Promoted — every project can use it now."},
    ]
    with MockServer(script_a) as mock:
        ra = start(c, pa, mock, text="check the preview frames for softness")
        from conftest import run_status

        try:
            q = wait_for(lambda: next((x for x in c.get(f"/api/runs/{ra}/requests", params={"status": "pending"}).json()
                                       if x["kind"] == "approval"), None), timeout=120)
        except AssertionError:
            outs = [m["content"][:1500] for m in mock.state["requests"][-1]["messages"] if m["role"] == "tool"]
            raise AssertionError(f"no promotion approval; status {run_status(c, ra)}; tool results so far: {outs}") from None
        assert q["payload"]["kind"] == "tool_promotion" and q["payload"]["tool"] == "laplacian_sharpness"
        assert any(d["file"] == "main.py" and "Laplacian" in d["diff"] for d in q["payload"]["diff"])
        assert q["payload"]["tests"]["status"] == "passed" and "variance of the Laplacian" in q["payload"]["readme"]
        c.post(f"/api/runs/{ra}/requests/{q['id']}/answer", json={"choice": "Approve"})
        finished(c, ra)
        reqs = mock.state["requests"]
        outs = [r for n, r in results(reqs[-1]) if n != "todo_write"]
    assert run_status(c, ra) in ("idle", "completed")
    assert outs[0].startswith("No toolbox matches"), outs[0]
    assert outs[2].startswith("exit 0")
    assert "tests: passed" in outs[3]
    assert "New tool available: laplacian_sharpness" in outs[4]
    # the dynamic registry: the tool was not offered before registering, and is offered right after
    assert "laplacian_sharpness" not in tool_names(reqs[4]) and "laplacian_sharpness" in tool_names(reqs[5])
    assert '"sharpness"' in outs[5] and '"blurry": false' in outs[5]
    assert "skill soft_frame_detection written" in outs[6]
    assert "now GLOBAL v1.0.0" in outs[7] and "approved by the user" in outs[7]
    types = [e["type"] for e in events(c, ra)]
    for t in ("tool_created", "tool_registered", "skill_written", "tool_promoted", "approval_request"):
        assert t in types, t
    created = next(e["data"] for e in events(c, ra) if e["type"] == "tool_created")
    assert created["test"]["status"] == "passed" and any(d["file"] == "main.py" for d in created["diff"])
    # the script and the tool are versioned in the project's tools repo; the promotion is audited
    from app.toolbox import store

    log = store.project_repo(pa["id"]).git("log", "--format=%s")
    assert "Add script sharpness.py" in log and "Create tool laplacian_sharpness v1.0.0 (agent)" in log and "promoted to the global toolbox" in log
    assert any(a["action"] == "promote" and a["target"] == "tool:laplacian_sharpness" and a["run_id"] == ra
               for a in c.get("/api/toolbox/audit").json())
    assert not (pdir_a / "work/tools/laplacian_sharpness").exists()
    # --- a second project finds and reuses it
    pdir_b = Path(os.environ["LUMA_DATA_DIR"]) / "projects" / pb["id"]
    (pdir_b / "work").mkdir(exist_ok=True)
    cv2.imwrite(str(pdir_b / "work/f.png"), cv2.GaussianBlur((np.random.default_rng(2).random((48, 48)) * 255).astype(np.uint8), (0, 0), 3))
    script_b = [
        {"tool_calls": [call("toolbox_search", query="is this frame blurry? sharpness")]},
        {"tool_calls": [call("toolbox_call", name="laplacian_sharpness", params={"image": "work/f.png"})]},
        {"text": "That frame is soft."},
    ]
    with MockServer(script_b) as mock:
        rb = start(c, pb, mock, text="is the frame soft?")
        finished(c, rb)
        outs = [m["content"] for m in mock.state["requests"][-1]["messages"] if m["role"] == "tool"]
    assert "tool **laplacian_sharpness** v1.0.0 (global, enabled" in outs[0]
    assert '"blurry": true' in outs[1]
    st = c.get("/api/toolbox/tools/global/laplacian_sharpness").json()
    assert st["stats"]["calls"] >= 2 and ra in st["stats"]["runs"] and rb in st["stats"]["runs"]
    assert st["author"] == "agent" and st["created_from_run"] == ra
