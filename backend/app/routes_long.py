"""REST for long-job state the user can see and edit: plan (todos), memory, checkpoints, resume."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from . import checkpoints, db, memory, plan
from .agent.runner import runs
from .auth import require_user
from .events import bus
from .routes_projects import get_project
from .routes_runs import own_run, run_json
from .secrets_store import resolve

router = APIRouter(dependencies=[Depends(require_user)])


def _latest_run(project_id: str) -> str | None:
    with db.session() as s:
        r = s.scalars(select(db.Run).where(db.Run.project_id == project_id, db.Run.parent_run_id.is_(None)).order_by(db.Run.created_at.desc())).first()
        return r.id if r else None


# ------------------------------------------------------------------------------ plan
@router.get("/api/runs/{run_id}/plan")
def get_plan(run_id: str):
    own_run(run_id)
    return {"tree": plan.tree(run_id), "progress": plan.progress(run_id), "revisions": plan.revisions(run_id)}


class TodoIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    detail: str = ""
    priority: str = "medium"
    acceptance_criteria: str = ""
    parent_id: str | None = None


def _note_user_edit(run_id: str, text: str) -> None:
    runs.system_note(run_id, f"The user edited the plan: {text}. Re-read the plan (it is pinned above) and adapt.")


@router.post("/api/runs/{run_id}/todos", status_code=201)
def add_todo(run_id: str, body: TodoIn):
    r = own_run(run_id)
    try:
        t = plan.add(run_id, r.project_id, body.model_dump(exclude={"parent_id"}), body.parent_id, author="user")
    except plan.PlanError as e:
        raise HTTPException(400, str(e)) from None
    _note_user_edit(run_id, f"added “{t['title']}” ({t['id']})")
    return t


class TodoPatch(BaseModel):
    title: str | None = Field(None, max_length=300)
    detail: str | None = None
    priority: str | None = None
    acceptance_criteria: str | None = None
    status: str | None = None
    note: str | None = None


def _own_todo(todo_id: str) -> dict:
    try:
        t = plan.get(todo_id)
    except plan.PlanError:
        raise HTTPException(404, "todo not found") from None
    own_run(t["run_id"])
    return t


@router.patch("/api/todos/{todo_id}")
def patch_todo(todo_id: str, body: TodoPatch):
    t = _own_todo(todo_id)
    patch = body.model_dump(exclude_none=True)
    if patch.get("status") == "skipped" and not patch.get("note"):
        patch["note"] = "skipped by the user"
    if patch.get("status") == "done" and not patch.get("note") and not t["evidence"]:
        patch["note"] = "marked done by the user"
    try:
        new = plan.update(t["run_id"], todo_id, patch.pop("status", None), patch.pop("note", None), None, author="user", **patch)
    except plan.PlanError as e:
        raise HTTPException(400, str(e)) from None
    changes = ", ".join(f"{k}={v!r}" for k, v in body.model_dump(exclude_none=True).items())
    _note_user_edit(t["run_id"], f"changed “{new['title']}” ({todo_id}): {changes}")
    return new


class ReorderIn(BaseModel):
    ids: list[str]


@router.post("/api/runs/{run_id}/todos/reorder")
def reorder_todos(run_id: str, body: ReorderIn):
    own_run(run_id)
    try:
        plan.reorder(run_id, body.ids)
    except plan.PlanError as e:
        raise HTTPException(400, str(e)) from None
    _note_user_edit(run_id, "reordered the items")
    return plan.tree(run_id)


# ------------------------------------------------------------------------------ memory
@router.get("/api/projects/{project_id}/memory")
def list_memory(project_id: str):
    get_project(project_id)
    return memory.entries(project_id)


class MemoryIn(BaseModel):
    key: str = Field(..., min_length=1, max_length=200)
    value: str = Field(..., max_length=4000)


@router.put("/api/projects/{project_id}/memory")
def put_memory(project_id: str, body: MemoryIn):
    get_project(project_id)
    try:
        e = memory.write(project_id, body.key, body.value, "user")
    except memory.MemoryError_ as err:
        raise HTTPException(400, str(err)) from None
    rid = _latest_run(project_id)
    if rid:
        bus.publish(rid, "memory_update", {"action": "write", "entry": e, "by": "user"})
        if runs.is_active(rid):
            runs.system_note(rid, f"The user set project memory {e['key']!r} = {e['value']!r}.")
    return e


@router.delete("/api/projects/{project_id}/memory/{key}")
def delete_memory(project_id: str, key: str):
    get_project(project_id)
    if not memory.delete(project_id, key):
        raise HTTPException(404, "no such memory entry")
    rid = _latest_run(project_id)
    if rid:
        bus.publish(rid, "memory_update", {"action": "delete", "key": key, "by": "user"})
        if runs.is_active(rid):
            runs.system_note(rid, f"The user deleted project memory {key!r}.")
    return {"deleted": key}


# ------------------------------------------------------------------------------ checkpoints
@router.get("/api/projects/{project_id}/checkpoints")
def list_checkpoints(project_id: str):
    get_project(project_id)
    return checkpoints.list_(project_id)


class CheckpointIn(BaseModel):
    label: str = Field(..., min_length=1, max_length=300)
    run_id: str | None = None


@router.post("/api/projects/{project_id}/checkpoints", status_code=201)
async def create_checkpoint(project_id: str, body: CheckpointIn):
    get_project(project_id)
    rid = body.run_id or _latest_run(project_id)
    if body.run_id:
        own_run(body.run_id)
    try:
        return await asyncio.to_thread(checkpoints.create, project_id, body.label, rid)
    except checkpoints.CheckpointError as e:
        raise HTTPException(400, str(e)) from None


@router.get("/api/projects/{project_id}/checkpoints/{cid}/files")
def checkpoint_files(project_id: str, cid: str):
    get_project(project_id)
    try:
        return checkpoints.files(project_id, cid)
    except checkpoints.CheckpointError as e:
        raise HTTPException(404, str(e)) from None


@router.post("/api/projects/{project_id}/checkpoints/{cid}/restore")
async def restore_checkpoint(project_id: str, cid: str):
    get_project(project_id)
    rid = _latest_run(project_id)
    if rid and runs.is_active(rid):
        raise HTTPException(409, "Stop the running director before restoring a checkpoint.")
    try:
        res = await asyncio.to_thread(checkpoints.restore, project_id, cid, rid)
    except checkpoints.CheckpointError as e:
        raise HTTPException(404, str(e)) from None
    if rid:
        runs.system_note(rid, f"The user restored checkpoint “{res['restored']['label']}”: the workspace, output settings and plan are "
                              f"back to that state (undo checkpoint {res['safety_checkpoint']}).")
    return res


# ------------------------------------------------------------------------------ resume
@router.post("/api/runs/{run_id}/resume")
async def resume_run(run_id: str, request: Request):
    own_run(run_id)
    creds = resolve(request)
    try:
        res = await runs.resume(run_id, creds if creds.has_llm else None)
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    return {**res, "run": run_json(runs.get(run_id))}


# ------------------------------------------------------------------------------ artifacts
from . import artifacts  # noqa: E402


@router.get("/api/projects/{project_id}/artifacts")
def list_artifacts(project_id: str, run_id: str | None = None):
    get_project(project_id)
    return artifacts.list_(project_id, run_id)


@router.get("/api/projects/{project_id}/artifacts/versions/{group}")
def artifact_versions(project_id: str, group: str):
    get_project(project_id)
    return artifacts.versions(project_id, group)


def _own_artifact(artifact_id: str) -> dict:
    try:
        a = artifacts.get(artifact_id)
    except artifacts.ArtifactError:
        raise HTTPException(404, "artifact not found") from None
    get_project(a["project_id"])
    return a


class ArtifactPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    favorite: bool | None = None


@router.patch("/api/artifacts/{artifact_id}")
def patch_artifact(artifact_id: str, body: ArtifactPatch):
    _own_artifact(artifact_id)
    return artifacts.update(artifact_id, body.title, body.favorite)


@router.delete("/api/artifacts/{artifact_id}")
def delete_artifact(artifact_id: str):
    _own_artifact(artifact_id)
    artifacts.delete(artifact_id)
    return {"deleted": artifact_id}


@router.get("/api/projects/{project_id}/artifacts.zip")
async def artifacts_zip(project_id: str, favorites: bool = False):
    import tempfile

    from fastapi.responses import FileResponse
    from starlette.background import BackgroundTask

    import os
    from pathlib import Path

    p = get_project(project_id)
    fd, tmp = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    await asyncio.to_thread(artifacts.zip_all, project_id, Path(tmp), favorites)
    name = "".join(c if c.isalnum() or c in "-_" else "_" for c in p.name)[:60] or "artifacts"
    return FileResponse(tmp, media_type="application/zip", filename=f"{name}_artifacts.zip", background=BackgroundTask(os.remove, tmp))
