"""Artifacts: everything the director presents (videos, stills, audio, files, comparisons,
storyboards, timelines, code, tables, palettes, fonts). Versioned by ``version_group``:
presenting again in the same group creates v2, v3…"""
from __future__ import annotations

import re
import time
import zipfile
from pathlib import Path

from sqlalchemy import func, select

from . import db
from .config import config
from .events import bus

TYPES = ("video", "image", "audio", "file", "comparison", "storyboard", "timeline", "code", "table", "palette", "font", "grid", "options")


class ArtifactError(Exception):
    pass


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:120] or "untitled"


def file_url(project_id: str, rel: str | None) -> str | None:
    return f"/api/files/{project_id}/{rel}" if rel else None


def to_json(a: db.Artifact | dict) -> dict:
    d = db.to_dict(a) if not isinstance(a, dict) else dict(a)
    d["favorite"] = bool(d.get("favorite"))
    d["url"] = file_url(d["project_id"], d.get("path"))
    d["meta"] = d.get("meta") or {}
    return d


def create(project_id: str, run_id: str | None, type: str, title: str, path: str | None = None, version_group: str | None = None,
           meta: dict | None = None, publish: bool = True) -> dict:
    if type not in TYPES:
        raise ArtifactError(f"unknown artifact type {type}")
    group = slug(version_group or f"{type}-{title}")
    with db.session() as s:
        v = s.scalar(select(func.max(db.Artifact.version)).where(db.Artifact.project_id == project_id, db.Artifact.version_group == group)) or 0
        a = db.Artifact(project_id=project_id, run_id=run_id, type=type, path=path, title=(title or "Untitled")[:300], version_group=group,
                        version=v + 1, meta=meta or {}, created_at=time.time())
        s.add(a)
        s.flush()
        out = to_json(a)
    if publish and run_id:
        # `kind/path/url` keep the 1.0 shape that the Files/Preview panes understand
        bus.publish(run_id, "artifact", {"artifact": out, "kind": type, "path": path, "url": out["url"], "title": out["title"],
                                         "version": out["version"], "version_group": group})
    return out


def get(artifact_id: str) -> dict:
    with db.session() as s:
        a = s.get(db.Artifact, artifact_id)
        if a is None:
            raise ArtifactError(f"no artifact {artifact_id}")
        return to_json(a)


def list_(project_id: str, run_id: str | None = None) -> list[dict]:
    with db.session() as s:
        q = select(db.Artifact).where(db.Artifact.project_id == project_id)
        if run_id:
            q = q.where(db.Artifact.run_id == run_id)
        return [to_json(a) for a in s.scalars(q.order_by(db.Artifact.created_at.desc()))]


def versions(project_id: str, group: str) -> list[dict]:
    with db.session() as s:
        return [to_json(a) for a in s.scalars(select(db.Artifact).where(db.Artifact.project_id == project_id, db.Artifact.version_group == group)
                                                   .order_by(db.Artifact.version))]


def update(artifact_id: str, title: str | None = None, favorite: bool | None = None) -> dict:
    with db.session() as s:
        a = s.get(db.Artifact, artifact_id)
        if a is None:
            raise ArtifactError(f"no artifact {artifact_id}")
        if title is not None:
            a.title = title[:300]
        if favorite is not None:
            a.favorite = 1 if favorite else 0
        s.flush()
        return to_json(a)


def delete(artifact_id: str) -> None:
    """Removes the artifact record (the underlying workspace file is kept: other versions,
    scenes or deliverables may use it)."""
    with db.session() as s:
        a = s.get(db.Artifact, artifact_id)
        if a is None:
            raise ArtifactError(f"no artifact {artifact_id}")
        s.delete(a)


def files_of(a: dict) -> list[str]:
    out = [a["path"]] if a.get("path") else []
    m = a.get("meta") or {}
    for k in ("images", "shots"):
        out += [x.get("path") for x in m.get(k) or [] if isinstance(x, dict) and x.get("path")]
    for k in ("a", "b"):
        if isinstance(m.get(k), dict) and m[k].get("path"):
            out.append(m[k]["path"])
    if m.get("poster"):
        out.append(m["poster"])
    return list(dict.fromkeys(out))


def zip_all(project_id: str, dest: Path, favorites_only: bool = False) -> Path:
    pdir = config.projects_dir / project_id
    arts = [a for a in list_(project_id) if a["favorite"] or not favorites_only]
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        seen = set()
        index = []
        for a in arts:
            for rel in files_of(a):
                p = (pdir / rel).resolve()
                if rel in seen or pdir.resolve() not in p.parents or not p.is_file():
                    continue
                seen.add(rel)
                z.write(p, rel)
            index.append(f"{a['title']} (v{a['version']}, {a['type']}): {', '.join(files_of(a)) or '-'}")
        z.writestr("ARTIFACTS.txt", "\n".join(index) + "\n")
    return dest
