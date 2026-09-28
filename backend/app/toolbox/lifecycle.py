"""Tool lifecycle: create → test → register → call → update → promote (and deprecate, delete,
enable/disable, rollback).  Used by the agent's toolbox tools and by the REST API (tools a user
authors by hand go through exactly the same validation, tests and scan, marked ``author: user``).
"""
from __future__ import annotations

import asyncio
import base64
import difflib
import re
import shutil
import tempfile
import time
from pathlib import Path

from .. import db
from ..config import config
from ..secrets_store import redact
from ..terminal import nonet_group, sandbox_argv, sandbox_env
from . import manifest as M
from . import scan as S
from . import store, venv
from .gitrepo import GitError

CODE_FILES = ("tool.yaml", "main.py", "test_tool.py", "README.md")
MAX_TEST_S = 900


class ToolboxError(Exception):
    """An expected failure; the message goes back to the model / user verbatim."""


def reserved_names() -> set[str]:
    from ..agent.tools.base import REGISTRY

    return set(REGISTRY)


def _check_files(main_py: str, test_py: str, readme: str) -> list[str]:
    errs = []
    if not re.search(r"^def run\s*\(\s*params\b[^)]*,\s*ctx\b", main_py or "", re.M):
        errs.append("main.py must define `def run(params, ctx)` returning a dict")
    if not re.search(r"^def test_\w+", test_py or "", re.M):
        errs.append("test_tool.py must contain at least one pytest test (`def test_...`) using small fixtures")
    if len((readme or "").strip()) < 80:
        errs.append("README.md must explain what/why/how, with an example and limitations (≥ 80 characters)")
    return errs


def _write_fixtures(fdir: Path, fixtures: dict | None, pdir: Path | None) -> list[str]:
    written = []
    for name, spec in (fixtures or {}).items():
        if "/" in name or name.startswith(".") or not re.match(r"^[\w.\-]{1,80}$", name):
            raise ToolboxError(f"fixture name {name!r} must be a plain file name")
        fdir.mkdir(parents=True, exist_ok=True)
        dst = fdir / name
        if isinstance(spec, str):
            spec = {"text": spec}
        if "text" in spec:
            dst.write_text(spec["text"])
        elif "base64" in spec:
            dst.write_bytes(base64.b64decode(spec["base64"]))
        elif "from" in spec:
            if pdir is None:
                raise ToolboxError("fixtures `from` needs a project workspace")
            src = (pdir / spec["from"]).resolve()
            if pdir.resolve() not in src.parents or not src.is_file():
                raise ToolboxError(f"fixture source {spec['from']!r} is not a file in the workspace")
            shutil.copyfile(src, dst)
        else:
            raise ToolboxError(f"fixture {name!r}: give text, base64 or from (a workspace path)")
        written.append(name)
    return written


def file_diff(old: dict[str, str], new: dict[str, str]) -> list[dict]:
    out = []
    for name in sorted(set(old) | set(new)):
        a, b = old.get(name), new.get(name)
        if a == b:
            continue
        d = "".join(difflib.unified_diff((a or "").splitlines(True), (b or "").splitlines(True), f"a/{name}" if a is not None else "/dev/null",
                                         f"b/{name}" if b is not None else "/dev/null"))
        out.append({"file": name, "status": "added" if a is None else "deleted" if b is None else "modified", "diff": d[:60000]})
    return out


def read_files(d: Path) -> dict[str, str]:
    return {n: (d / n).read_text(errors="replace") for n in CODE_FILES if (d / n).exists()}


def _actor_label(actor: str) -> str:
    return actor


def _author_name(actor: str) -> str:
    return "Luma agent" if actor.startswith("agent") else actor.split(":", 1)[-1] if ":" in actor else actor


