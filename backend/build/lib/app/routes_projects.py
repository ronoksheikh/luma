"""Projects, assets and safe file serving."""
from __future__ import annotations

import mimetypes
import os
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from . import assets as A
from . import db
from .config import DEFAULT_PROJECT_SETTINGS, FPS_CHOICES, PROJECT_SUBDIRS, RESOLUTIONS, config

router = APIRouter()


# ======================================================================================
# helpers
# ======================================================================================


def project_dir(project_id: str) -> Path:
    return config.projects_dir / project_id


def ensure_workspace(project_id: str) -> Path:
    d = project_dir(project_id)
    for sub in PROJECT_SUBDIRS:
        (d / sub).mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(d / sub, 0o2775)
        except OSError:
            pass
    try:
        os.chmod(d, 0o2775)
    except OSError:
        pass
    return d


def safe_path(project_id: str, rel: str) -> Path:
    """Resolve ``rel`` inside the project dir; reject traversal and escaping symlinks."""
    base = project_dir(project_id).resolve()
    if not base.exists():
        raise HTTPException(404, "project not found")
    if "\x00" in rel or rel.startswith("/") or "\\" in rel:
        raise HTTPException(400, "invalid path")
    p = (base / rel).resolve()
    if p != base and base not in p.parents:
        raise HTTPException(403, "path outside the project")
    return p


def file_url(project_id: str, rel: str) -> str:
    return f"/api/files/{project_id}/{rel}"


def get_project(project_id: str) -> db.Project:
    with db.session() as s:
        p = s.get(db.Project, project_id)
        if p is None:
            raise HTTPException(404, "project not found")
        return p


def project_json(p: db.Project) -> dict:
    with db.session() as s:
        n_assets = s.scalar(select(func.count()).select_from(db.Asset).where(db.Asset.project_id == p.id)) or 0
        run = s.scalars(select(db.Run).where(db.Run.project_id == p.id).order_by(db.Run.created_at.desc())).first()
    return {
        "id": p.id, "name": p.name, "brief": p.brief, "kind": p.kind,
        "settings": {**DEFAULT_PROJECT_SETTINGS, **(p.settings or {})},
        "created_at": p.created_at, "updated_at": p.updated_at, "asset_count": n_assets, "max_assets": config.max_files,
        "latest_run": {"id": run.id, "status": run.status, "kind": run.kind} if run else None,
    }


def asset_json(a: db.Asset) -> dict:
    return {
        "id": a.id, "project_id": a.project_id, "filename": a.filename, "kind": a.kind, "size": a.size, "path": a.path,
        "url": file_url(a.project_id, a.path), "thumb_url": file_url(a.project_id, a.thumb) if a.thumb else None,
        "analysis": a.analysis, "created_at": a.created_at,
    }


class ProjectSettings(BaseModel):
    width: int = 1920
    height: int = 1080
    fps: int = 60
    duration: float = Field(5.0, ge=3, le=180)
    formats: list[str] = ["mp4"]
    avoid_colors: list[str] = []
    voice_language: str = Field("en", max_length=32)
    voice_tone: str = Field("", max_length=200)
    captions: bool = True

    @field_validator("fps")
    @classmethod
    def _fps(cls, v):
        if v not in FPS_CHOICES:
            raise ValueError(f"fps must be one of {FPS_CHOICES}")
        return v

    @field_validator("formats")
    @classmethod
    def _formats(cls, v):
        bad = [f for f in v if f not in ("mp4", "prores")]
        if bad or "mp4" not in v:
            raise ValueError("formats: mp4 (required) and optionally prores")
        return v

    @field_validator("avoid_colors")
    @classmethod
    def _avoid(cls, v):
        from luma_engine.brand import Color

        return [Color.of(c).hex for c in v][:32]

    def check_resolution(self):
        if (self.width, self.height) not in RESOLUTIONS:
            raise HTTPException(422, f"resolution must be one of {[f'{w}x{h}' for w, h in RESOLUTIONS]}")


class ProjectIn(BaseModel):
    name: str = Field("Untitled project", min_length=1, max_length=200)
    brief: str = Field("", max_length=20000)
    settings: ProjectSettings | None = None


class ProjectPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    brief: str | None = Field(None, max_length=20000)
    settings: ProjectSettings | None = None


# ======================================================================================
# projects
# ======================================================================================


@router.get("/api/projects")
def list_projects():
    with db.session() as s:
        ps = list(s.scalars(select(db.Project).order_by(db.Project.updated_at.desc())))
    return [project_json(p) for p in ps]


@router.post("/api/projects", status_code=201)
def create_project(body: ProjectIn):
    st = body.settings or ProjectSettings()
    st.check_resolution()
    with db.session() as s:
        p = db.Project(name=body.name.strip(), brief=body.brief, settings=st.model_dump())
        s.add(p)
    ensure_workspace(p.id)
    return project_json(p)


@router.get("/api/projects/{project_id}")
def read_project(project_id: str):
    return project_json(get_project(project_id))


@router.patch("/api/projects/{project_id}")
def update_project(project_id: str, body: ProjectPatch):
    get_project(project_id)
    if body.settings is not None:
        body.settings.check_resolution()
    with db.session() as s:
        p = s.get(db.Project, project_id)
        if body.name is not None:
            p.name = body.name.strip()
        if body.brief is not None:
            p.brief = body.brief
        if body.settings is not None:
            p.settings = body.settings.model_dump()
        p.updated_at = time.time()
    return project_json(get_project(project_id))


@router.post("/api/projects/{project_id}/duplicate", status_code=201)
def duplicate_project(project_id: str):
    src = get_project(project_id)
    with db.session() as s:
        p = db.Project(name=f"{src.name} (copy)", brief=src.brief, settings=dict(src.settings or {}))
        s.add(p)
        s.flush()
        new_id = p.id
        for a in s.scalars(select(db.Asset).where(db.Asset.project_id == project_id)):
            s.add(db.Asset(project_id=new_id, filename=a.filename, kind=a.kind, size=a.size, path=a.path, thumb=a.thumb, analysis=a.analysis))
    ensure_workspace(new_id)
    sd, dd = project_dir(project_id), project_dir(new_id)
    for sub in ("assets", "work", "audio"):
        if (sd / sub).exists():
            shutil.copytree(sd / sub, dd / sub, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".luma", "__pycache__"))
    return project_json(get_project(new_id))


@router.delete("/api/projects/{project_id}")
async def delete_project(project_id: str):
    get_project(project_id)
    from .agent.runner import runs
    from .jobs import jobs
    from .terminal import terminals

    with db.session() as s:
        run_ids = [r.id for r in s.scalars(select(db.Run).where(db.Run.project_id == project_id))]
    for rid in run_ids:
        await runs.cancel(rid)
    for j in jobs.list(project_id):
        if j["status"] in ("queued", "running"):
            jobs.kill_id(j["id"])
    terminals.close(project_id)
    with db.session() as s:
        for model in (db.Asset, db.Job):
            for row in s.scalars(select(model).where(model.project_id == project_id)):
                s.delete(row)
        for r in s.scalars(select(db.Run).where(db.Run.project_id == project_id)):
            for m in s.scalars(select(db.Message).where(db.Message.run_id == r.id)):
                s.delete(m)
            for e in s.scalars(select(db.Event).where(db.Event.run_id == r.id)):
                s.delete(e)
            s.delete(r)
        s.delete(s.get(db.Project, project_id))
    shutil.rmtree(project_dir(project_id), ignore_errors=True)
    return {"deleted": project_id}


# ======================================================================================
# assets
# ======================================================================================


@router.get("/api/projects/{project_id}/assets")
def list_assets(project_id: str):
    get_project(project_id)
    with db.session() as s:
        return [asset_json(a) for a in s.scalars(select(db.Asset).where(db.Asset.project_id == project_id).order_by(db.Asset.created_at))]


