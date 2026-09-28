"""Engine plugins in ``toolbox/plugins/<name>/`` (fx, templates, audio instruments, QC checks)
and templates saved from successful project scenes.

Layout of a plugin::

    plugin.yaml        name, kind, version, description, author, created_from_run, params (templates)
    plugin.py          the code (fx / instrument / qc_check)      — or scene.py for templates
    __init__.py        generated: exposes the plugin's API
    test_plugin.py     pytest; must pass before the plugin is enabled
    README.md
    assets/ preview.mp4 thumb.png  (templates)

``registry.json`` (written here, read by ``luma_engine.plugins``) lists which plugins are enabled.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from sqlalchemy import select

from .. import db
from ..config import config
from . import scan as S
from . import store
from .lifecycle import ToolboxError, _perms, file_diff, run_tests

KINDS = ("fx", "template", "instrument", "qc_check")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,47}$")
KIND_HINT = {
    "fx": "an effect: functions that draw into a luma_engine Frame, e.g. `def apply(frame, t, **params)`",
    "instrument": "an audio instrument: e.g. `def synth(duration, sr=48000, **params) -> np.ndarray` (float32, -1..1)",
    "qc_check": "a QC check: `def check(video_path, info) -> {'name', 'pass', 'value', 'expected', 'detail', 'severity'}` "
                "(run by qc_report automatically)",
    "template": "a parameterised scene (use template_save)",
}


def pdir(name: str) -> Path:
    return store.plugins_dir() / name


def write_registry() -> None:
    reg = {}
    with db.session() as s:
        for p in s.scalars(select(db.Plugin)):
            reg[p.name] = {"kind": p.kind, "version": p.version, "enabled": bool(p.enabled)}
    f = store.plugins_dir() / "registry.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(reg, indent=1, sort_keys=True))
    os.chmod(tmp, 0o644)
    tmp.replace(f)


def public(p: db.Plugin) -> dict:
    d = db.to_dict(p)
    d.pop("test_output", None)
    return d


def get(name: str) -> db.Plugin | None:
    with db.session() as s:
        return s.scalars(select(db.Plugin).where(db.Plugin.name == name)).first()


def all_plugins(kind: str | None = None) -> list[dict]:
    with db.session() as s:
        q = select(db.Plugin).order_by(db.Plugin.name)
        if kind:
            q = q.where(db.Plugin.kind == kind)
        return [public(p) for p in s.scalars(q)]


def sync() -> None:
    root = store.plugins_dir()
    seen = set()
    if root.exists():
        for d in sorted(root.iterdir()):
            if (d / "plugin.yaml").exists():
                seen.add(d.name)
                _index(d)
    with db.session() as s:
        for p in s.scalars(select(db.Plugin)):
            if p.name not in seen:
                store.fts_delete("plugin", p.id)
                s.delete(p)
    write_registry()


def _index(d: Path, **fields) -> dict:
    meta = yaml.safe_load((d / "plugin.yaml").read_text()) or {}
    with db.session() as s:
        p = s.scalars(select(db.Plugin).where(db.Plugin.name == d.name)).first()
        if p is None:
            p = db.Plugin(name=d.name, kind=meta.get("kind", "fx"), path=store.rel_to_data(d))
            s.add(p)
        p.kind, p.version = meta.get("kind", p.kind), str(meta.get("version", p.version))
        p.description, p.author = str(meta.get("description", "")), meta.get("author", p.author or "agent")
        p.created_from_run = meta.get("created_from_run") or p.created_from_run
        p.meta = {k: meta.get(k) for k in ("params", "template", "api", "tags") if meta.get(k) is not None}
        for k, v in fields.items():
            setattr(p, k, v)
        p.updated_at = time.time()
        s.flush()
        out = public(p)
    readme = (d / "README.md").read_text(errors="replace")[:10000] if (d / "README.md").exists() else ""
    store.fts_put("plugin", out["id"], "global", None, d.name, out["description"], readme, f"{out['kind']} " + " ".join(meta.get("tags") or []))
    return out


def _commit(message: str, actor: str) -> str | None:
    try:
        return store.global_repo().commit(message, author="Luma agent" if actor.startswith("agent") else actor.split(":", 1)[-1])
    except Exception:  # noqa: BLE001
        return None


async def _test_and_enable(d: Path, name: str) -> dict:
    # LUMA_PLUGIN_TESTING lets the tests `from luma_engine.plugins import <name>` before it is enabled
    res = await run_tests(d, network=False, timeout=600, extra_env={"LUMA_PLUGIN_TESTING": name})
    rep = await asyncio.to_thread(S.scan_dir, d, False, True, ("plugin.py", "scene.py"))
    enabled = res["status"] == "passed" and rep.ok
    _index(d, test_status=res["status"], test_output=res["output"], enabled=1 if enabled else 0)
    write_registry()
    return {"test": res, "scan": rep.to_dict(), "enabled": enabled}


async def create(kind: str, name: str, code: str, tests: str, description: str, readme: str | None = None, params: dict | None = None,
                 run_id: str | None = None, actor: str = "system", author: str = "agent") -> dict:
    if kind not in KINDS or kind == "template":
        raise ToolboxError(f"kind must be one of fx, instrument, qc_check (use template_save for templates); got {kind!r}")
    if not NAME_RE.match(name or ""):
        raise ToolboxError(f"plugin name {name!r} must be snake_case (3–48 chars)")
    if name in _builtin_names():
        raise ToolboxError(f"{name!r} clashes with a luma_engine module; choose another name")
    if len((description or "").strip()) < 20:
        raise ToolboxError("description is required (what it does, when to use it)")
    if not re.search(r"^def test_\w+", tests or "", re.M):
        raise ToolboxError("tests must contain at least one pytest test (`def test_...`)")
    if kind == "qc_check" and not re.search(r"^def check\s*\(", code, re.M):
        raise ToolboxError("a qc_check plugin must define `def check(video_path, info)`")
    d = pdir(name)
    existing = get(name)
    before = {}
    version = "1.0.0"
    if d.exists():
        before = {f: (d / f).read_text(errors="replace") for f in ("plugin.yaml", "plugin.py", "test_plugin.py", "README.md") if (d / f).exists()}
        old = yaml.safe_load(before.get("plugin.yaml", "") or "{}") or {}
        from .manifest import bump

        version = bump(str(old.get("version", "1.0.0")), "minor")
    d.mkdir(parents=True, exist_ok=True)
    api = sorted(set(re.findall(r"^def ([a-zA-Z]\w*)\s*\(", code, re.M)))
    meta = {"name": name, "kind": kind, "version": version, "description": " ".join(description.split()), "author": author,
            "created_from_run": run_id, "api": api}
    if params:
        meta["params"] = params
    (d / "plugin.yaml").write_text(yaml.safe_dump(meta, sort_keys=False, allow_unicode=True))
    (d / "plugin.py").write_text(code)
    (d / "__init__.py").write_text(f'"""Toolbox plugin {name} ({kind}) — {meta["description"][:200]}"""\nfrom .plugin import *  # noqa: F401,F403\n'
                                   f"from .plugin import {', '.join(api) or '__name__ as _unused'}  # noqa: F401\n")
    (d / "test_plugin.py").write_text(tests)
    (d / "README.md").write_text(readme or f"# {name}\n\n*{kind} plugin* — {meta['description']}\n\n```python\nfrom luma_engine.plugins import {name}\n"
                                 f"{name}.{api[0] if api else 'apply'}(...)\n```\n")
    _perms(d, "global")
    res = await _test_and_enable(d, name)
    after = {f: (d / f).read_text(errors="replace") for f in ("plugin.yaml", "plugin.py", "test_plugin.py", "README.md")}
    commit = _commit(f"{'Update' if existing else 'Create'} {kind} plugin {name} v{version}", actor)
    store.audit(actor, "plugin_update" if existing else "plugin_create", f"plugin:{name}", run_id, {"kind": kind, "version": version,
                                                                                                  "enabled": res["enabled"], "commit": commit})
    return {"plugin": public(get(name)), **res, "diff": file_diff(before, after), "import": f"from luma_engine.plugins import {name}", "api": api,
            "commit": commit}


def _builtin_names() -> set[str]:
    eng = Path(__import__("luma_engine").__file__).parent
    return {p.stem for p in eng.glob("*.py")} | {p.name for p in eng.iterdir() if p.is_dir()} | {"plugins", "template_builder", "registry",
                                                                                                 "load", "list_plugins", "plugin_dirs"}


async def set_enabled(name: str, on: bool, actor: str = "system") -> dict:
    p = get(name)
    if p is None:
        raise ToolboxError(f"no plugin named {name}")
    if on:
        res = await _test_and_enable(config.data_dir / p.path, name)
        if not res["enabled"]:
            raise ToolboxError(f"{name} cannot be enabled: tests {res['test']['status']}, static checks {'ok' if res['scan']['ok'] else 'failed'}")
    else:
        _index(config.data_dir / p.path, enabled=0)
        write_registry()
    store.audit(actor, "plugin_enable" if on else "plugin_disable", f"plugin:{name}")
    return public(get(name))


def delete(name: str, actor: str = "system", run_id: str | None = None) -> None:
    p = get(name)
    if p is None:
        raise ToolboxError(f"no plugin named {name}")
    shutil.rmtree(config.data_dir / p.path, ignore_errors=True)
    with db.session() as s:
        r = s.get(db.Plugin, p.id)
        if r:
            s.delete(r)
    store.fts_delete("plugin", p.id)
    write_registry()
    _commit(f"Delete plugin {name}", actor)
    store.audit(actor, "plugin_delete", f"plugin:{name}", run_id)


def read(name: str) -> dict:
    p = get(name)
    if p is None:
        raise ToolboxError(f"no plugin named {name}")
    d = config.data_dir / p.path
    files = {f: (d / f).read_text(errors="replace") for f in ("plugin.yaml", "plugin.py", "scene.py", "test_plugin.py", "README.md") if (d / f).exists()}
    with db.session() as s:
        full = db.to_dict(s.get(db.Plugin, p.id))
    return {**full, "files": files, "assets": sorted(f.name for f in (d / "assets").iterdir()) if (d / "assets").exists() else []}


# ======================================================================================== templates
TEMPLATE_TEST = '''"""Generated: the template renders with its default params (low resolution, last frame)."""
import numpy as np


def test_template_renders_with_defaults():
    from luma_engine.plugins import {name}

    sc = {name}.build(width=320, height={h}, fps=12)
    last = np.asarray(sc.render(max(0.0, sc.duration - 1 / sc.fps), blur=False))
    assert last.ndim == 3 and last.shape[0] > 0
    assert float(last.std()) > 0.001, "the last frame is blank"
'''


async def template_save(pdir_: Path, project_id: str, name: str, scene_path: str, params_schema: dict, preview_video: str | None,
                        description: str, title: str | None = None, run_id: str | None = None, actor: str = "system",
                        author: str = "agent") -> dict:
    if not NAME_RE.match(name or ""):
        raise ToolboxError(f"template name {name!r} must be snake_case (3–48 chars)")
    if name in _builtin_names() or name in ("fan_unfold", "exploded_assembly", "stroke_reveal", "voiced_explainer"):
        raise ToolboxError(f"{name!r} clashes with a built-in name")
    src = (pdir_ / scene_path).resolve()
    if pdir_.resolve() not in src.parents or not src.is_file() or src.suffix != ".py":
        raise ToolboxError(f"scene_path {scene_path!r} must be a .py scene file in the workspace")
    try:
        Draft202012Validator.check_schema(params_schema)
    except SchemaError as e:
        raise ToolboxError(f"params_schema is not a valid JSON Schema: {e.message}") from None
    props = (params_schema or {}).get("properties") or {}
    if params_schema.get("type") != "object" or not props:
        raise ToolboxError("params_schema must be {type: object, properties: {...}} listing the template's parameters (logo, colours, fonts, timings)")
    nodef = [k for k, v in props.items() if "default" not in v]
    if nodef:
        raise ToolboxError(f"every template parameter needs a default so the template renders out of the box; missing: {', '.join(nodef)}")
    d = pdir(name)
    existing = get(name)
    if d.exists():
        shutil.rmtree(d)
    (d / "assets").mkdir(parents=True)
    shutil.copyfile(src, d / "scene.py")
    copied = []
    for k, v in props.items():
        dv = v.get("default")
        if isinstance(dv, str) and dv.startswith(("assets/", "fonts/")):
            f = (pdir_ / dv).resolve()
            if f.is_file() and pdir_.resolve() in f.parents:
                (d / Path(dv).parent).mkdir(parents=True, exist_ok=True)
                shutil.copyfile(f, d / dv)
                copied.append(dv)
    with db.session() as s:
        p = s.get(db.Project, project_id)
        st = dict(p.settings or {}) if p else {}
    tmeta = {"width": int(st.get("width", 1920)), "height": int(st.get("height", 1080)), "fps": int(st.get("fps", 60)),
             "duration": float(st.get("duration", 5.0)), "title": title or name.replace("_", " ").title(), "assets": copied,
             "from_project": project_id}
    if preview_video:
        pv = (pdir_ / preview_video).resolve()
        if pdir_.resolve() not in pv.parents or not pv.is_file():
            raise ToolboxError(f"preview_video {preview_video!r} is not a file in the workspace")
        if pv.stat().st_size > 60 * 1024 * 1024:
            raise ToolboxError("preview_video must be ≤ 60 MB — make a shorter / smaller preview (make_gif_or_webp or a scaled render)")
        shutil.copyfile(pv, d / "preview.mp4")
        tmeta["preview"] = "preview.mp4"
        dur = _probe_duration(d / "preview.mp4")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0.0, dur * 0.8):.3f}", "-i", str(d / "preview.mp4"), "-frames:v", "1",
                        "-vf", "scale=640:-2", str(d / "thumb.png")], capture_output=True, timeout=60)
        if (d / "thumb.png").exists():
            tmeta["thumbnail"] = "thumb.png"
    meta = {"name": name, "kind": "template", "version": "1.0.0" if not existing else _next(existing.version), "description": " ".join(description.split()),
            "author": author, "created_from_run": run_id, "params": params_schema, "template": tmeta, "api": ["build"]}
    (d / "plugin.yaml").write_text(yaml.safe_dump(meta, sort_keys=False, allow_unicode=True))
    (d / "__init__.py").write_text(f'"""Template {name}: {meta["description"][:200]}"""\nfrom luma_engine.plugins import template_builder\n\nbuild = template_builder(__file__)\n')
    h = max(2, int(round(320 * tmeta["height"] / max(1, tmeta["width"]) / 2)) * 2)
    (d / "test_plugin.py").write_text(TEMPLATE_TEST.replace("{name}", name).replace("{h}", str(h)))
    rows = "\n".join(f"| `{k}` | {v.get('type', '')} | `{json.dumps(v.get('default'))}` | {v.get('description', '')} |" for k, v in props.items())
    (d / "README.md").write_text(f"# {tmeta['title']}\n\n*Template* — {meta['description']}\n\nSaved from project `{project_id}`"
                                 f"{f' (run `{run_id}`)' if run_id else ''}; {tmeta['width']}×{tmeta['height']} @ {tmeta['fps']} fps, {tmeta['duration']} s.\n\n"
                                 f"| param | type | default | |\n|---|---|---|---|\n{rows}\n\n```python\nfrom luma_engine.plugins import {name}\n"
                                 f"scene = {name}.build(logo=\"assets/your-logo.svg\")\n```\n")
    _perms(d, "global")
    res = await _test_and_enable(d, name)
    commit = _commit(f"Save template {name} v{meta['version']} from project {project_id}", actor)
    store.audit(actor, "template_save", f"plugin:{name}", run_id, {"version": meta["version"], "enabled": res["enabled"], "commit": commit})
    return {"plugin": public(get(name)), **res, "template": tmeta, "import": f"from luma_engine.plugins import {name}"}


def _next(v: str) -> str:
    from .manifest import bump

    return bump(v, "minor")


def _probe_duration(p: Path) -> float:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(p)], capture_output=True, text=True, timeout=30)
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:  # noqa: BLE001
        return 0.0


def templates() -> list[dict]:
    """Built-in templates + enabled template plugins, for the gallery."""
    out = [{"name": n, "source": "builtin", "title": n.replace("_", " ").title(), "description": d, "enabled": True}
           for n, d in BUILTIN_TEMPLATES.items()]
    for p in all_plugins("template"):
        t = (p.get("meta") or {}).get("template") or {}
        out.append({"name": p["name"], "source": "plugin", "title": t.get("title") or p["name"], "description": p["description"], "version": p["version"],
                    "enabled": bool(p["enabled"]), "preview": f"/api/toolbox/plugins/{p['name']}/file/preview.mp4" if t.get("preview") else None,
                    "thumbnail": f"/api/toolbox/plugins/{p['name']}/file/thumb.png" if t.get("thumbnail") else None, "meta": t,
                    "params": (p.get("meta") or {}).get("params"), "created_from_run": p.get("created_from_run")})
    return out


BUILTIN_TEMPLATES = {
    "fan_unfold": "Light-born fan unfold: parts swing open around the hinge on springs, ignite, shockwave, lockup glide, exact hold.",
    "exploded_assembly": "Exploded assembly: parts fly in from an exploded 3D arrangement and seat into the mark.",
    "stroke_reveal": "Stroke reveal: outlines write on with glowing pen tips, then fill and settle into the lockup.",
    "voiced_explainer": "Voiced explainer: logo reveal with kinetic captions driven by a voice-over.",
}


def use_template(name: str, owner_id: str | None, project_name: str | None = None, params: dict | None = None) -> dict:
    """A new project started from a template: its assets, a work/scene.py that builds the template with
    editable PARAMS, output settings from the template."""
    from ..routes_projects import add_asset_bytes, ensure_workspace

    if name in BUILTIN_TEMPLATES:
        raise ToolboxError("built-in templates are started from the chat (\"use the fan_unfold template…\") or the demo")
    p = get(name)
    if p is None or not p.enabled:
        raise ToolboxError(f"no enabled template named {name}")
    d = config.data_dir / p.path
    meta = yaml.safe_load((d / "plugin.yaml").read_text()) or {}
    t = meta.get("template") or {}
    props = ((meta.get("params") or {}).get("properties") or {})
    values = {k: v.get("default") for k, v in props.items()}
    values.update(params or {})
    from ..config import DEFAULT_PROJECT_SETTINGS

    settings = {**DEFAULT_PROJECT_SETTINGS, **{k: t[k] for k in ("width", "height", "fps", "duration") if k in t}}
    with db.session() as s:
        proj = db.Project(name=project_name or t.get("title") or name, settings=settings, owner_id=owner_id,
                          brief=f"Started from the template **{t.get('title') or name}** (v{meta.get('version')}): {meta.get('description', '')}\n"
                                f"Template parameters are in work/scene.py (PARAMS).")
        s.add(proj)
    pdir_ = ensure_workspace(proj.id)
    for rel in t.get("assets") or []:
        f = d / rel
        if f.is_file():
            if rel.startswith("assets/"):
                a = add_asset_bytes(proj.id, f.name, f.read_bytes())
                for k, v in values.items():
                    if v == rel:
                        values[k] = a["path"]
            else:
                (pdir_ / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(f, pdir_ / rel)
    scene = (f'"""Started from the toolbox template `{name}` v{meta.get("version")}.\n\n{meta.get("description", "")}\n\n'
             f'Edit PARAMS (see the template README for each parameter) or copy the template\'s scene code to customise it."""\n'
             f"from luma_engine.plugins import {name}\n\nPARAMS = {json.dumps(values, indent=4)}\n\n\n"
             f"def build(**overrides):\n    return {name}.build(**{{**PARAMS, **{{k: v for k, v in overrides.items() if v is not None}}}})\n")
    (pdir_ / "work" / "scene.py").write_text(scene)
    try:
        os.chmod(pdir_ / "work" / "scene.py", 0o664)
    except OSError:
        pass
    return {"project_id": proj.id, "scene": "work/scene.py", "params": values}