# ------------------------------------------------------------------------------------------ tests
async def run_tests(d: Path, network: bool = False, timeout: float = 300, extra_env: dict | None = None) -> dict:
    """pytest for a tool/plugin directory, in the sandbox, with the tool venv."""
    if not venv.ready.wait(timeout=180):
        return {"status": "failed", "passed": 0, "failed": 0, "output": "the tool environment (/data/venv) is not ready"}
    tests = sorted(p.name for p in d.glob("test_*.py"))
    if not tests:
        return {"status": "failed", "passed": 0, "failed": 0, "output": "no test_*.py file"}
    env = sandbox_env(str(d), {"PYTHONDONTWRITEBYTECODE": "1", "OPENBLAS_NUM_THREADS": "2", "PYTEST_ADDOPTS": "", **(extra_env or {})})
    argv = sandbox_argv([venv.python(), "-m", "pytest", "-q", "-p", "no:cacheprovider", "--no-header", "-rfE", "--tb=short",
                         "--rootdir", str(d), *tests], env, group=None if network else nonet_group())
    t0 = time.monotonic()
    proc = await asyncio.create_subprocess_exec(*argv, cwd=str(d), env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                                                start_new_session=True)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), min(MAX_TEST_S, max(60.0, timeout)))
        text = out.decode(errors="replace")
    except asyncio.TimeoutError:
        from .execute import _kill

        _kill(proc)
        return {"status": "failed", "passed": 0, "failed": 0, "output": f"tests timed out after {timeout:.0f}s", "seconds": round(time.monotonic() - t0, 1)}
    text = redact(text)
    passed = sum(int(x) for x in re.findall(r"(\d+) passed", text))
    failed = sum(int(x) for x in re.findall(r"(\d+) (?:failed|errors?)", text))
    ok = proc.returncode == 0 and passed > 0
    return {"status": "passed" if ok else "failed", "passed": passed, "failed": failed, "output": text[-12000:],
            "seconds": round(time.monotonic() - t0, 1), "exit_code": proc.returncode}


async def test(row: db.ToolboxTool) -> dict:
    d = config.data_dir / row.path
    row = store._upsert_from_dir(row.scope, row.project_id, d, reserved_names())
    if row.test_status == "invalid":
        return {"status": "invalid", "output": row.test_output, "passed": 0, "failed": 0}
    h = store.files_hash(d)
    res = await run_tests(d, network=bool((row.manifest or {}).get("network")) and bool(_settings().get("toolbox_network")),
                          timeout=float((row.manifest or {}).get("timeout_s") or 300) * 2)
    fields = {"test_status": res["status"], "test_output": res["output"], "tested_hash": h if res["status"] == "passed" else None, "files_hash": h}
    if res["status"] != "passed" and row.enabled:
        fields["enabled"] = 0
        res["disabled"] = True
    store.update_row(row.id, **fields)
    return res


def _settings() -> dict:
    from ..routes_settings import load_settings

    return load_settings()


# ------------------------------------------------------------------------------------------ create
async def create(scope: str, project_id: str | None, name: str, manifest: dict | str, main_py: str, test_py: str, readme: str,
                 fixtures: dict | None = None, author: str = "agent", run_id: str | None = None, actor: str = "system",
                 pdir: Path | None = None) -> dict:
    if isinstance(manifest, str):
        manifest = M.parse(manifest)
    manifest = dict(manifest or {})
    manifest.update({"name": name, "scope": scope, "author": author})
    manifest.setdefault("version", "1.0.0")
    if run_id:
        manifest["created_from_run"] = run_id
    else:
        manifest.setdefault("created_from_run", None)
    try:
        manifest = M.validate(manifest, reserved_names())
    except M.ManifestError as e:
        raise ToolboxError("tool.yaml is invalid:\n- " + "\n- ".join(e.errors)) from None
    errs = _check_files(main_py, test_py, readme)
    if errs:
        raise ToolboxError("cannot create the tool:\n- " + "\n- ".join(errs))
    store.sync(project_id, reserved_names())
    if store.get(scope, project_id, name) is not None:
        raise ToolboxError(f"a {scope} tool named {name} already exists — use tool_update (or pick another name)")
    root = store.tools_root(scope, project_id)
    root.mkdir(parents=True, exist_ok=True)
    d = root / name
    if d.exists():
        shutil.rmtree(d)
    stage = Path(tempfile.mkdtemp(prefix=f".{name}-", dir=root))
    try:
        (stage / "tool.yaml").write_text(M.dump(manifest))
        (stage / "main.py").write_text(main_py)
        (stage / "test_tool.py").write_text(test_py)
        (stage / "README.md").write_text(readme)
        fx = _write_fixtures(stage / "fixtures", fixtures, pdir)
        stage.rename(d)
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
    _perms(d, scope)
    report = await asyncio.to_thread(S.scan_dir, d, bool(manifest.get("network")))
    deps_ok, deps_out = await asyncio.to_thread(venv.install, manifest.get("dependencies") or [])
    row = store._upsert_from_dir(scope, project_id, d, reserved_names())
    store.update_row(row.id, scan=report.to_dict(), author=author, created_from_run=manifest.get("created_from_run"))
    t = await test(row) if deps_ok else {"status": "failed", "output": "dependency install failed:\n" + deps_out, "passed": 0, "failed": 0}
    if not deps_ok:
        store.update_row(row.id, test_status="failed", test_output=t["output"])
    files = read_files(d)
    commit = _commit(scope, project_id, f"Create tool {name} v{manifest['version']} ({author})", actor)
    if scope == "global":
        store.audit(actor, "create", f"tool:{name}", run_id, {"version": manifest["version"], "commit": commit})
    row = store.get(scope, project_id, name)
    return {"tool": store.public(row), "scan": report.to_dict(), "deps": {"ok": deps_ok, "installed": manifest.get("dependencies") or [],
            "output": deps_out[-2000:]}, "test": t, "fixtures": fx, "diff": file_diff({}, files), "commit": commit}