def add_asset_bytes(project_id: str, filename: str, data: bytes) -> dict:
    kind, data = A.validate(data, filename)
    pdir = ensure_workspace(project_id)
    dest = A.unique_path(pdir / "assets", A.safe_filename(filename, kind))
    dest.write_bytes(data)
    try:
        os.chmod(dest, 0o664)
    except OSError:
        pass
    rel = str(dest.relative_to(pdir))
    analysis = A.analyze(kind, dest, pdir)
    thumb = A.thumbnail(kind, dest, pdir)
    with db.session() as s:
        a = db.Asset(project_id=project_id, filename=dest.name, kind=kind, size=len(data), path=rel, thumb=thumb, analysis=analysis)
        s.add(a)
        p = s.get(db.Project, project_id)
        p.updated_at = time.time()
    return asset_json(a)


@router.post("/api/projects/{project_id}/assets", status_code=201)
async def upload_assets(project_id: str, files: list[UploadFile] = File(...)):
    get_project(project_id)
    with db.session() as s:
        have = s.scalar(select(func.count()).select_from(db.Asset).where(db.Asset.project_id == project_id)) or 0
    if have + len(files) > config.max_files:
        raise HTTPException(409, f"A project can hold at most {config.max_files} files ({have} already uploaded, {len(files)} more requested).")
    # read + validate everything first so a bad file doesn't leave a partial upload
    payloads = []
    for f in files:
        data = await f.read(config.max_file_bytes + 1)
        try:
            A.validate(data, f.filename or "upload")
        except A.UploadError as e:
            raise HTTPException(e.status, str(e)) from None
        payloads.append((f.filename or "upload", data))
    import asyncio

    out = []
    for name, data in payloads:
        out.append(await asyncio.to_thread(add_asset_bytes, project_id, name, data))
    return out


@router.delete("/api/projects/{project_id}/assets/{asset_id}")
def delete_asset(project_id: str, asset_id: str):
    with db.session() as s:
        a = s.get(db.Asset, asset_id)
        if a is None or a.project_id != project_id:
            raise HTTPException(404, "asset not found")
        path, thumb = a.path, a.thumb
        s.delete(a)
    for rel in (path, thumb):
        if rel:
            try:
                safe_path(project_id, rel).unlink()
            except (FileNotFoundError, HTTPException):
                pass
    return {"deleted": asset_id}


# ======================================================================================
# files
# ======================================================================================

LISTABLE = ("outputs", "renders", "audio", "work", "assets")
MEDIA_EXT = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".mp4", ".mov", ".wav", ".mp3", ".srt", ".json", ".zip", ".pdf", ".txt", ".py", ".md"}


@router.get("/api/projects/{project_id}/files")
def list_files(project_id: str, dir: str = "outputs", recursive: bool = True, limit: int = 500):
    if dir.split("/")[0] not in LISTABLE:
        raise HTTPException(400, f"dir must start with one of {LISTABLE}")
    root = safe_path(project_id, dir)
    if not root.exists():
        return []
    base = project_dir(project_id).resolve()
    items = []
    it = root.rglob("*") if recursive else root.iterdir()
    for p in it:
        if len(items) >= limit:
            break
        rel = p.relative_to(base)
        if any(part.startswith(".") for part in rel.parts) or "frames" in rel.parts:
            continue
        if p.is_file() and p.suffix.lower() in MEDIA_EXT:
            st = p.stat()
            items.append({"path": str(rel), "url": file_url(project_id, str(rel)), "size": st.st_size, "mtime": st.st_mtime,
                          "ext": p.suffix.lower()})
    items.sort(key=lambda x: -x["mtime"])
    return items


@router.get("/api/files/{project_id}/{rel:path}")
def serve_file(project_id: str, rel: str, request: Request, download: bool = False):
    p = safe_path(project_id, rel)
    if not p.is_file():
        raise HTTPException(404, "file not found")
    ext = p.suffix.lower()
    mt = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    if ext in (".py", ".md", ".txt", ".srt", ".json", ".sh", ".log"):
        mt = "text/plain; charset=utf-8"
    headers = {"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff",
               "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox"}
    if ext in (".html", ".htm", ".xhtml"):
        mt = "text/plain; charset=utf-8"  # never render user HTML
    return FileResponse(p, media_type=mt, headers=headers, filename=p.name if download else None,
                        content_disposition_type="attachment" if download else "inline")
