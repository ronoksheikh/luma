"""Toolbox index: where tools / skills / plugins live, their DB rows, FTS5 search, per-tool
usage stats and health, and the audit log.

Filesystem is the source of truth; the database is an index that :func:`sync` rebuilds from
it (on boot, and whenever a scope is listed or searched).  A tool is *callable* only when it
is enabled (registered) AND the hash of its files still equals the hash its tests passed on —
editing ``main.py`` by hand makes it "modified" until it is tested again.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import time
from pathlib import Path

from sqlalchemy import select, text

from .. import db
from ..config import config
from . import manifest as M
from .gitrepo import Repo

log = logging.getLogger("luma.toolbox")
FAIL_LIMIT = 3


# ------------------------------------------------------------------------------------------ paths
def tools_root(scope: str, project_id: str | None) -> Path:
    if scope == "global":
        return config.toolbox_dir / "tools"
    if not project_id:
        raise ValueError("project scope needs a project")
    return config.projects_dir / project_id / "work" / "tools"


def tool_dir(scope: str, project_id: str | None, name: str) -> Path:
    return tools_root(scope, project_id) / name


def skills_dir() -> Path:
    return config.toolbox_dir / "skills"


def plugins_dir() -> Path:
    return config.toolbox_dir / "plugins"


def global_repo() -> Repo:
    return Repo(config.toolbox_dir / ".git", config.toolbox_dir)


def project_repo(project_id: str) -> Repo:
    return Repo(config.data_dir / "git" / f"{project_id}.work", config.projects_dir / project_id / "work", include=["tools", "scripts"])


def repo_for(scope: str, project_id: str | None) -> tuple[Repo, str]:
    """(repository, path prefix of tools inside it)."""
    return (global_repo(), "tools") if scope == "global" else (project_repo(project_id), "tools")


def ensure_layout() -> None:
    """Create /data/toolbox (git repo, read-only for the sandbox), /data/venv and /data/opt."""
    tb = config.toolbox_dir
    for d in (tb, tb / "tools", tb / "skills", tb / "plugins", config.opt_dir):
        d.mkdir(parents=True, exist_ok=True)
    for d in (tb, tb / "tools", tb / "skills", tb / "plugins"):
        try:
            os.chmod(d, 0o2755)
        except OSError:
            pass
    config.venv_dir.mkdir(parents=True, exist_ok=True)
    r = global_repo().ensure()
    readme = tb / "README.md"
    if not readme.exists():
        readme.write_text("# Luma Studio toolbox\n\nGlobal tools, skills and engine plugins, shared by every project. "
                          "Managed by Luma Studio — every change is a commit; tool versions are tags `<tool>@<version>`.\n")
        r.commit("Initialise the toolbox", author="Luma Studio")


def files_hash(d: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(p for p in d.rglob("*") if p.is_file()):
        rel = f.relative_to(d).as_posix()
        if "__pycache__" in rel or ".pytest_cache" in rel or ".ruff_cache" in rel or rel.startswith(".tmp"):
            continue
        h.update(rel.encode() + b"\0")
        h.update(f.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def rel_to_data(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(config.data_dir.resolve()))
    except ValueError:
        return str(p)


# ------------------------------------------------------------------------------------------ rows
def public(row: db.ToolboxTool, detail: bool = False) -> dict:
    d = db.to_dict(row)
    st = row.stats or {}
    calls = int(st.get("calls") or 0)
    d["stats"] = {**st, "success_rate": round(st.get("ok", 0) / calls, 3) if calls else None,
                  "avg_s": round(st.get("total_s", 0.0) / calls, 3) if calls else None}
    d["status"] = status_of(row)
    if not detail:
        d.pop("test_output", None)
        d.pop("manifest", None)
    return d


def status_of(row: db.ToolboxTool) -> str:
    if row.test_status == "invalid":
        return "invalid"
    if row.deprecated:
        return "deprecated"
    if row.enabled and row.tested_hash and row.files_hash and row.tested_hash != row.files_hash:
        return "modified"
    if row.enabled:
        return "enabled"
    if row.test_status == "failed" or (row.stats or {}).get("fail_streak", 0) >= FAIL_LIMIT:
        return "failing"
    return "disabled"


def get(scope: str, project_id: str | None, name: str) -> db.ToolboxTool | None:
    with db.session() as s:
        q = select(db.ToolboxTool).where(db.ToolboxTool.name == name, db.ToolboxTool.scope == scope)
        q = q.where(db.ToolboxTool.project_id == project_id) if scope == "project" else q.where(db.ToolboxTool.project_id.is_(None))
        return s.scalars(q).first()


def resolve(name: str, project_id: str | None) -> db.ToolboxTool | None:
    """Project tools override global ones with the same name."""
    if project_id:
        r = get("project", project_id, name)
        if r is not None:
            return r
    return get("global", None, name)


def rows(project_id: str | None, scope: str | None = None) -> list[db.ToolboxTool]:
    with db.session() as s:
        q = select(db.ToolboxTool)
        if scope == "global":
            q = q.where(db.ToolboxTool.scope == "global")
        elif scope == "project":
            q = q.where(db.ToolboxTool.scope == "project", db.ToolboxTool.project_id == project_id)
        else:
            q = q.where((db.ToolboxTool.scope == "global") | (db.ToolboxTool.project_id == project_id))
        return list(s.scalars(q.order_by(db.ToolboxTool.name)))


def effective(project_id: str | None) -> dict[str, db.ToolboxTool]:
    """name → row after project-over-global overriding."""
    out: dict[str, db.ToolboxTool] = {}
    for r in rows(project_id):
        if r.name not in out or r.scope == "project":
            out[r.name] = r
    return out


def overrides(project_id: str | None) -> list[str]:
    names: dict[str, set] = {}
    for r in rows(project_id):
        names.setdefault(r.name, set()).add(r.scope)
    return sorted(n for n, sc in names.items() if len(sc) == 2)


def callable_rows(project_id: str | None) -> dict[str, db.ToolboxTool]:
    return {n: r for n, r in effective(project_id).items() if status_of(r) == "enabled"}


# ------------------------------------------------------------------------------------------ sync
def sync(project_id: str | None = None, reserved: set[str] | None = None) -> None:
    """Rebuild the index of the global tools (and the project's) from the filesystem."""
    scopes = [("global", None)] + ([("project", project_id)] if project_id else [])
    for scope, pid in scopes:
        root = tools_root(scope, pid)
        seen = set()
        if root.exists():
            for d in sorted(root.iterdir()):
                if d.is_dir() and (d / "tool.yaml").exists():
                    seen.add(d.name)
                    _upsert_from_dir(scope, pid, d, reserved)
        with db.session() as s:
            q = select(db.ToolboxTool).where(db.ToolboxTool.scope == scope)
            q = q.where(db.ToolboxTool.project_id == pid) if pid else q.where(db.ToolboxTool.project_id.is_(None))
            for r in s.scalars(q):
                if r.name not in seen:
                    s.delete(r)
                    s.execute(text("DELETE FROM toolbox_fts WHERE kind='tool' AND ref=:r"), {"r": r.id})


def _upsert_from_dir(scope: str, pid: str | None, d: Path, reserved: set[str] | None) -> db.ToolboxTool:
    fh = files_hash(d)
    row = get(scope, pid, d.name)
    if row is not None and row.files_hash == fh and row.manifest:
        return row
    try:
        m = M.validate(M.parse((d / "tool.yaml").read_text()), reserved)
        err = None
    except M.ManifestError as e:
        m, err = None, str(e)
    with db.session() as s:
        if row is None:
            row = db.ToolboxTool(name=d.name, scope=scope, project_id=pid, path=rel_to_data(d), stats={})
            s.add(row)
        else:
            row = s.merge(row)
        row.files_hash = fh
        row.path = rel_to_data(d)
        if m:
            row.manifest, row.version, row.description = m, m["version"], m["description"]
            row.tags, row.author, row.deprecated = m.get("tags") or [], m.get("author") or "agent", m.get("deprecated")
            row.created_from_run = row.created_from_run or m.get("created_from_run")
            if row.test_status == "invalid":
                row.test_status = "untested"
        else:
            row.test_status, row.test_output, row.enabled = "invalid", err, 0
        row.updated_at = time.time()
        s.flush()
        rid = row.id
    readme = (d / "README.md").read_text(errors="replace")[:20000] if (d / "README.md").exists() else ""
    fts_put("tool", rid, scope, pid, d.name, (m or {}).get("description", ""), readme, " ".join((m or {}).get("tags") or []))
    return get(scope, pid, d.name)


def update_row(row_id: str, **fields) -> db.ToolboxTool:
    with db.session() as s:
        r = s.get(db.ToolboxTool, row_id)
        for k, v in fields.items():
            setattr(r, k, v)
        r.updated_at = time.time()
        return r


# ------------------------------------------------------------------------------------------ FTS
def fts_put(kind: str, ref: str, scope: str | None, project_id: str | None, name: str, description: str, body: str, tags: str) -> None:
    with db.session() as s:
        s.execute(text("DELETE FROM toolbox_fts WHERE kind=:k AND ref=:r"), {"k": kind, "r": ref})
        s.execute(text("INSERT INTO toolbox_fts(kind, ref, scope, project_id, name, description, body, tags) "
                       "VALUES (:k, :r, :s, :p, :n, :d, :b, :t)"),
                  {"k": kind, "r": ref, "s": scope or "global", "p": project_id or "", "n": name.replace("_", " ") + " " + name,
                   "d": description, "b": body, "t": tags})


def fts_delete(kind: str, ref: str) -> None:
    with db.session() as s:
        s.execute(text("DELETE FROM toolbox_fts WHERE kind=:k AND ref=:r"), {"k": kind, "r": ref})


STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "from", "by", "is", "it", "this", "that", "be", "as", "at",
        "into", "i", "we", "you", "my", "our", "make", "use", "using", "then", "do", "can", "new", "tool", "tools"}


