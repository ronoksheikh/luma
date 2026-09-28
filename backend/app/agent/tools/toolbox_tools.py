"""The self-extending toolbox: search / read / call tools, quick scripts, and the tool lifecycle
(create → test → register → update → promote), engine plugins, templates and skills.

Registered toolbox tools also appear as first-class tools (see ``toolbox_function_tools``):
the loop refreshes the tool list every step, so a tool registered now is visible next step.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from ... import db, plan
from ...config import config
from ...events import bus
from ...secrets_store import redact
from ...terminal import sandbox_argv, sandbox_env, sandbox_python
from ...toolbox import execute as X
from ...toolbox import lifecycle as L
from ...toolbox import manifest as M
from ...toolbox import plugins as P
from ...toolbox import skills as K
from ...toolbox import store
from .base import REGISTRY, Tool, ToolContext, ToolError, ToolOutput, tool, truncate

RPC_BUILTINS = {"media_probe", "extract_palette", "inspect_asset", "image_stats"}
MAX_RPC_DEPTH = 3
SEARCHED: set[str] = set()  # runs that called toolbox_search (reuse before build)


def _actor(ctx: ToolContext) -> str:
    return f"agent:{ctx.run_id}"


def _searched(run_id: str) -> bool:
    if run_id in SEARCHED:
        return True
    from sqlalchemy import text

    with db.session() as s:
        n = s.execute(text("SELECT COUNT(*) FROM events WHERE run_id=:r AND type='tool_call_start' AND json_extract(data, '$.name') "
                           "IN ('toolbox_search', 'skill_search')"), {"r": run_id}).scalar()
    if n:
        SEARCHED.add(run_id)
    return bool(n)


def _require_search(ctx: ToolContext, what: str) -> None:
    if not _searched(ctx.run_id):
        raise ToolError(f"Reuse before build: call toolbox_search (and skill_search) for this sub-task before {what}. "
                        "If an existing tool or template fits, use it instead of writing new code.")


def _row(ctx: ToolContext, name: str, scope: str | None = None) -> db.ToolboxTool:
    store.sync(ctx.project_id, L.reserved_names())
    r = store.get(scope, ctx.project_id if scope == "project" else None, name) if scope else store.resolve(name, ctx.project_id)
    if r is None:
        raise ToolError(f"no toolbox tool named {name!r}{' in ' + scope + ' scope' if scope else ''} — toolbox_search / toolbox_list show what exists")
    return r


def _stats_line(t: dict) -> str:
    st = t.get("stats") or {}
    calls = st.get("calls") or 0
    rate = f"{st['success_rate'] * 100:.0f}% ok" if st.get("success_rate") is not None else "never called"
    return f"{calls} call(s), {rate}" + (f", avg {st['avg_s']}s" if st.get("avg_s") else "")


# ================================================================================ discovery
@tool(
    "toolbox_search",
    """Full-text search over the toolbox: tools (global + this project's), skills (playbooks) and engine plugins/templates —
    names, descriptions, READMEs and tags. CALL THIS BEFORE WRITING NEW CODE for any non-trivial sub-task (reuse before
    build). Returns the best matches with usage stats and last test status.""",
    {"query": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}},
     "scope": {"type": "string", "enum": ["global", "project", "all"], "default": "all"},
     "kinds": {"type": "array", "items": {"type": "string", "enum": ["tool", "skill", "plugin"]}}},
    ["query"],
)
async def toolbox_search(ctx: ToolContext, a: dict) -> ToolOutput:
    store.sync(ctx.project_id, L.reserved_names())
    SEARCHED.add(ctx.run_id)
    kinds = tuple(a.get("kinds") or ("tool", "skill", "plugin"))
    q = str(a["query"]) + " " + " ".join(a.get("tags") or [])
    hits = store.search(q, ctx.project_id, kinds, limit=12)
    scope = a.get("scope") or "all"
    tags = set(a.get("tags") or [])
    lines, results = [], []
    eff = store.effective(ctx.project_id)
    for h in hits:
        if h["kind"] == "tool":
            with db.session() as s:
                r = s.get(db.ToolboxTool, h["ref"])
            if r is None or (scope != "all" and r.scope != scope) or (tags and not tags & set(r.tags or [])):
                continue
            if eff.get(r.name) is not None and eff[r.name].id != r.id:
                continue  # shadowed by the project's tool of the same name
            t = store.public(r)
            results.append({"kind": "tool", "name": r.name, "scope": r.scope, "version": r.version, "status": t["status"], "test_status": r.test_status})
            lines.append(f"- tool **{r.name}** v{r.version} ({r.scope}, {t['status']}, tests {r.test_status}; {_stats_line(t)}): {r.description}")
        elif h["kind"] == "skill":
            results.append({"kind": "skill", "name": h["name"]})
            with db.session() as s:
                sk = s.get(db.Skill, h["ref"])
            lines.append(f"- skill **{h['name']}**: {sk.description if sk else ''} → skill_read")
        else:
            p = P.get(h["name"])
            if p is None:
                continue
            results.append({"kind": "plugin", "name": p.name, "plugin_kind": p.kind})
            lines.append(f"- {p.kind} plugin **{p.name}** v{p.version} ({'enabled' if p.enabled else 'disabled'}): {p.description} "
                         f"→ `from luma_engine.plugins import {p.name}`")
    if not lines:
        body = (f"No toolbox matches for {a['query']!r}. Nothing to reuse: explore in work/scripts/ (script_write + script_run); if the "
                "code turns out reusable, turn it into a tool (tool_create).")
    else:
        body = f"{len(lines)} match(es) for {a['query']!r} (best first):\n" + "\n".join(lines) + \
            "\nRegistered tools can be called directly by name (or via toolbox_call); toolbox_read shows manifest, README and source."
    return ToolOutput(body, ui={"results": results})


@tool(
    "toolbox_list",
    "List toolbox tools (global and/or this project's) with status, version and usage stats.",
    {"scope": {"type": "string", "enum": ["global", "project", "all"], "default": "all"}},
)
async def toolbox_list(ctx: ToolContext, a: dict) -> ToolOutput:
    store.sync(ctx.project_id, L.reserved_names())
    scope = a.get("scope") or "all"
    rows = store.rows(ctx.project_id, None if scope == "all" else scope)
    if not rows:
        return ToolOutput("The toolbox has no tools in that scope yet.")
    ov = set(store.overrides(ctx.project_id))
    lines = []
    for r in rows:
        t = store.public(r)
        note = " (overrides the global tool)" if r.scope == "project" and r.name in ov else " (shadowed by a project tool)" if r.name in ov else ""
        lines.append(f"- {r.name} v{r.version} [{r.scope}{note}] {t['status']}, tests {r.test_status}, {_stats_line(t)} — {r.description[:160]}")
    return ToolOutput("\n".join(lines))


@tool(
    "toolbox_read",
    "Read a toolbox tool: manifest (tool.yaml), README, source (main.py), tests and fixtures list.",
    {"name": {"type": "string"}, "scope": {"type": "string", "enum": ["global", "project"]}},
    ["name"],
)
async def toolbox_read(ctx: ToolContext, a: dict) -> ToolOutput:
    r = _row(ctx, a["name"], a.get("scope"))
    f = L.read(r)
    t = store.public(r)
    fx = ", ".join(f"{x['name']} ({x['size']} B)" for x in f["fixtures"]) or "none"
    body = (f"# {r.name} v{r.version} ({r.scope}, {t['status']}, tests {r.test_status}; {_stats_line(t)})\n\n## tool.yaml\n{f.get('tool.yaml', '')}\n"
            f"## README.md\n{f.get('README.md', '')}\n\n## main.py\n```python\n{f.get('main.py', '')}\n```\n\n## test_tool.py\n```python\n"
            f"{f.get('test_tool.py', '')}\n```\nfixtures: {fx}")
    return ToolOutput(truncate(body, ctx, "toolbox_read"))


# ================================================================================ calling
async def call_toolbox_tool(ctx: ToolContext, name: str, params: dict, allow_deprecated: bool = False, source: str = "agent") -> ToolOutput:
    row = store.resolve(name, ctx.project_id)
    if row is None:
        store.sync(ctx.project_id, L.reserved_names())
        row = store.resolve(name, ctx.project_id)
    if row is None:
        raise ToolError(f"no toolbox tool named {name!r} — toolbox_search finds tools")
    d = config.data_dir / row.path
    h = store.files_hash(d) if d.exists() else None
    if h != row.files_hash:
        row = store._upsert_from_dir(row.scope, row.project_id, d, L.reserved_names())
    status = store.status_of(row)
    warn = ""
    if status == "deprecated":
        dep = row.deprecated or {}
        if not allow_deprecated:
            raise ToolError(f"{name} is deprecated ({dep.get('reason')}); " + (f"use {dep['replacement']} instead" if dep.get("replacement")
                            else "call it through toolbox_call if you really need it"))
        warn = f"⚠ {name} is deprecated ({dep.get('reason')}).\n"
    elif status == "modified":
        raise ToolError(f"{name}'s files changed after its tests passed — run tool_test / tool_update so it is re-tested before use")
    elif status != "enabled":
        raise ToolError(f"{name} is {status} (not registered or failing) — tool_test then tool_register it, or fix it with tool_update")
    ctx.output(f"🧰 {row.name} v{row.version} ({row.scope})\n")

    def on_log(line: str) -> None:
        ctx.output(line + "\n")

    def on_progress(pct: float, msg: str) -> None:
        ctx.emit("progress", {"stage": "tool", "frame": round(pct), "total": 100, "message": msg})

    async def rpc(cname: str, cparams: dict):
        if ctx.agent is None:
            raise ToolError("call_tool is only available inside an agent run")
        return await ctx.agent.invoke(cname, cparams, ctx)

    res = await X.run_tool(row, params, ctx.pdir, call_id=(ctx.tool_call_id or "call")[-24:].replace("/", "_"), on_log=on_log,
                           on_progress=on_progress, rpc=rpc)
    if res.kind == "params":
        raise ToolError(res.error)
    st = store.record_call(row, ctx.run_id, ctx.tool_call_id, res.ok, res.duration, res.error if not res.ok else None, source, ctx.project_id)
    if st.get("auto_disabled"):
        _auto_disabled(ctx, row)
    imgs = [ctx.rel(Path(p)) for p in res.images]
    out_rel = ctx.rel(res.out_dir) if res.out_dir and res.out_dir.exists() else None
    if not res.ok:
        tb = f"\n{res.traceback[-2500:]}" if res.traceback else ""
        more = f"\n⚠ {row.name} failed {st.get('fail_streak')} time(s) in a row" + (" and was disabled." if st.get("auto_disabled") else ".")
        return ToolOutput(f"Error: {res.error}{tb}{more if st.get('fail_streak', 0) > 1 else ''}", "error",
                          ui={"toolbox": {"name": row.name, "version": row.version, "scope": row.scope}, "logs": res.logs[-40:]})
    body = json.dumps(res.result, indent=1, default=str)
    msg = f"{warn}{row.name} v{row.version} → ({res.duration:.2f}s)\n{truncate(body, ctx, row.name)}"
    if out_rel:
        msg += f"\nfiles: {out_rel}/"
    if imgs:
        msg += "\nimages: " + ", ".join(imgs)
    for p in imgs[:6]:
        ctx.emit("image", {"path": p, "url": ctx.url(p), "caption": f"{row.name}"})
    return ToolOutput(msg, images=imgs[:4], ui={"toolbox": {"name": row.name, "version": row.version, "scope": row.scope}, "result": res.result,
                                               "images": [ctx.url(p) for p in imgs[:12]], "out_dir": out_rel})


def _auto_disabled(ctx: ToolContext, row: db.ToolboxTool) -> None:
    traces = store.failing_traces(row.id)
    detail = "\n\n".join(f"#{i + 1}: {t[:1500]}" for i, t in enumerate(traces))
    todo = None
    try:
        todo = plan.add(ctx.run_id, ctx.project_id, {"title": f"Fix tool {row.name}", "priority": "high", "status": "blocked",
                                                      "detail": f"{row.name} v{row.version} ({row.scope}) failed {store.FAIL_LIMIT} times in a row and was "
                                                                f"disabled. Last errors:\n{detail}",
                                                      "acceptance_criteria": f"tool_update fixes {row.name}; its tests cover the failing case; tool_register succeeds"},
                        None, "system")
    except plan.PlanError:
        pass
    msg = f"Tool {row.name} was disabled after {store.FAIL_LIMIT} failures in a row"
    with db.session() as s:
        n = db.Notification(project_id=ctx.project_id, run_id=ctx.run_id, level="warning", message=msg)
        s.add(n)
        s.flush()
        nid = n.id
    ctx.emit("notify", {"id": nid, "message": msg, "level": "warning"})
    ctx.emit("tool_disabled", {"name": row.name, "scope": row.scope, "version": row.version, "reason": "failed 3 times in a row", "traces": traces,
                               "todo_id": todo["id"] if todo else None})
    if row.scope == "global":
        store.audit(_actor(ctx), "auto_disable", f"tool:{row.name}", ctx.run_id, {"traces": [t[:500] for t in traces]})


@tool(
    "toolbox_call",
    """Call any registered toolbox tool by name with its params (use when the tool is not in your function list — only the
    most relevant toolbox tools are listed each step). Params are validated against the tool's JSON Schema.""",
    {"name": {"type": "string"}, "params": {"type": "object"}},
    ["name"],
)
async def toolbox_call(ctx: ToolContext, a: dict) -> ToolOutput:
    return await call_toolbox_tool(ctx, str(a["name"]), a.get("params") or {}, allow_deprecated=True)