def _perms(d: Path, scope: str) -> None:
    import os

    mode_d, mode_f = (0o2755, 0o644) if scope == "global" else (0o2775, 0o664)
    for p in [d, *d.rglob("*")]:
        try:
            os.chmod(p, mode_d if p.is_dir() else mode_f)
        except OSError:
            pass


def _commit(scope: str, project_id: str | None, message: str, actor: str, tag: str | None = None) -> str | None:
    repo, _ = store.repo_for(scope, project_id)
    try:
        sha = repo.commit(message, author=_author_name(actor))
        if tag:
            repo.tag(tag)
        return sha
    except GitError as e:  # versioning must never break the tool itself
        store.log.warning("toolbox git: %s", e)
        return None


# ------------------------------------------------------------------------------------------ register
async def register(row: db.ToolboxTool, run_id: str | None = None, actor: str = "system") -> dict:
    d = config.data_dir / row.path
    row = store._upsert_from_dir(row.scope, row.project_id, d, reserved_names())
    if row.test_status == "invalid":
        raise ToolboxError(f"{row.name}: tool.yaml is invalid — {row.test_output}")
    rep = await asyncio.to_thread(S.scan_dir, d, bool((row.manifest or {}).get("network")), False)
    store.update_row(row.id, scan=rep.to_dict())
    if not rep.ok:
        raise ToolboxError(f"{row.name} cannot be registered — the static checks found problems:\n{rep.text()}\nFix them with tool_update.")
    h = store.files_hash(d)
    t = None
    if row.test_status != "passed" or row.tested_hash != h:
        t = await test(row)
        row = store.get(row.scope, row.project_id, row.name)
    if row.test_status != "passed":
        raise ToolboxError(f"{row.name} cannot be registered: its tests do not pass.\n{(row.test_output or '')[-3000:]}")
    stats = dict(row.stats or {})
    stats["fail_streak"] = 0
    store.update_row(row.id, enabled=1, stats=stats)
    tag = f"{row.name}@{row.version}"
    commit = _commit(row.scope, row.project_id, f"Register {tag}", actor, tag=tag)
    if row.scope == "global":
        store.audit(actor, "register", f"tool:{row.name}", run_id, {"version": row.version, "commit": commit})
    return {"tool": store.public(store.get(row.scope, row.project_id, row.name)), "tag": tag, "test": t}


async def set_enabled(row: db.ToolboxTool, on: bool, actor: str = "system", run_id: str | None = None, reason: str = "") -> dict:
    if on:
        return await register(row, run_id, actor)
    store.update_row(row.id, enabled=0)
    if row.scope == "global":
        store.audit(actor, "disable", f"tool:{row.name}", run_id, {"reason": reason})
    return {"tool": store.public(store.get(row.scope, row.project_id, row.name))}


