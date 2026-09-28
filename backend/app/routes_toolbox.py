"""REST API for the Toolbox UI: tools (list / detail / create / update / test / enable / promote / delete / history /
diff / revert / try), skills, plugins, templates, search and the audit log.  Users author tools and skills through the
same validation, tests and static checks as the agent (``author: user``)."""
from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import db
from .auth import current_user, require_user
from .config import config
from .routes_projects import get_project, project_dir
from .toolbox import boot, venv
from .toolbox import execute as X
from .toolbox import lifecycle as L
from .toolbox import manifest as M
from .toolbox import plugins as P
from .toolbox import skills as K
from .toolbox import store

router = APIRouter(dependencies=[Depends(require_user)])


def _actor() -> str:
    uid = current_user.get()
    with db.session() as s:
        u = s.get(db.User, uid) if uid else None
    return f"user:{u.username}" if u else "user"


def _row(scope: str, name: str, project_id: str | None) -> db.ToolboxTool:
    if scope not in ("global", "project"):
        raise HTTPException(400, "scope must be global or project")
    if scope == "project":
        if not project_id:
            raise HTTPException(400, "project_id is required for project tools")
        get_project(project_id)
    store.sync(project_id if scope == "project" else None, L.reserved_names())
    r = store.get(scope, project_id if scope == "project" else None, name)
    if r is None:
        raise HTTPException(404, f"no {scope} tool named {name}")
    return r


def _err(e: Exception) -> HTTPException:
    return HTTPException(400, str(e))


# ------------------------------------------------------------------------------------------ overview
@router.get("/api/toolbox/summary")
def summary(project_id: str | None = None):
    if project_id:
        get_project(project_id)
    store.sync(project_id, L.reserved_names())
    rows = store.rows(project_id)
    by = {}
    for r in rows:
        st = store.status_of(r)
        by[st] = by.get(st, 0) + 1
    return {"tools": len(rows), "by_status": by, "skills": len(K.all_skills()), "plugins": len(P.all_plugins()),
            "templates": len([p for p in P.all_plugins("template")]) + len(P.BUILTIN_TEMPLATES),
            "venv": {k: venv.state.get(k) for k in ("status", "python", "missing")} | {"log": venv.state.get("log", [])[-5:]},
            "boot": boot.state, "overrides": store.overrides(project_id)}


@router.get("/api/toolbox/search")
def search(q: str, project_id: str | None = None, kinds: str = "tool,skill,plugin"):
    if project_id:
        get_project(project_id)
    store.sync(project_id, L.reserved_names())
    return store.search(q, project_id, tuple(k for k in kinds.split(",") if k), limit=20)


@router.get("/api/toolbox/audit")
def audit(limit: int = Query(200, le=1000)):
    return store.audit_log(limit)


# ------------------------------------------------------------------------------------------ tools
@router.get("/api/toolbox/tools")
def list_tools(project_id: str | None = None, scope: str | None = None, status: str | None = None, author: str | None = None,
               tag: str | None = None, q: str | None = None):
    if project_id:
        get_project(project_id)
    store.sync(project_id, L.reserved_names())
    rows = store.rows(project_id, scope if scope in ("global", "project") else None)
    ov = set(store.overrides(project_id))
    hits = None
    if q:
        hits = {h["ref"]: h["score"] for h in store.search(q, project_id, ("tool",), limit=100)}
    out = []
    for r in rows:
        t = store.public(r)
        t["overrides_global"] = r.scope == "project" and r.name in ov
        t["shadowed"] = r.scope == "global" and r.name in ov
        if status and t["status"] != status:
            continue
        if author and r.author != author:
            continue
        if tag and tag not in (r.tags or []):
            continue
        if hits is not None:
            if r.id not in hits and q.lower() not in r.name:
                continue
            t["score"] = hits.get(r.id, 0)
        out.append(t)
    if hits is not None:
        out.sort(key=lambda t: -t.get("score", 0))
    return out


@router.get("/api/toolbox/tools/{scope}/{name}")
def tool_detail(scope: str, name: str, project_id: str | None = None):
    r = _row(scope, name, project_id)
    d = store.public(r, detail=True)
    d["files"] = L.read(r)
    d["recent_calls"] = store.recent_calls(r.id, 30)
    d["versions"] = L.versions(r)
    d["function_schema"] = M.function_schema(r.manifest)["function"] if r.manifest else None
    d["overrides_global"] = scope == "project" and store.get("global", None, name) is not None
    return d