def toolbox_function_tools(project_id: str, run_id: str, focus: str, limit: int, pinned: set[str]) -> dict[str, Tool]:
    """The toolbox tools sent to the model this step: those registered/used in this run, the most relevant to the
    current focus (FTS over names, descriptions, READMEs, tags) and the most used — at most ``limit``."""
    rows = store.callable_rows(project_id)
    if not rows or limit <= 0:
        return {}
    chosen: list[str] = [n for n in rows if n in pinned]
    for h in store.search(focus, project_id, ("tool",), limit=limit * 2):
        if h["name"] in rows and h["name"] not in chosen:
            chosen.append(h["name"])
    for n, _ in sorted(rows.items(), key=lambda kv: -((kv[1].stats or {}).get("calls") or 0)):
        if n not in chosen:
            chosen.append(n)
    out = {}
    for n in chosen[:limit]:
        r = rows[n]
        fs = M.function_schema(r.manifest)["function"]

        async def handler(ctx: ToolContext, args: dict, _n=n) -> ToolOutput:
            return await call_toolbox_tool(ctx, _n, args)

        out[n] = Tool(n, fs["description"], fs["parameters"], handler)
    return out


# ================================================================================ scripts
def _script_path(ctx: ToolContext, path: str) -> Path:
    p = str(path).strip().lstrip("/")
    if not p.startswith("work/scripts/"):
        p = "work/scripts/" + p.removeprefix("scripts/")
    q = ctx.resolve(p)
    base = (ctx.pdir / "work" / "scripts").resolve()
    if base not in q.parents:
        raise ToolError("scripts live in work/scripts/")
    if q.suffix not in (".py", ".sh"):
        raise ToolError("scripts must be .py or .sh files")
    return q