# ------------------------------------------------------------------------------------------ update / rollback
async def update(row: db.ToolboxTool, changes: dict, bump: str = "patch", reason: str = "", run_id: str | None = None,
                 actor: str = "system", pdir: Path | None = None) -> dict:
    d = config.data_dir / row.path
    before = read_files(d)
    old = dict(row.manifest or M.parse(before.get("tool.yaml", "{}")))
    was_enabled = bool(row.enabled)
    m = dict(old)
    if changes.get("manifest"):
        patch = changes["manifest"] if isinstance(changes["manifest"], dict) else M.parse(changes["manifest"])
        for k in ("name", "scope", "author", "created_from_run"):
            patch.pop(k, None)
        m.update(patch)
    new_version = m.get("version") if M.SEMVER_RE.match(str(m.get("version"))) and M.newer(str(m.get("version")), old.get("version", "0.0.0")) \
        else M.bump(old.get("version", "1.0.0"), bump)
    m["version"] = new_version
    try:
        m = M.validate(m, reserved_names())
    except M.ManifestError as e:
        raise ToolboxError("tool.yaml is invalid:\n- " + "\n- ".join(e.errors)) from None
    main_py = changes.get("main_py", before.get("main.py", ""))
    test_py = changes.get("test_py", before.get("test_tool.py", ""))
    readme = changes.get("readme", before.get("README.md", ""))
    errs = _check_files(main_py, test_py, readme)
    if errs:
        raise ToolboxError("cannot update the tool:\n- " + "\n- ".join(errs))
    if not any(k in changes for k in ("main_py", "test_py", "readme", "manifest", "fixtures", "remove_fixtures")):
        raise ToolboxError("nothing to change: pass main_py, test_py, readme, manifest and/or fixtures")
    (d / "tool.yaml").write_text(M.dump(m))
    (d / "main.py").write_text(main_py)
    (d / "test_tool.py").write_text(test_py)
    (d / "README.md").write_text(readme)
    for n in changes.get("remove_fixtures") or []:
        f = d / "fixtures" / Path(n).name
        if f.exists():
            f.unlink()
    _write_fixtures(d / "fixtures", changes.get("fixtures"), pdir)
    _perms(d, row.scope)
    report = await asyncio.to_thread(S.scan_dir, d, bool(m.get("network")))
    deps_changed = sorted(m.get("dependencies") or []) != sorted(old.get("dependencies") or [])
    deps_ok, deps_out = (await asyncio.to_thread(venv.install, m.get("dependencies") or [])) if deps_changed else (True, "unchanged")
    row = store._upsert_from_dir(row.scope, row.project_id, d, reserved_names())
    store.update_row(row.id, scan=report.to_dict())
    t = await test(row) if deps_ok else {"status": "failed", "output": "dependency install failed:\n" + deps_out}
    after = read_files(d)
    msg = f"Update {row.name} to v{new_version}" + (f": {reason}" if reason else "")
    commit = _commit(row.scope, row.project_id, msg, actor)
    reg = None
    if t["status"] == "passed" and report.ok and was_enabled:
        reg = await register(store.get(row.scope, row.project_id, row.name), run_id, actor)
    elif was_enabled:
        store.update_row(row.id, enabled=0)
    if row.scope == "global":
        store.audit(actor, "update", f"tool:{row.name}", run_id, {"version": new_version, "reason": reason, "commit": commit})
    return {"tool": store.public(store.get(row.scope, row.project_id, row.name)), "version": new_version, "previous": old.get("version"),
            "scan": report.to_dict(), "test": t, "registered": bool(reg), "was_enabled": was_enabled, "diff": file_diff(before, after),
            "commit": commit}


def versions(row: db.ToolboxTool) -> list[dict]:
    repo, prefix = store.repo_for(row.scope, row.project_id)
    try:
        repo.ensure()
        tags = repo.tags(f"{row.name}@")
        log = repo.log(f"{prefix}/{row.name}")
    except GitError:
        return []
    by_sha = {}
    for t in tags:
        sha = repo.git("rev-list", "-n1", t, check=False).strip()
        by_sha.setdefault(sha, []).append(t.split("@", 1)[1])
    for e in log:
        e["versions"] = by_sha.get(e["sha"], [])
    return log


def diff(row: db.ToolboxTool, a: str, b: str | None = None) -> str:
    repo, prefix = store.repo_for(row.scope, row.project_id)
    ra = f"{row.name}@{a}" if M.SEMVER_RE.match(a) else a
    rb = (f"{row.name}@{b}" if M.SEMVER_RE.match(b) else b) if b else None
    return repo.diff(ra, rb, f"{prefix}/{row.name}")


