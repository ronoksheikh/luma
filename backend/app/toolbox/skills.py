"""Skills: written playbooks the agent learns (``toolbox/skills/<name>/SKILL.md``).

```markdown
---
name: logo_split_exact
description: When to use it — one or two sentences the retriever and the model read first.
tags: [svg, geometry]
tools_used: [svg_split_exact]
created_from_run: r_…
---
## When to use
## Steps
## Pitfalls
## Verification
## Example
```
"""
from __future__ import annotations

import re
import time

import yaml
from sqlalchemy import select

from .. import db
from ..config import config
from . import store

SECTIONS = ("When to use", "Steps", "Pitfalls", "Verification", "Example")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


class SkillError(ValueError):
    pass


def split(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text or "", re.S)
    if not m:
        raise SkillError("SKILL.md must start with YAML front-matter between --- lines (name, description, tags, tools_used)")
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise SkillError(f"front-matter is not valid YAML: {e}") from None
    if not isinstance(fm, dict):
        raise SkillError("front-matter must be a mapping")
    return fm, m.group(2)


def validate(name: str, content: str) -> tuple[dict, str]:
    if not NAME_RE.match(name or ""):
        raise SkillError(f"skill name {name!r} must be snake_case (3–64 chars)")
    fm, body = split(content)
    errs = []
    if fm.get("name") not in (None, name):
        errs.append(f"front-matter name {fm.get('name')!r} differs from {name!r}")
    if len(str(fm.get("description") or "").strip()) < 20:
        errs.append("description (when to use this skill) is required — at least a sentence")
    for k in ("tags", "tools_used"):
        if k in fm and not (isinstance(fm[k], list) and all(isinstance(x, str) for x in fm[k])):
            errs.append(f"{k} must be a list of strings")
    have = {h.strip().lower() for h in re.findall(r"^#{2,3}\s+(.+?)\s*$", body, re.M)}
    missing = [s for s in SECTIONS if s.lower() not in have]
    if missing:
        errs.append("missing sections: " + ", ".join(f"## {s}" for s in missing))
    if len(body.strip()) < 200:
        errs.append("the body is too short to be a useful playbook (concrete steps, pitfalls and a verification)")
    if errs:
        raise SkillError("invalid SKILL.md:\n- " + "\n- ".join(errs))
    fm["name"] = name
    fm.setdefault("tags", [])
    fm.setdefault("tools_used", [])
    return fm, body


def render(fm: dict, body: str) -> str:
    keys = ["name", "description", "tags", "tools_used", "created_from_run"]
    head = {k: fm[k] for k in keys if fm.get(k) is not None}
    head.update({k: v for k, v in fm.items() if k not in head and v is not None})
    return "---\n" + yaml.safe_dump(head, sort_keys=False, allow_unicode=True, width=110) + "---\n\n" + body.lstrip("\n")


def path_of(name: str):
    return store.skills_dir() / name / "SKILL.md"


def write(name: str, content: str, author: str = "agent", run_id: str | None = None, actor: str = "system") -> dict:
    fm, body = validate(name, content)
    p = path_of(name)
    existed = p.exists()
    if run_id and not fm.get("created_from_run"):
        fm["created_from_run"] = run_id
    old = p.read_text() if existed else ""
    p.parent.mkdir(parents=True, exist_ok=True)
    text = render(fm, body)
    p.write_text(text)
    for q in (p.parent, p):
        try:
            import os

            os.chmod(q, 0o2755 if q.is_dir() else 0o644)
        except OSError:
            pass
    row = index(name, author if not existed else None)
    try:
        store.global_repo().commit(f"{'Update' if existed else 'Write'} skill {name}", author="Luma agent" if actor.startswith("agent") else actor)
    except Exception:  # noqa: BLE001
        pass
    store.audit(actor, "skill_update" if existed else "skill_write", f"skill:{name}", run_id, {"chars": len(text)})
    from .lifecycle import file_diff

    return {"skill": row, "created": not existed, "diff": file_diff({"SKILL.md": old} if existed else {}, {"SKILL.md": text})}