@tool(
    "script_write",
    """Write a quick one-off script into work/scripts/ (project scope; the default place for exploratory code). Versioned in
    the project's tools repo. Run it with script_run. If it becomes reusable (parameterisable, needed again, >~40 lines or
    solves a tricky problem), turn it into a tool with tool_create.""",
    {"path": {"type": "string", "description": "e.g. measure_lockup.py (placed in work/scripts/)"}, "code": {"type": "string"}},
    ["path", "code"],
)
async def script_write(ctx: ToolContext, a: dict) -> ToolOutput:
    _require_search(ctx, "writing a script")
    p = _script_path(ctx, a["path"])
    existed = p.exists()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(a["code"])
    try:
        os.chmod(p, 0o775 if p.suffix == ".sh" else 0o664)
    except OSError:
        pass
    L._commit("project", ctx.project_id, f"{'Update' if existed else 'Add'} script {p.name}", _actor(ctx))
    n = a["code"].count("\n") + 1
    hint = " It is over 40 lines — if it will be needed again, make it a tool (tool_create)." if n > 40 else ""
    return ToolOutput(f"{'updated' if existed else 'wrote'} {ctx.rel(p)} ({n} lines).{hint}")


@tool(
    "script_run",
    "Run a script from work/scripts/ in the sandbox (python for .py, bash for .sh), cwd = the project; output streams live.",
    {"path": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}}, "timeout_s": {"type": "number", "default": 300}},
    ["path"],
)
async def script_run(ctx: ToolContext, a: dict) -> ToolOutput:
    p = _script_path(ctx, a["path"])
    if not p.exists():
        raise ToolError(f"{ctx.rel(p)} does not exist — script_write it first")
    timeout = max(1.0, min(1800.0, float(a.get("timeout_s") or 300)))
    env = sandbox_env(str(ctx.pdir), {"PYTHONDONTWRITEBYTECODE": "1"})
    cmd = [sandbox_python(), str(p)] if p.suffix == ".py" else ["bash", str(p)]
    argv = sandbox_argv(cmd + [str(x) for x in a.get("args") or []], env)
    t0 = time.monotonic()
    proc = await asyncio.create_subprocess_exec(*argv, cwd=str(ctx.pdir), env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                                                start_new_session=True)
    chunks: list[str] = []

    async def pump():
        while True:
            b = await proc.stdout.read(4096)
            if not b:
                break
            s = redact(b.decode(errors="replace"))
            chunks.append(s)
            ctx.output(s)

    try:
        await asyncio.wait_for(asyncio.gather(pump(), proc.wait()), timeout)
    except asyncio.TimeoutError:
        X._kill(proc)
        raise ToolError(f"{ctx.rel(p)} timed out after {timeout:.0f}s. Output so far:\n{''.join(chunks)[-3000:]}") from None
    except asyncio.CancelledError:
        X._kill(proc, graceful=True)
        raise
    out = "".join(chunks)
    return ToolOutput(f"exit {proc.returncode} in {time.monotonic() - t0:.1f}s\n{truncate(out, ctx, 'script')}",
                      "success" if proc.returncode == 0 else "error")