async def rollback(row: db.ToolboxTool, version: str, run_id: str | None = None, actor: str = "system") -> dict:
    repo, prefix = store.repo_for(row.scope, row.project_id)
    tag = f"{row.name}@{version}"
    if tag not in repo.tags(f"{row.name}@"):
        raise ToolboxError(f"no version {version} of {row.name}; versions: {', '.join(t.split('@')[1] for t in repo.tags(row.name + '@')) or 'none'}")
    d = config.data_dir / row.path
    before = read_files(d)
    current = row.version
    repo.restore(tag, f"{prefix}/{row.name}")
    m = M.parse((d / "tool.yaml").read_text())
    m["version"] = M.bump(current, "patch")
    (d / "tool.yaml").write_text(M.dump(m))
    _perms(d, row.scope)
    row = store._upsert_from_dir(row.scope, row.project_id, d, reserved_names())
    t = await test(row)
    commit = _commit(row.scope, row.project_id, f"Roll {row.name} back to v{version} (as v{m['version']})", actor)
    reg = None
    if t["status"] == "passed":
        reg = await register(store.get(row.scope, row.project_id, row.name), run_id, actor)
    if row.scope == "global":
        store.audit(actor, "rollback", f"tool:{row.name}", run_id, {"to": version, "as": m["version"], "commit": commit})
    return {"tool": store.public(store.get(row.scope, row.project_id, row.name)), "restored": version, "version": m["version"], "test": t,
            "registered": bool(reg), "diff": file_diff(before, read_files(d))}


# ------------------------------------------------------------------------------------------ promote
def project_specifics(project_id: str) -> tuple[set[str], set[str], set[str]]:
    """Brand colours, asset file names and names that must not be hard-coded in a global tool."""
    from sqlalchemy import select

    colors: set[str] = set()
    assets: set[str] = set()
    names: set[str] = set()
    with db.session() as s:
        p = s.get(db.Project, project_id)
        if p:
            names.add(p.name)
            colors |= set(S.HEX.findall(p.brief or ""))
            for w in re.findall(r"\b[A-Z][a-z]{3,}\b", p.name or ""):
                names.add(w)
        for m in s.scalars(select(db.Memory).where(db.Memory.project_id == project_id)):
            colors |= set(S.HEX.findall(m.value or ""))
        for a in s.scalars(select(db.Asset).where(db.Asset.project_id == project_id)):
            assets.add(a.filename)
            colors |= set(S.HEX.findall(str(a.analysis or "")))
    generic = {"Untitled", "Project", "Test", "Demo"}
    return colors, assets, {n for n in names if n not in generic}


def promotion_findings(row: db.ToolboxTool) -> tuple[list[S.Finding], S.Report]:
    d = config.data_dir / row.path
    colors, assets, names = project_specifics(row.project_id)
    rep = S.scan_dir(d, bool((row.manifest or {}).get("network")), fix=False)
    return S.promotion_scan(d, colors, assets, names), rep


def promotion_preview(row: db.ToolboxTool) -> dict:
    d = config.data_dir / row.path
    g = store.get("global", None, row.name)
    old = read_files(config.data_dir / g.path) if g else {}
    new = read_files(d)
    found, rep = promotion_findings(row)
    return {"diff": file_diff(old, new), "readme": new.get("README.md", ""), "tests": {"status": row.test_status, "output": (row.test_output or "")[-4000:]},
            "findings": [f.__dict__ for f in found] + [f.__dict__ for f in rep.blocking], "replaces_global": g.version if g else None}