def _terms(q: str) -> list[str]:
    return list(dict.fromkeys(w for w in re.findall(r"[A-Za-z0-9]+", (q or "").lower()) if w not in STOP and len(w) > 1))[:24]


def fts_query(q: str) -> str | None:
    words = _terms(q)
    if not words:
        return None
    return " OR ".join(f'"{w}"*' for w in words)


def search(query: str, project_id: str | None, kinds: tuple[str, ...] = ("tool", "skill", "plugin"), limit: int = 10) -> list[dict]:
    """[{kind, ref, name, scope, score, snippet, matched}], best first (bm25; name and description weigh most).

    Matching is OR over the query's terms (prefix, porter-stemmed), but a hit must cover enough of them:
    1–2 term queries need one term, longer queries at least two — a single shared common word is noise."""
    terms = _terms(query)
    fq = fts_query(query)
    if not fq:
        return []
    with db.session() as s:
        res = s.execute(text(
            "SELECT kind, ref, scope, project_id, name, bm25(toolbox_fts, 0, 0, 0, 0, 8.0, 4.0, 1.0, 3.0) AS score, "
            "snippet(toolbox_fts, 6, '[', ']', '…', 12) AS snip FROM toolbox_fts WHERE toolbox_fts MATCH :q "
            "AND (project_id = '' OR project_id = :p) ORDER BY score LIMIT :n"),
            {"q": fq, "p": project_id or "", "n": limit * 4}).all()
        cover: dict[tuple, int] = {}
        for t in terms:
            for kind, ref in s.execute(text("SELECT kind, ref FROM toolbox_fts WHERE toolbox_fts MATCH :q"), {"q": f'"{t}"*'}).all():
                cover[(kind, ref)] = cover.get((kind, ref), 0) + 1
    need = 1 if len(terms) <= 2 else 2
    out = []
    for kind, ref, scope, pid, name, score, snip in res:
        if kind not in kinds or cover.get((kind, ref), 0) < need:
            continue
        out.append({"kind": kind, "ref": ref, "scope": scope, "project_id": pid or None, "name": name.split(" ")[-1], "score": round(-score, 3),
                    "snippet": snip, "matched": cover.get((kind, ref), 0)})
    return out[:limit]