# ================================================================================ lifecycle
def _fmt_test(t: dict | None) -> str:
    if not t:
        return ""
    return f"tests: {t.get('status')} ({t.get('passed', 0)} passed, {t.get('failed', 0)} failed)"


@tool(
    "tool_create",
    """Scaffold a PROJECT-scoped toolbox tool (promote it later with tool_promote): writes tool.yaml (manifest: description,
    parameters/returns JSON Schemas, pinned dependencies, timeout_s, resources, network, produces_files, tags), main.py
    (`def run(params, ctx) -> dict`; ctx has workspace, out_dir, out(name), path(p), log(), progress(pct, msg), emit_image(path),
    cancelled(), call_tool(name, params)), test_tool.py (pytest with small fixtures; `from luma_engine.toolkit import make_test_ctx`)
    and README.md; auto-formats and lints (ruff), installs dependencies into the persistent venv, runs the tests.
    Call toolbox_search first. Then tool_register.""",
    {"name": {"type": "string", "description": "snake_case, unique"},
     "manifest": {"type": "object", "description": "tool.yaml fields (name/scope/version are filled in: version 1.0.0)"},
     "main_py": {"type": "string"}, "test_py": {"type": "string"}, "readme": {"type": "string"},
     "fixtures": {"type": "object", "description": "{file name: {text} | {base64} | {from: workspace path}} — ≤ 2 MB in total"},
     "scope": {"type": "string", "enum": ["project"], "default": "project"}},
    ["name", "manifest", "main_py", "test_py", "readme"],
)
async def tool_create(ctx: ToolContext, a: dict) -> ToolOutput:
    _require_search(ctx, "creating a tool")
    if (a.get("scope") or "project") != "project":
        raise ToolError("agents create tools in project scope; use tool_promote to make one global (the user approves it)")
    try:
        res = await L.create("project", ctx.project_id, str(a["name"]), a.get("manifest") or {}, a["main_py"], a["test_py"], a["readme"],
                             a.get("fixtures"), "agent", ctx.run_id, _actor(ctx), ctx.pdir)
    except (L.ToolboxError, M.ManifestError) as e:
        raise ToolError(str(e)) from None
    t, sc = res["test"], res["scan"]
    ctx.emit("tool_created", {"name": a["name"], "scope": "project", "version": res["tool"]["version"], "diff": res["diff"], "test": _short_test(t),
                              "scan": sc, "deps": res["deps"], "description": res["tool"]["description"]})
    lines = [f"created project tool {a['name']} v{res['tool']['version']} in work/tools/{a['name']}/", _fmt_test(t)]
    if sc["formatted"]:
        lines.append("ruff formatted: " + ", ".join(sc["formatted"]))
    if sc["blocking"]:
        lines.append("static checks BLOCK registration:\n" + "\n".join(f"  {f['file']}:{f['line']} [{f['rule']}] {f['message']}" for f in sc["blocking"]))
    if sc["warnings"]:
        lines.append("lint warnings:\n" + "\n".join(f"  {f['file']}:{f['line']} [{f['rule']}] {f['message']}" for f in sc["warnings"][:10]))
    if not res["deps"]["ok"]:
        lines.append("dependency install FAILED:\n" + res["deps"]["output"][-1500:])
    if t["status"] != "passed":
        lines.append("test output:\n" + t.get("output", "")[-3000:])
        lines.append("Fix it with tool_update, then tool_register.")
    else:
        lines.append(f"Next: tool_register(name={a['name']!r}) to call it as a first-class tool.")
    return ToolOutput("\n".join(x for x in lines if x), "success" if t["status"] == "passed" else "error")


