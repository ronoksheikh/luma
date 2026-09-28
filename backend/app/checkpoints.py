"""Workspace checkpoints with git.

The repository lives OUTSIDE the workspace (``/data/git/<project>``, mode 0700) so the agent's
shell can neither see nor corrupt it; the work tree is the project directory.  Heavy or
regenerable files (rendered frames, previews, logs, deliverables) are excluded.  Each commit
also carries ``work/.luma/state.json`` (project brief + output settings + the run's plan), so a
restore brings back the code, audio, settings and todo state together.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

from sqlalchemy import select

from . import db, plan
from .config import config
from .events import bus

EXCLUDE = ["renders/", "outputs/", "work/previews/", "work/.luma/logs/", "work/.luma/jobs/", "__pycache__/", "*.pyc", "*.log",
           "*.mov", "*.mkv", "frames/", ".cache/", "node_modules/"]
MAX_FILE = 25 * 1024 * 1024
_locks: dict[str, threading.Lock] = {}


class CheckpointError(Exception):
    pass


def _git_dir(project_id: str) -> Path:
    return config.data_dir / "git" / project_id


def _pdir(project_id: str) -> Path:
    return config.projects_dir / project_id


def _git(project_id: str, *args: str, check: bool = True, timeout: float = 120) -> str:
    gd = _git_dir(project_id)
    cmd = ["git", f"--git-dir={gd}", f"--work-tree={_pdir(project_id)}", "-c", "safe.directory=*", "-c", "user.name=Luma Studio",
           "-c", "user.email=luma@localhost", "-c", "core.autocrlf=false", "-c", "gc.auto=0", *args]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(_pdir(project_id)))
    if check and r.returncode != 0:
        raise CheckpointError(f"git {args[0]} failed: {(r.stderr or r.stdout).strip()[:800]}")
    return r.stdout


def _ensure_repo(project_id: str) -> None:
    gd = _git_dir(project_id)
    if (gd / "HEAD").exists():
        return
    gd.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(gd.parent, 0o700)
    subprocess.run(["git", "init", "--bare", "-q", str(gd)], check=True, capture_output=True)
    _git(project_id, "config", "core.bare", "false")


def _write_excludes(project_id: str) -> None:
    pdir = _pdir(project_id)
    big = []
    for root, dirs, files in os.walk(pdir):
        rel_root = Path(root).relative_to(pdir)
        dirs[:] = [d for d in dirs if not any(f"{(rel_root / d).as_posix()}/".startswith(e) or f"{d}/" == e for e in EXCLUDE if e.endswith("/"))]
        for f in files:
            p = Path(root) / f
            try:
                if p.stat().st_size > MAX_FILE:
                    big.append("/" + p.relative_to(pdir).as_posix())
            except OSError:
                pass
    info = _git_dir(project_id) / "info"
    info.mkdir(exist_ok=True)
    (info / "exclude").write_text("\n".join(EXCLUDE + big) + "\n")


def _state(project_id: str, run_id: str | None) -> dict:
    with db.session() as s:
        p = s.get(db.Project, project_id)
        st = {"brief": p.brief, "settings": p.settings or {}}
    st["run_id"] = run_id
    st["todos"] = plan.todos(run_id) if run_id else []
    return st


def create(project_id: str, label: str, run_id: str | None = None, auto: bool = False, meta: dict | None = None) -> dict:
    label = (label or "").strip()[:300] or "checkpoint"
    lock = _locks.setdefault(project_id, threading.Lock())
    with lock:
        _ensure_repo(project_id)
        _write_excludes(project_id)
        state_p = _pdir(project_id) / "work" / ".luma" / "state.json"
        state_p.parent.mkdir(parents=True, exist_ok=True)
        state_p.write_text(json.dumps(_state(project_id, run_id), indent=1, default=str))
        _git(project_id, "add", "-A", timeout=600)
        _git(project_id, "commit", "-q", "--allow-empty", "-m", label, timeout=600)
        commit = _git(project_id, "rev-parse", "HEAD").strip()
        files = _git(project_id, "show", "--stat", "--format=", "HEAD", check=False).strip().splitlines()
        stats = files[-1].strip() if files else ""
    with db.session() as s:
        c = db.Checkpoint(project_id=project_id, run_id=run_id, label=label, commit=commit, auto=1 if auto else 0,
                          meta={**(meta or {}), "stats": stats})
        s.add(c)
        s.flush()
        out = db.to_dict(c)
    if run_id:
        bus.publish(run_id, "checkpoint", {"action": "create", "checkpoint": out})
    return out


def list_(project_id: str) -> list[dict]:
    with db.session() as s:
        return [db.to_dict(c) for c in s.scalars(select(db.Checkpoint).where(db.Checkpoint.project_id == project_id).order_by(db.Checkpoint.created_at.desc()))]


def files(project_id: str, checkpoint_id: str) -> list[str]:
    c = _get(project_id, checkpoint_id)
    return _git(project_id, "ls-tree", "-r", "--name-only", c["commit"]).splitlines()


def _get(project_id: str, checkpoint_id: str) -> dict:
    with db.session() as s:
        c = s.get(db.Checkpoint, checkpoint_id)
        if c is None or c.project_id != project_id:
            raise CheckpointError(f"no checkpoint {checkpoint_id}")
        return db.to_dict(c)


def restore(project_id: str, checkpoint_id: str, run_id: str | None = None) -> dict:
    """Restore the workspace (tracked files), project settings and the plan of the run the
    checkpoint was taken in.  The current state is checkpointed first, so a restore can itself
    be undone."""
    c = _get(project_id, checkpoint_id)
    before = create(project_id, f"Before restoring “{c['label']}”", run_id=run_id or c["run_id"], auto=True)
    lock = _locks.setdefault(project_id, threading.Lock())
    with lock:
        _git(project_id, "read-tree", "--reset", "-u", c["commit"], timeout=600)
    state_p = _pdir(project_id) / "work" / ".luma" / "state.json"
    restored_plan = 0
    if state_p.exists():
        st = json.loads(state_p.read_text())
        with db.session() as s:
            p = s.get(db.Project, project_id)
            p.brief = st.get("brief", p.brief)
            p.settings = st.get("settings") or p.settings
            p.updated_at = time.time()
        target_run = run_id or st.get("run_id")
        if target_run and st.get("todos") is not None:
            restored_plan = _restore_plan(target_run, project_id, st["todos"], c["label"])
    out = {"restored": c, "safety_checkpoint": before["id"], "plan_items": restored_plan}
    for rid in {run_id, c["run_id"]} - {None}:
        bus.publish(rid, "checkpoint", {"action": "restore", "checkpoint": c, "safety_checkpoint": before["id"]})
    return out


def _restore_plan(run_id: str, project_id: str, items: list[dict], label: str) -> int:
    from sqlalchemy import delete

    old = plan.todos(run_id)
    with db.session() as s:
        if old:
            rev = len(list(s.scalars(select(db.PlanRevision.id).where(db.PlanRevision.run_id == run_id)))) + 1
            s.add(db.PlanRevision(run_id=run_id, revision=rev, reason=f"restored checkpoint “{label}”", author="user", snapshot=old))
        s.execute(delete(db.Todo).where(db.Todo.run_id == run_id))
        s.flush()
        for t in items:
            s.add(db.Todo(**{k: t.get(k) for k in ("id", "parent_id", "title", "detail", "status", "priority", "order", "acceptance_criteria",
                                                   "evidence", "note", "started_at", "completed_at", "created_at", "updated_at")},
                          run_id=run_id, project_id=project_id))
    plan.publish(run_id, f"restored checkpoint “{label}”")
    return len(items)


def auto(project_id: str, run_id: str | None, label: str, **meta) -> dict | None:
    """Best-effort automatic checkpoint (after successful renders)."""
    try:
        return create(project_id, label, run_id=run_id, auto=True, meta=meta)
    except Exception:  # noqa: BLE001 — a checkpoint must never break a render
        return None