# ------------------------------------------------------------------------------------------ stats / health
def record_call(row: db.ToolboxTool, run_id: str | None, tool_call_id: str | None, ok: bool, duration: float, error: str | None,
                source: str = "agent", project_id: str | None = None) -> dict:
    """Update usage stats; returns the stats. A tool that fails FAIL_LIMIT times in a row is disabled."""
    with db.session() as s:
        s.add(db.ToolboxCall(tool_id=row.id, name=row.name, version=row.version, project_id=project_id or row.project_id, run_id=run_id,
                             tool_call_id=tool_call_id, source=source, status="success" if ok else "error", duration_s=duration,
                             error=(error or "")[:4000] or None))
        r = s.get(db.ToolboxTool, row.id)
        st = dict(r.stats or {})
        st["calls"] = st.get("calls", 0) + 1
        st["ok" if ok else "fail"] = st.get("ok" if ok else "fail", 0) + 1
        st["total_s"] = round(st.get("total_s", 0.0) + duration, 3)
        st["last_used"] = time.time()
        st["fail_streak"] = 0 if ok else st.get("fail_streak", 0) + 1
        if not ok:
            st["last_error"] = (error or "")[:1000]
        if run_id:
            runs = [x for x in st.get("runs", []) if x != run_id]
            st["runs"] = ([run_id] + runs)[:20]
        r.stats = st
        disabled = False
        if st["fail_streak"] >= FAIL_LIMIT and r.enabled:
            r.enabled = 0
            disabled = True
        r.updated_at = time.time()
    return {**st, "auto_disabled": disabled}


def recent_calls(tool_id: str, limit: int = 20) -> list[dict]:
    with db.session() as s:
        q = select(db.ToolboxCall).where(db.ToolboxCall.tool_id == tool_id).order_by(db.ToolboxCall.id.desc()).limit(limit)
        return [db.to_dict(c) for c in s.scalars(q)]


def failing_traces(tool_id: str, n: int = 3) -> list[str]:
    with db.session() as s:
        q = select(db.ToolboxCall).where(db.ToolboxCall.tool_id == tool_id, db.ToolboxCall.status == "error").order_by(db.ToolboxCall.id.desc()).limit(n)
        return [c.error or "" for c in s.scalars(q)]


# ------------------------------------------------------------------------------------------ audit
def audit(actor: str, action: str, target: str, run_id: str | None = None, detail: dict | None = None) -> None:
    with db.session() as s:
        s.add(db.ToolboxAudit(actor=actor, action=action, target=target, run_id=run_id, detail=detail))


def audit_log(limit: int = 200) -> list[dict]:
    with db.session() as s:
        return [db.to_dict(a) for a in s.scalars(select(db.ToolboxAudit).order_by(db.ToolboxAudit.id.desc()).limit(limit))]


def copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".ruff_cache", "*.pyc"))