def _short_test(t: dict | None) -> dict:
    return {k: (v[-6000:] if k == "output" and isinstance(v, str) else v) for k, v in (t or {}).items()}


@tool("tool_test", "Run a toolbox tool's pytest suite (in the sandbox, with the tool venv); returns a pass/fail summary.",
      {"name": {"type": "string"}, "scope": {"type": "string", "enum": ["global", "project"]}}, ["name"])
async def tool_test(ctx: ToolContext, a: dict) -> ToolOutput:
    r = _row(ctx, a["name"], a.get("scope"))
    t = await L.test(r)
    ctx.emit("tool_tested", {"name": r.name, "scope": r.scope, "version": r.version, "test": _short_test(t)})
    extra = "\n⚠ it was registered, so it has been disabled until its tests pass again" if t.get("disabled") else ""
    return ToolOutput(f"{r.name} v{r.version}: {_fmt_test(t)}\n{t.get('output', '')[-4000:]}{extra}", "success" if t["status"] == "passed" else "error")


@tool("tool_register", """Make a tested toolbox tool callable as a first-class tool in this run (from your next step) and in all
      future runs in its scope. Refused unless the manifest validates, the static checks pass and the tests pass.""",
      {"name": {"type": "string"}, "scope": {"type": "string", "enum": ["global", "project"]}}, ["name"])
async def tool_register(ctx: ToolContext, a: dict) -> ToolOutput:
    r = _row(ctx, a["name"], a.get("scope"))
    try:
        res = await L.register(r, ctx.run_id, _actor(ctx))
    except L.ToolboxError as e:
        raise ToolError(str(e)) from None
    ctx.runner.toolbox_pinned.setdefault(ctx.run_id, set()).add(r.name)
    t = res["tool"]
    ctx.emit("tool_registered", {"name": r.name, "scope": r.scope, "version": t["version"], "description": t["description"], "tag": res["tag"]})
    params = json.dumps((t.get("manifest") or r.manifest or {}).get("parameters", {}).get("properties", {}))[:600]
    return ToolOutput(f"🧰 New tool available: {r.name} v{t['version']} ({r.scope}). Call it directly as `{r.name}` from your next step "
                      f"(params: {params}).")


@tool(
    "tool_update",
    """Change a toolbox tool: pass any of main_py / test_py / readme / manifest (partial, merged) / fixtures / remove_fixtures.
    The version is bumped (bump: patch|minor|major), tests re-run, and a registered tool is re-registered when they pass
    (disabled otherwise). Earlier versions stay tagged (<tool>@<version>) — tool_rollback restores one.""",
    {"name": {"type": "string"}, "scope": {"type": "string", "enum": ["global", "project"]}, "main_py": {"type": "string"},
     "test_py": {"type": "string"}, "readme": {"type": "string"}, "manifest": {"type": "object"}, "fixtures": {"type": "object"},
     "remove_fixtures": {"type": "array", "items": {"type": "string"}}, "bump": {"type": "string", "enum": ["patch", "minor", "major"], "default": "patch"},
     "reason": {"type": "string"}},
    ["name", "reason"],
)
async def tool_update(ctx: ToolContext, a: dict) -> ToolOutput:
    r = _row(ctx, a["name"], a.get("scope"))
    if r.scope == "global":
        raise ToolError(f"{r.name} is a global tool; agents change global tools by improving a project copy and promoting it. "
                        f"Create a project tool with the same name (it overrides the global one here), then tool_promote it.")
    changes = {k: a[k] for k in ("main_py", "test_py", "readme", "manifest", "fixtures", "remove_fixtures") if k in a}
    try:
        res = await L.update(r, changes, a.get("bump") or "patch", str(a.get("reason") or ""), ctx.run_id, _actor(ctx), ctx.pdir)
    except (L.ToolboxError, M.ManifestError) as e:
        raise ToolError(str(e)) from None
    ctx.emit("tool_updated", {"name": r.name, "scope": r.scope, "version": res["version"], "previous": res["previous"], "diff": res["diff"],
                              "test": _short_test(res["test"]), "registered": res["registered"], "reason": a.get("reason")})
    t = res["test"]
    msg = f"{r.name} {res['previous']} → {res['version']}: {_fmt_test(t)}"
    if res["registered"]:
        msg += "; re-registered"
    elif res["was_enabled"]:
        msg += "; DISABLED until its tests pass (previous versions stay tagged — tool_rollback)"
    if res["scan"]["blocking"]:
        msg += "\nstatic checks:\n" + "\n".join(f"  {f['file']}:{f['line']} [{f['rule']}] {f['message']}" for f in res["scan"]["blocking"])
    if t["status"] != "passed":
        msg += "\n" + t.get("output", "")[-3000:]
    return ToolOutput(msg, "success" if t["status"] == "passed" else "error")