def index(name: str, author: str | None = None) -> dict:
    p = path_of(name)
    fm, body = split(p.read_text())
    with db.session() as s:
        r = s.scalars(select(db.Skill).where(db.Skill.name == name)).first()
        if r is None:
            r = db.Skill(name=name, path=store.rel_to_data(p), author=author or fm.get("author") or "agent")
            s.add(r)
        elif author:
            r.author = author
        r.description = str(fm.get("description") or "")
        r.tags, r.tools_used = list(fm.get("tags") or []), list(fm.get("tools_used") or [])
        r.created_from_run = fm.get("created_from_run") or r.created_from_run
        r.updated_at = time.time()
        s.flush()
        d = db.to_dict(r)
    store.fts_put("skill", d["id"], "global", None, name, d["description"], body[:20000], " ".join(d["tags"] + d["tools_used"]))
    return d


def sync() -> None:
    root = store.skills_dir()
    seen = set()
    if root.exists():
        for d in sorted(root.iterdir()):
            if (d / "SKILL.md").exists():
                try:
                    index(d.name)
                    seen.add(d.name)
                except SkillError:
                    continue
    with db.session() as s:
        for r in s.scalars(select(db.Skill)):
            if r.name not in seen:
                s.delete(r)
                store.fts_delete("skill", r.id)


def read(name: str, count: bool = True) -> dict:
    p = path_of(name)
    if not p.exists():
        raise SkillError(f"no skill named {name!r} — use skill_search")
    with db.session() as s:
        r = s.scalars(select(db.Skill).where(db.Skill.name == name)).first()
        if r and count:
            r.reads = (r.reads or 0) + 1
        d = db.to_dict(r) if r else {"name": name}
    return {**d, "content": p.read_text()}


def delete(name: str, actor: str = "system", run_id: str | None = None) -> None:
    import shutil

    p = path_of(name)
    if not p.exists():
        raise SkillError(f"no skill named {name!r}")
    shutil.rmtree(p.parent)
    with db.session() as s:
        r = s.scalars(select(db.Skill).where(db.Skill.name == name)).first()
        if r:
            store.fts_delete("skill", r.id)
            s.delete(r)
    try:
        store.global_repo().commit(f"Delete skill {name}", author=actor)
    except Exception:  # noqa: BLE001
        pass
    store.audit(actor, "skill_delete", f"skill:{name}", run_id)


def all_skills() -> list[dict]:
    with db.session() as s:
        return [db.to_dict(r) for r in s.scalars(select(db.Skill).order_by(db.Skill.name))]


def search(query: str, limit: int = 5) -> list[dict]:
    hits = store.search(query, None, kinds=("skill",), limit=limit)
    out = []
    with db.session() as s:
        for h in hits:
            r = s.get(db.Skill, h["ref"])
            if r:
                out.append({**db.to_dict(r), "score": h["score"], "snippet": h["snippet"]})
    return out


def relevant_summary(text: str, limit: int = 3) -> str:
    """Pinned into every run: the skills most relevant to the brief / assets / request."""
    hits = search(text, limit) if text.strip() else []
    total = len(all_skills())
    if not total:
        return ""
    lines = [f"\n\n## Relevant skills (playbooks you wrote on earlier jobs; {total} in the toolbox)"]
    if hits:
        for h in hits:
            used = f" — tools: {', '.join(h['tools_used'])}" if h.get("tools_used") else ""
            lines.append(f"- **{h['name']}**: {h['description']}{used}")
        lines.append("Read the full playbook with skill_read(name) before you start that part of the job; skill_search finds others.")
    else:
        lines.append("None matched this brief — use skill_search when you hit a hard sub-problem.")
    return "\n".join(lines)


def seed_dir():
    return config.toolbox_seed_dir / "skills"