async def promote(row: db.ToolboxTool, run_id: str | None = None, actor: str = "system") -> dict:
    if row.scope != "project":
        raise ToolboxError(f"{row.name} is already global")
    found, rep = await asyncio.to_thread(promotion_findings, row)
    if found or not rep.ok:
        lines = [f"- {f.file}:{f.line} [{f.rule}] {f.message}" for f in found + rep.blocking]
        raise ToolboxError(f"{row.name} cannot be promoted yet — it contains project-specific values or unsafe code:\n" + "\n".join(lines) +
                           "\nParameterize them (take paths, colours and names as params with no brand-specific defaults), "
                           "update the tool with tool_update, then promote again.")
    if row.test_status != "passed" or row.tested_hash != store.files_hash(config.data_dir / row.path):
        t = await test(row)
        if t["status"] != "passed":
            raise ToolboxError(f"{row.name}'s tests must pass before promotion:\n{t['output'][-2000:]}")
    src = config.data_dir / row.path
    g = store.get("global", None, row.name)
    dst = store.tool_dir("global", None, row.name)
    before = read_files(dst) if dst.exists() else {}
    store.copy_tree(src, dst)
    m = M.parse((dst / "tool.yaml").read_text())
    m["scope"] = "global"
    if g and not M.newer(m["version"], g.version):
        m["version"] = M.bump(g.version, "minor")
    (dst / "tool.yaml").write_text(M.dump(m))
    _perms(dst, "global")
    grow = store._upsert_from_dir("global", None, dst, reserved_names())
    # the tool keeps its history: usage stats and call log move with it
    st = dict(grow.stats or {}) if g else {}
    for k, v in (row.stats or {}).items():
        if isinstance(v, (int, float)) and k != "fail_streak":
            st[k] = st.get(k, 0) + v if k != "last_used" else max(st.get(k, 0), v)
        elif k == "runs":
            st["runs"] = list(dict.fromkeys((row.stats or {}).get("runs", []) + st.get("runs", [])))[:20]
        elif k not in st:
            st[k] = v
    store.update_row(grow.id, author=row.author, created_from_run=row.created_from_run, stats=st)
    with db.session() as s:
        s.query(db.ToolboxCall).filter(db.ToolboxCall.tool_id == row.id).update({"tool_id": grow.id})
    t = await test(grow)
    commit = _commit("global", None, f"Promote {row.name} v{m['version']} from project {row.project_id}", actor)
    reg = None
    if t["status"] == "passed":
        reg = await register(store.get("global", None, row.name), run_id, actor)
    store.audit(actor, "promote", f"tool:{row.name}", run_id, {"version": m["version"], "from_project": row.project_id, "commit": commit,
                                                                "replaced": g.version if g else None})
    # the project copy would shadow the global one — remove it (its history stays in the project repo)
    shutil.rmtree(src, ignore_errors=True)
    _commit("project", row.project_id, f"{row.name} promoted to the global toolbox (v{m['version']})", actor)
    store.sync(row.project_id, reserved_names())
    return {"tool": store.public(store.get("global", None, row.name)), "version": m["version"], "registered": bool(reg), "test": t,
            "diff": file_diff(before, read_files(dst)), "commit": commit}


# ------------------------------------------------------------------------------------------ deprecate / delete
async def deprecate(row: db.ToolboxTool, reason: str, replacement: str | None, run_id: str | None = None, actor: str = "system") -> dict:
    d = config.data_dir / row.path
    m = M.parse((d / "tool.yaml").read_text())
    m["deprecated"] = {"reason": reason, "replacement": replacement}
    (d / "tool.yaml").write_text(M.dump(m))
    row = store._upsert_from_dir(row.scope, row.project_id, d, reserved_names())
    # a metadata-only change keeps the tested state valid
    store.update_row(row.id, tested_hash=store.files_hash(d) if row.test_status == "passed" else row.tested_hash)
    commit = _commit(row.scope, row.project_id, f"Deprecate {row.name}: {reason}", actor)
    if row.scope == "global":
        store.audit(actor, "deprecate", f"tool:{row.name}", run_id, {"reason": reason, "replacement": replacement, "commit": commit})
    return {"tool": store.public(store.get(row.scope, row.project_id, row.name))}


async def delete(row: db.ToolboxTool, run_id: str | None = None, actor: str = "system") -> dict:
    d = config.data_dir / row.path
    shutil.rmtree(d, ignore_errors=True)
    commit = _commit(row.scope, row.project_id, f"Delete tool {row.name} v{row.version}", actor)
    with db.session() as s:
        r = s.get(db.ToolboxTool, row.id)
        if r:
            s.delete(r)
    store.fts_delete("tool", row.id)
    if row.scope == "global":
        store.audit(actor, "delete", f"tool:{row.name}", run_id, {"version": row.version, "commit": commit})
    return {"deleted": row.name, "scope": row.scope, "commit": commit}


def read(row: db.ToolboxTool) -> dict:
    d = config.data_dir / row.path
    fx = []
    if (d / "fixtures").exists():
        fx = [{"name": f.name, "size": f.stat().st_size} for f in sorted((d / "fixtures").iterdir()) if f.is_file()]
    return {**read_files(d), "fixtures": fx}