@tool("tool_rollback", "Restore an earlier version of a project toolbox tool (re-tested and re-registered as a new patch version).",
      {"name": {"type": "string"}, "version": {"type": "string"}}, ["name", "version"])
async def tool_rollback(ctx: ToolContext, a: dict) -> ToolOutput:
    r = _row(ctx, a["name"], "project")
    try:
        res = await L.rollback(r, str(a["version"]), ctx.run_id, _actor(ctx))
    except L.ToolboxError as e:
        raise ToolError(str(e)) from None
    ctx.emit("tool_updated", {"name": r.name, "scope": r.scope, "version": res["version"], "previous": r.version, "diff": res["diff"],
                              "test": _short_test(res["test"]), "registered": res["registered"], "reason": f"rollback to {a['version']}"})
    return ToolOutput(f"{r.name} restored from v{a['version']} as v{res['version']}: {_fmt_test(res['test'])}"
                      f"{'; registered' if res['registered'] else ''}")


@tool(
    "tool_promote",
    """Promote a project tool to the GLOBAL toolbox (every project can use it). A static scan blocks project-specific values
    (paths, asset names, brand colours/names, secrets) — parameterize them first. The user approves the promotion (the
    card shows the diff, tests and README) unless Autopilot + 'allow global promotion' are on.""",
    {"name": {"type": "string"}, "reason": {"type": "string", "description": "why other projects need it"}},
    ["name"],
)
async def tool_promote(ctx: ToolContext, a: dict) -> ToolOutput:
    r = _row(ctx, a["name"], "project")
    found, rep = await asyncio.to_thread(L.promotion_findings, r)
    if found or not rep.ok:
        lines = [f"- {f.file}:{f.line} [{f.rule}] {f.message}" for f in found + rep.blocking]
        raise ToolError(f"{r.name} cannot be promoted yet — it contains project-specific values or unsafe code:\n" + "\n".join(lines) +
                        "\nParameterize them (take paths, colours and names as params without brand-specific defaults), tool_update, then promote again.")
    prev = L.promotion_preview(r)
    auto = bool(ctx.settings.get("project", {}).get("autopilot")) and bool(ctx.settings.get("allow_global_promotion"))
    if not auto:
        payload = {"title": f"Promote tool {r.name} v{r.version} to the global toolbox", "kind": "tool_promotion",
                   "summary": (f"{r.description}\n\nWhy: {a.get('reason') or '—'}\n\nEvery project will be able to call it"
                               + (f"; it replaces global v{prev['replaces_global']}." if prev["replaces_global"] else ".")),
                   "diff": prev["diff"], "readme": prev["readme"][:12000], "tests": prev["tests"], "tool": r.name,
                   "artifacts": [], "choices": ["Approve", "Keep it in this project"]}
        ans = await ctx.runner.request_user(ctx.run_id, "approval", payload, ctx.tool_call_id)
        if ans.get("choice") != "Approve":
            note = str(ans.get("note") or ans.get("text") or "").strip()
            return ToolOutput(f"NOT PROMOTED — the user chose “{ans.get('choice') or 'no'}”.{(' ' + note) if note else ''} {r.name} stays a project tool.")
    try:
        res = await L.promote(r, ctx.run_id, _actor(ctx))
    except L.ToolboxError as e:
        raise ToolError(str(e)) from None
    ctx.emit("tool_promoted", {"name": r.name, "version": res["version"], "registered": res["registered"], "auto": auto, "diff": res["diff"]})
    return ToolOutput(f"{r.name} is now GLOBAL v{res['version']}{' (Autopilot)' if auto else ' (approved by the user)'}; "
                      f"{_fmt_test(res['test'])}{'; registered for every project' if res['registered'] else ''}.")


@tool("tool_deprecate", "Deprecate a project tool (it leaves the function list; toolbox_call still works with a warning).",
      {"name": {"type": "string"}, "reason": {"type": "string"}, "replacement": {"type": "string"}}, ["name", "reason"])