class ToolIn(BaseModel):
    scope: str = Field("project", pattern="^(global|project)$")
    project_id: str | None = None
    name: str
    manifest: Any
    main_py: str = Field(max_length=200_000)
    test_py: str = Field(max_length=200_000)
    readme: str = Field(max_length=200_000)
    fixtures: dict | None = None


@router.post("/api/toolbox/tools", status_code=201)
async def create_tool(body: ToolIn):
    pdir = None
    if body.scope == "project":
        if not body.project_id:
            raise HTTPException(400, "project_id is required for a project tool")
        get_project(body.project_id)
        pdir = project_dir(body.project_id)
    try:
        return await L.create(body.scope, body.project_id if body.scope == "project" else None, body.name, body.manifest, body.main_py, body.test_py,
                              body.readme, body.fixtures, "user", None, _actor(), pdir)
    except (L.ToolboxError, M.ManifestError) as e:
        raise _err(e) from None


class ToolUpdate(BaseModel):
    main_py: str | None = Field(None, max_length=200_000)
    test_py: str | None = Field(None, max_length=200_000)
    readme: str | None = Field(None, max_length=200_000)
    manifest: Any = None
    fixtures: dict | None = None
    remove_fixtures: list[str] | None = None
    bump: str = Field("patch", pattern="^(patch|minor|major)$")
    reason: str = ""


@router.put("/api/toolbox/tools/{scope}/{name}")
async def update_tool(scope: str, name: str, body: ToolUpdate, project_id: str | None = None):
    r = _row(scope, name, project_id)
    changes = {k: v for k, v in body.model_dump().items() if k in ("main_py", "test_py", "readme", "manifest", "fixtures", "remove_fixtures") and v is not None}
    try:
        return await L.update(r, changes, body.bump, body.reason, None, _actor(), project_dir(project_id) if project_id else None)
    except (L.ToolboxError, M.ManifestError) as e:
        raise _err(e) from None


@router.post("/api/toolbox/tools/{scope}/{name}/test")
async def test_tool(scope: str, name: str, project_id: str | None = None):
    return await L.test(_row(scope, name, project_id))


@router.post("/api/toolbox/tools/{scope}/{name}/enable")
async def enable_tool(scope: str, name: str, project_id: str | None = None):
    try:
        return await L.set_enabled(_row(scope, name, project_id), True, _actor())
    except L.ToolboxError as e:
        raise _err(e) from None


@router.post("/api/toolbox/tools/{scope}/{name}/disable")
async def disable_tool(scope: str, name: str, project_id: str | None = None):
    return await L.set_enabled(_row(scope, name, project_id), False, _actor(), reason="disabled in the Toolbox")


@router.get("/api/toolbox/tools/project/{name}/promotion")
def promotion_preview(name: str, project_id: str):
    return L.promotion_preview(_row("project", name, project_id))


@router.post("/api/toolbox/tools/project/{name}/promote")
async def promote_tool(name: str, project_id: str):
    """The user promoting from the Toolbox IS the approval; the static scan still applies."""
    try:
        return await L.promote(_row("project", name, project_id), None, _actor())
    except L.ToolboxError as e:
        raise _err(e) from None


class Deprecate(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)
    replacement: str | None = None


@router.post("/api/toolbox/tools/{scope}/{name}/deprecate")
async def deprecate_tool(scope: str, name: str, body: Deprecate, project_id: str | None = None):
    return await L.deprecate(_row(scope, name, project_id), body.reason, body.replacement, None, _actor())


@router.delete("/api/toolbox/tools/{scope}/{name}")
async def delete_tool(scope: str, name: str, project_id: str | None = None):
    return await L.delete(_row(scope, name, project_id), None, _actor())


@router.get("/api/toolbox/tools/{scope}/{name}/history")
def history(scope: str, name: str, project_id: str | None = None):
    return L.versions(_row(scope, name, project_id))


@router.get("/api/toolbox/tools/{scope}/{name}/diff")
def diff(scope: str, name: str, a: str, b: str | None = None, project_id: str | None = None):
    r = _row(scope, name, project_id)
    try:
        return {"diff": L.diff(r, a, b)}
    except Exception as e:  # noqa: BLE001
        raise _err(e) from None


class Revert(BaseModel):
    version: str


@router.post("/api/toolbox/tools/{scope}/{name}/revert")
async def revert(scope: str, name: str, body: Revert, project_id: str | None = None):
    try:
        return await L.rollback(_row(scope, name, project_id), body.version, None, _actor())
    except L.ToolboxError as e:
        raise _err(e) from None