async def tool_deprecate(ctx: ToolContext, a: dict) -> ToolOutput:
    r = _row(ctx, a["name"])
    if r.scope == "global":
        raise ToolError("global tools are deprecated by the user in the Toolbox (or propose it with ask_user)")
    await L.deprecate(r, str(a["reason"]), a.get("replacement"), ctx.run_id, _actor(ctx))
    ctx.emit("tool_disabled", {"name": r.name, "scope": r.scope, "version": r.version, "reason": f"deprecated: {a['reason']}",
                               "replacement": a.get("replacement")})
    return ToolOutput(f"{r.name} deprecated" + (f"; use {a['replacement']}" if a.get("replacement") else ""))


@tool("tool_delete", "Delete a toolbox tool (project tools directly; deleting a GLOBAL tool needs the user's approval).",
      {"name": {"type": "string"}, "scope": {"type": "string", "enum": ["global", "project"]}, "reason": {"type": "string"}}, ["name", "reason"])
async def tool_delete(ctx: ToolContext, a: dict) -> ToolOutput:
    r = _row(ctx, a["name"], a.get("scope"))
    if r.scope == "global":
        payload = {"title": f"Delete the GLOBAL tool {r.name} v{r.version}?", "summary": f"Reason: {a['reason']}\nEvery project loses it "
                   f"(its history stays in the toolbox repo).", "artifacts": [], "choices": ["Delete", "Keep it"], "tool": r.name}
        ans = await ctx.runner.request_user(ctx.run_id, "approval", payload, ctx.tool_call_id)
        if ans.get("choice") != "Delete":
            return ToolOutput(f"NOT DELETED — the user chose “{ans.get('choice') or 'no'}”.")
    await L.delete(r, ctx.run_id, _actor(ctx))
    ctx.emit("tool_disabled", {"name": r.name, "scope": r.scope, "version": r.version, "reason": f"deleted: {a['reason']}", "deleted": True})
    return ToolOutput(f"deleted {r.scope} tool {r.name}")


# ================================================================================ plugins / templates
@tool(
    "plugin_create",
    """Add a luma_engine extension to the (global) toolbox: kind fx (draw into a Frame, e.g. `def apply(frame, t, **params)`),
    instrument (`def synth(duration, sr=48000, **params) -> np.ndarray`) or qc_check (`def check(video_path, info) -> dict`,
    run by qc_report). Tests must pass before it is enabled. Scene code then uses `from luma_engine.plugins import <name>`.
    Creating an existing name updates it (minor version bump).""",
    {"kind": {"type": "string", "enum": ["fx", "instrument", "qc_check"]}, "name": {"type": "string"}, "code": {"type": "string"},
     "tests": {"type": "string", "description": "pytest; import the plugin with `from luma_engine.plugins import <name>`"},
     "description": {"type": "string"}, "readme": {"type": "string"}},
    ["kind", "name", "code", "tests", "description"],
)
async def plugin_create(ctx: ToolContext, a: dict) -> ToolOutput:
    _require_search(ctx, "creating a plugin")
    try:
        res = await P.create(a["kind"], a["name"], a["code"], a["tests"], a["description"], a.get("readme"), None, ctx.run_id, _actor(ctx))
    except L.ToolboxError as e:
        raise ToolError(str(e)) from None
    ctx.emit("plugin_created", {"name": a["name"], "kind": a["kind"], "version": res["plugin"]["version"], "enabled": res["enabled"],
                                "diff": res["diff"], "test": _short_test(res["test"]), "scan": res["scan"], "api": res["api"]})
    if not res["enabled"]:
        why = res["test"].get("output", "")[-3000:] if res["test"]["status"] != "passed" else \
            "\n".join(f"{f['file']}:{f['line']} [{f['rule']}] {f['message']}" for f in res["scan"]["blocking"])
        return ToolOutput(f"plugin {a['name']} written but NOT enabled ({_fmt_test(res['test'])}):\n{why}\nFix and call plugin_create again.", "error")
    return ToolOutput(f"{a['kind']} plugin {a['name']} v{res['plugin']['version']} enabled — {_fmt_test(res['test'])}. In scene code: "
                      f"`{res['import']}` (API: {', '.join(res['api'])}).")


@tool(
    "template_save",
    """Turn a successful project scene into a reusable, parameterized template (a template plugin with a thumbnail and preview
    video): params_schema lists the parameters that change per brand (logo, colours, fonts, wordmark, timings) — scene
    attributes / build() params — each with a default. Assets referenced by defaults (assets/…) are bundled. It appears in the
    Templates gallery ("Use template" starts a new project) and scenes can `from luma_engine.plugins import <name>`.""",
    {"name": {"type": "string"}, "scene_path": {"type": "string"}, "params_schema": {"type": "object"},
     "preview_video": {"type": "string", "description": "workspace path of a rendered preview (mp4)"},
     "description": {"type": "string"}, "title": {"type": "string"}},
    ["name", "scene_path", "params_schema", "description"],
)
async def template_save(ctx: ToolContext, a: dict) -> ToolOutput:
    try:
        res = await P.template_save(ctx.pdir, ctx.project_id, a["name"], a["scene_path"], a["params_schema"], a.get("preview_video"),
                                    a["description"], a.get("title"), ctx.run_id, _actor(ctx))
    except L.ToolboxError as e:
        raise ToolError(str(e)) from None
    t = res["template"]
    ctx.emit("template_saved", {"name": a["name"], "title": t["title"], "version": res["plugin"]["version"], "enabled": res["enabled"],
                                "thumbnail": f"/api/toolbox/plugins/{a['name']}/file/thumb.png" if t.get("thumbnail") else None,
                                "preview": f"/api/toolbox/plugins/{a['name']}/file/preview.mp4" if t.get("preview") else None,
                                "params": list((a["params_schema"].get("properties") or {}).keys()), "test": _short_test(res["test"])})
    if not res["enabled"]:
        return ToolOutput(f"template {a['name']} saved but NOT enabled — its generated render test failed:\n{res['test'].get('output', '')[-3000:]}", "error")
    return ToolOutput(f"template {a['name']} v{res['plugin']['version']} saved ({t['width']}×{t['height']}, params: "
                      f"{', '.join(a['params_schema'].get('properties', {}))}; bundled assets: {', '.join(t['assets']) or 'none'}). "
                      f"It is in the Templates gallery; scenes can `{res['import']}` and call {a['name']}.build(**params).")