class TryIn(BaseModel):
    project_id: str
    params: dict = {}


@router.post("/api/toolbox/tools/{scope}/{name}/try")
async def try_tool(scope: str, name: str, body: TryIn):
    """Run a tool by hand (the 'Try it' form) in a project's workspace. No agent run: ctx.call_tool is unavailable."""
    get_project(body.project_id)
    r = _row(scope, name, body.project_id if scope == "project" else None)
    if store.status_of(r) in ("invalid",):
        raise HTTPException(400, f"{name} has an invalid manifest")
    pdir = project_dir(body.project_id)
    res = await X.run_tool(r, body.params, pdir, call_id=f"try_{int(__import__('time').time() * 1000)}")
    if res.kind != "params":
        store.record_call(r, None, None, res.ok, res.duration, None if res.ok else res.error, "try", body.project_id)
    rel = lambda p: str(Path(p).resolve().relative_to(pdir.resolve()))  # noqa: E731
    return {"ok": res.ok, "result": res.result, "error": res.error, "traceback": res.traceback, "kind": res.kind, "duration": res.duration,
            "logs": res.logs[-200:], "images": [f"/api/files/{body.project_id}/{rel(p)}" for p in res.images],
            "out_dir": rel(res.out_dir) if res.out_dir and res.out_dir.exists() else None}


# ------------------------------------------------------------------------------------------ skills
@router.get("/api/toolbox/skills")
def list_skills(q: str | None = None):
    K.sync()
    return K.search(q, 50) if q else K.all_skills()


@router.get("/api/toolbox/skills/{name}")
def get_skill(name: str):
    try:
        return K.read(name, count=False)
    except K.SkillError as e:
        raise HTTPException(404, str(e)) from None


class SkillIn(BaseModel):
    content: str = Field(max_length=200_000)


@router.put("/api/toolbox/skills/{name}")
def put_skill(name: str, body: SkillIn):
    try:
        return K.write(name, body.content, "user", None, _actor())
    except K.SkillError as e:
        raise _err(e) from None


@router.delete("/api/toolbox/skills/{name}")
def delete_skill(name: str):
    try:
        K.delete(name, _actor())
    except K.SkillError as e:
        raise HTTPException(404, str(e)) from None
    return {"deleted": name}


# ------------------------------------------------------------------------------------------ plugins / templates
@router.get("/api/toolbox/plugins")
def list_plugins(kind: str | None = None):
    P.sync()
    return P.all_plugins(kind)


@router.get("/api/toolbox/plugins/{name}")
def get_plugin(name: str):
    try:
        return P.read(name)
    except L.ToolboxError as e:
        raise HTTPException(404, str(e)) from None


@router.post("/api/toolbox/plugins/{name}/{action}")
async def plugin_action(name: str, action: str):
    if action not in ("enable", "disable"):
        raise HTTPException(404)
    try:
        return await P.set_enabled(name, action == "enable", _actor())
    except L.ToolboxError as e:
        raise _err(e) from None


@router.delete("/api/toolbox/plugins/{name}")
def delete_plugin(name: str):
    try:
        P.delete(name, _actor())
    except L.ToolboxError as e:
        raise HTTPException(404, str(e)) from None
    return {"deleted": name}


@router.get("/api/toolbox/plugins/{name}/file/{rel:path}")
def plugin_file(name: str, rel: str):
    p = P.get(name)
    if p is None:
        raise HTTPException(404)
    base = (config.data_dir / p.path).resolve()
    f = (base / rel).resolve()
    if base not in f.parents or not f.is_file() or f.suffix.lower() not in (".mp4", ".png", ".jpg", ".webp", ".gif", ".svg"):
        raise HTTPException(404)
    mt = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
    return FileResponse(f, media_type=mt, headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff",
                                                   "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox"})


@router.get("/api/toolbox/templates")
def list_templates():
    P.sync()
    return P.templates()


class UseTemplate(BaseModel):
    name: str | None = Field(None, max_length=200)
    params: dict | None = None


@router.post("/api/toolbox/templates/{name}/use", status_code=201)
def use_template(name: str, body: UseTemplate):
    from .routes_projects import project_json

    try:
        res = P.use_template(name, current_user.get(), body.name, body.params)
    except L.ToolboxError as e:
        raise _err(e) from None
    return {**res, "project": project_json(get_project(res["project_id"]))}