# ================================================================================ skills
@tool(
    "skill_write",
    """Write or update a skill: a playbook of HOW you solved a hard problem, for future runs. content = SKILL.md with YAML
    front-matter (name, description = when to use it, tags, tools_used) and sections ## When to use, ## Steps, ## Pitfalls
    (concrete, e.g. "skia toarray() is unpremultiplied by default; pass alphaType=kPremul"), ## Verification, ## Example.""",
    {"name": {"type": "string"}, "content": {"type": "string"}},
    ["name", "content"],
)
async def skill_write(ctx: ToolContext, a: dict) -> ToolOutput:
    try:
        res = K.write(str(a["name"]), a["content"], "agent", ctx.run_id, _actor(ctx))
    except K.SkillError as e:
        raise ToolError(str(e)) from None
    ctx.emit("skill_written", {"name": a["name"], "created": res["created"], "description": res["skill"]["description"],
                               "tags": res["skill"]["tags"], "diff": res["diff"]})
    return ToolOutput(f"skill {a['name']} {'written' if res['created'] else 'updated'} — future runs see it when relevant")


@tool("skill_read", "Read a skill's full playbook (SKILL.md).", {"name": {"type": "string"}}, ["name"])
async def skill_read(ctx: ToolContext, a: dict) -> ToolOutput:
    try:
        s = K.read(str(a["name"]))
    except K.SkillError as e:
        raise ToolError(str(e)) from None
    return ToolOutput(s["content"])


@tool("skill_search", "Search the skills (playbooks from earlier jobs) by what you are about to do.", {"query": {"type": "string"}}, ["query"])
async def skill_search(ctx: ToolContext, a: dict) -> ToolOutput:
    SEARCHED.add(ctx.run_id)
    hits = K.search(str(a["query"]), 8)
    if not hits:
        return ToolOutput(f"no skills match {a['query']!r}")
    return ToolOutput("\n".join(f"- **{h['name']}** ({', '.join(h['tags'])}): {h['description']}" for h in hits) + "\nskill_read(name) for the full text.")


# ================================================================================ RPC (ctx.call_tool)
async def invoke(agent, name: str, params: dict, parent: ToolContext) -> object:
    """``ctx.call_tool`` from inside a toolbox tool: ElevenLabs tools, other toolbox tools and a few read-only built-ins,
    through the same approval gates and budgets as the agent's own calls. Never exposes keys."""
    if parent.rpc_depth >= MAX_RPC_DEPTH:
        raise ToolError(f"call_tool nesting is limited to {MAX_RPC_DEPTH} levels")
    t = REGISTRY.get(name)
    is_toolbox = t is None and store.resolve(name, parent.project_id) is not None
    if t is not None and not (t.elevenlabs or name in RPC_BUILTINS):
        raise ToolError(f"call_tool may call ElevenLabs tools, other toolbox tools and {sorted(RPC_BUILTINS)} — not {name}")
    if t is not None and name not in agent.base_tools:
        raise ToolError(f"{name} is not available in this run (ElevenLabs not configured?)")
    if t is None and not is_toolbox:
        raise ToolError(f"unknown tool {name}")
    agent._gate(name, params)
    parent.output(f"↳ call_tool {name}({json.dumps(params)[:200]})\n")
    sub = ToolContext(parent.run_id, parent.project_id, parent.pdir, parent.creds, parent.settings, parent.vision, parent.runner,
                      tool_call_id=f"{parent.tool_call_id}~{name}", coalescer=parent.coalescer, el_budget=parent.el_budget, agent=agent,
                      rpc_depth=parent.rpc_depth + 1)
    if t is not None:
        missing = [k for k in t.parameters.get("required", []) if k not in params]
        if missing:
            raise ToolError(f"missing required argument(s) {missing} for {name}")
        out = await t.handler(sub, params)
    else:
        out = await call_toolbox_tool(sub, name, params, source="rpc")
    if out.status != "success":
        raise ToolError(out.content)
    bus.publish(parent.run_id, "tool_output_delta", {"id": parent.tool_call_id, "delta": f"↳ {name}: ok\n"})
    return out.ui.get("result", out.content) if isinstance(out.ui, dict) and "result" in out.ui else out.content
