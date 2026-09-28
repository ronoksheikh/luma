"""The run plan (todos): storage, rules, revisions and the compact form pinned into context.

Rules enforced here (for both the agent's tools and the user's edits):
* statuses: pending | in_progress | blocked | done | skipped | failed;
* at most ONE leaf item is ``in_progress`` (an agent attempt to start a second one is rejected;
  a user edit demotes the other item back to pending);
* ``done`` needs evidence (existing project paths, artifact ids, or JSON objects such as QC
  results) or an explicit justification note; ``skipped`` needs a reason;
* parents (phases) derive their status from their children;
* replacing the plan stores the previous plan as a revision (plans are re-planned, never
  silently dropped).
"""
from __future__ import annotations

import time
from pathlib import Path

from sqlalchemy import delete, select

from . import db
from .events import bus

STATUSES = ("pending", "in_progress", "blocked", "done", "skipped", "failed")
PRIORITIES = ("high", "medium", "low")
CLOSED = ("done", "skipped")
ICON = {"pending": "[ ]", "in_progress": "[>]", "blocked": "[!]", "done": "[x]", "skipped": "[-]", "failed": "[F]"}


class PlanError(Exception):
    pass


def _row(t: db.Todo) -> dict:
    d = db.to_dict(t)
    d["evidence"] = d.get("evidence") or []
    return d


def todos(run_id: str) -> list[dict]:
    with db.session() as s:
        rows = list(s.scalars(select(db.Todo).where(db.Todo.run_id == run_id).order_by(db.Todo.order, db.Todo.created_at)))
    return [_row(t) for t in rows]


def tree(run_id: str) -> list[dict]:
    items = todos(run_id)
    by_parent: dict[str | None, list[dict]] = {}
    for t in items:
        by_parent.setdefault(t["parent_id"], []).append(t)

    def build(pid):
        return [{**t, "children": build(t["id"])} for t in by_parent.get(pid, [])]

    return build(None)


def leaves(items: list[dict]) -> list[dict]:
    parents = {t["parent_id"] for t in items if t["parent_id"]}
    return [t for t in items if t["id"] not in parents]


def progress(run_id: str) -> dict:
    items = todos(run_id)
    lv = leaves(items)
    done = sum(1 for t in lv if t["status"] in CLOSED)
    cur = next((t for t in lv if t["status"] == "in_progress"), None)
    return {"done": done, "total": len(lv), "current": cur and {"id": cur["id"], "title": cur["title"]}}


def publish(run_id: str, reason: str = "") -> None:
    bus.publish(run_id, "todo_update", {"todos": todos(run_id), "progress": progress(run_id), "reason": reason})


# ------------------------------------------------------------------------------ writes
def _validate_item(it: dict) -> dict:
    if not isinstance(it, dict):
        raise PlanError("each item must be an object with at least a title")
    title = str(it.get("title") or "").strip()
    if not title:
        raise PlanError("every item needs a title")
    pr = str(it.get("priority") or "medium").lower()
    if pr not in PRIORITIES:
        raise PlanError(f"priority must be one of {PRIORITIES}")
    st = str(it.get("status") or "pending")
    if st not in STATUSES:
        raise PlanError(f"status must be one of {STATUSES}")
    return {"title": title[:300], "detail": str(it.get("detail") or ""), "priority": pr, "status": st,
            "acceptance_criteria": str(it.get("acceptance_criteria") or ""), "children": it.get("children") or it.get("items") or [],
            "id": it.get("id")}


def write_plan(run_id: str, project_id: str, items: list, reason: str = "", author: str = "agent") -> list[dict]:
    """Create or replace the whole plan. Items may nest via ``children``. Items that carry the
    ``id`` of an existing todo keep its status, evidence and timings."""
    if not isinstance(items, list) or not items:
        raise PlanError("items must be a non-empty list")
    old = todos(run_id)
    if old and not reason.strip():
        raise PlanError("a plan already exists: pass `reason` explaining why you are re-planning (the old plan is kept as a revision)")
    keep = {t["id"]: t for t in old}
    flat: list[tuple[dict, str | None, int]] = []

    def walk(lst, parent, depth):
        if depth > 3:
            raise PlanError("plans nest at most 3 levels (phase → task → subtask)")
        for it in lst:
            v = _validate_item(it)
            flat.append((v, parent, len(flat)))
            walk(v["children"], v, depth + 1)

    walk(items, None, 1)
    if len(flat) > 200:
        raise PlanError("too many items (max 200)")
    now = time.time()
    with db.session() as s:
        if old:
            rev = len(list(s.scalars(select(db.PlanRevision.id).where(db.PlanRevision.run_id == run_id)))) + 1
            s.add(db.PlanRevision(run_id=run_id, revision=rev, reason=reason.strip() or "initial", author=author, snapshot=old))
            s.execute(delete(db.Todo).where(db.Todo.run_id == run_id))
            s.flush()
        ids: dict[int, str] = {}
        for v, parent, idx in flat:
            prev = keep.get(v["id"]) if v["id"] else None
            t = db.Todo(run_id=run_id, project_id=project_id, title=v["title"], detail=v["detail"], priority=v["priority"],
                        acceptance_criteria=v["acceptance_criteria"], order=idx, created_at=now, updated_at=now,
                        status=prev["status"] if prev else ("pending" if v["status"] in ("in_progress", "done") else v["status"]),
                        evidence=prev["evidence"] if prev else None, note=prev["note"] if prev else None,
                        started_at=prev["started_at"] if prev else None, completed_at=prev["completed_at"] if prev else None)
            if prev:
                t.id = prev["id"]
            v["_obj"] = t
            s.add(t)
            s.flush()
            ids[id(v)] = t.id
            if parent is not None:
                t.parent_id = ids[id(parent)]
    _derive_parents(run_id)
    items_now = todos(run_id)
    if old:
        bus.publish(run_id, "plan_revision", {"revision": _revision_count(run_id), "reason": reason.strip(), "author": author,
                                              "before": len(old), "after": len(items_now)})
    publish(run_id, "plan written" if not old else "re-planned")
    return items_now


def _revision_count(run_id: str) -> int:
    with db.session() as s:
        return len(list(s.scalars(select(db.PlanRevision.id).where(db.PlanRevision.run_id == run_id))))


def revisions(run_id: str) -> list[dict]:
    with db.session() as s:
        return [db.to_dict(r) for r in s.scalars(select(db.PlanRevision).where(db.PlanRevision.run_id == run_id).order_by(db.PlanRevision.id))]


def add(run_id: str, project_id: str, item: dict, parent_id: str | None = None, author: str = "agent") -> dict:
    v = _validate_item(item)
    with db.session() as s:
        if parent_id:
            par = s.get(db.Todo, parent_id)
            if par is None or par.run_id != run_id:
                raise PlanError(f"no todo {parent_id} in this plan")
        siblings = list(s.scalars(select(db.Todo).where(db.Todo.run_id == run_id)))
        order = max([t.order for t in siblings], default=-1) + 1
        if parent_id:  # place right after the parent's last descendant
            desc = [t.order for t in siblings if t.parent_id == parent_id] or [s.get(db.Todo, parent_id).order]
            order = max(desc) + 1
            for t in siblings:
                if t.order >= order:
                    t.order += 1
        t = db.Todo(run_id=run_id, project_id=project_id, parent_id=parent_id, title=v["title"], detail=v["detail"], priority=v["priority"],
                    acceptance_criteria=v["acceptance_criteria"], status="pending" if v["status"] in ("in_progress", "done") else v["status"], order=order)
        s.add(t)
        s.flush()
        tid = t.id
    _derive_parents(run_id)
    publish(run_id, f"{author} added a todo")
    return get(tid)


def get(todo_id: str) -> dict:
    with db.session() as s:
        t = s.get(db.Todo, todo_id)
        if t is None:
            raise PlanError(f"no todo {todo_id}")
        return _row(t)


def check_evidence(pdir: Path | None, project_id: str, evidence: list) -> list:
    """Validate evidence entries: project paths must exist, artifact ids must exist, objects are
    kept as-is (e.g. QC JSON). Returns the normalised list."""
    out = []
    for e in evidence or []:
        if isinstance(e, dict):
            out.append(e)
            continue
        s_ = str(e).strip()
        if not s_:
            continue
        if s_.startswith("art_"):
            with db.session() as s:
                a = s.get(db.Artifact, s_)
            if a is None or a.project_id != project_id:
                raise PlanError(f"evidence artifact {s_} does not exist")
            out.append(s_)
            continue
        if pdir is not None and ("/" in s_ or "." in s_):
            p = (pdir / s_).resolve()
            if pdir.resolve() not in p.parents or not p.exists():
                raise PlanError(f"evidence path {s_!r} does not exist in the project workspace")
        out.append(s_)
    return out


def resolve_ref(run_id: str, ref: str) -> str:
    """A todo id, or (for convenience) a unique title of an item in this plan."""
    ref = str(ref or "").strip()
    items = todos(run_id)
    if any(t["id"] == ref for t in items):
        return ref
    hits = [t for t in items if t["title"].strip().lower() == ref.lower()]
    if len(hits) == 1:
        return hits[0]["id"]
    raise PlanError(f"no todo {ref!r} in this plan (use todo_list to see ids)")


def update(run_id: str, todo_id: str, status: str | None = None, note: str | None = None, evidence: list | None = None,
           author: str = "agent", pdir: Path | None = None, **fields) -> dict:
    todo_id = resolve_ref(run_id, todo_id)
    with db.session() as s:
        t = s.get(db.Todo, todo_id)
        has_children = s.scalars(select(db.Todo.id).where(db.Todo.parent_id == todo_id)).first() is not None
        project_id = t.project_id
    ev = check_evidence(pdir, project_id, evidence) if evidence else None
    if status is not None:
        if status not in STATUSES:
            raise PlanError(f"status must be one of {STATUSES}")
        if has_children and status in ("in_progress", "done") and author == "agent":
            raise PlanError("this is a phase: update its tasks; the phase status follows them")
        if status == "done":
            prior = get(todo_id)["evidence"]
            if not (ev or prior) and not (note and note.strip()):
                raise PlanError("cannot mark done without evidence: attach evidence (file paths, artifact ids, QC JSON) or a note justifying "
                                "why none applies. Acceptance criteria: " + (get(todo_id)["acceptance_criteria"] or "(none)"))
        if status == "skipped" and not (note and note.strip()):
            raise PlanError("skipping needs a reason in `note`")
        if status == "in_progress":
            others = [x for x in leaves(todos(run_id)) if x["status"] == "in_progress" and x["id"] != todo_id]
            if others:
                if author == "agent":
                    o = others[0]
                    raise PlanError(f"'{o['title']}' ({o['id']}) is already in progress. Only ONE item may be in progress: finish it "
                                    f"(done with evidence), block, skip or fail it first.")
                with db.session() as s:
                    for o in others:
                        s.get(db.Todo, o["id"]).status = "pending"
    with db.session() as s:
        t = s.get(db.Todo, todo_id)
        now = time.time()
        if status is not None and status != t.status:
            t.status = status
            if status == "in_progress" and not t.started_at:
                t.started_at = now
            if status in ("done", "skipped", "failed"):
                t.completed_at = now
            elif status in ("pending", "in_progress", "blocked"):
                t.completed_at = None
        if note is not None:
            t.note = note
        if ev:
            t.evidence = [*(t.evidence or []), *ev]
        for k in ("title", "detail", "priority", "acceptance_criteria", "order"):
            if fields.get(k) is not None:
                if k == "priority" and fields[k] not in PRIORITIES:
                    raise PlanError(f"priority must be one of {PRIORITIES}")
                setattr(t, k, fields[k])
        t.updated_at = now
    _derive_parents(run_id)
    publish(run_id, f"{author} updated {todo_id}")
    return get(todo_id)


def reorder(run_id: str, ids: list[str]) -> None:
    with db.session() as s:
        rows = {t.id: t for t in s.scalars(select(db.Todo).where(db.Todo.run_id == run_id))}
        missing = [i for i in ids if i not in rows]
        if missing:
            raise PlanError(f"unknown todo ids {missing}")
        rest = sorted((t for i, t in rows.items() if i not in ids), key=lambda t: t.order)
        for n, t in enumerate([rows[i] for i in ids] + rest):
            t.order = n
    publish(run_id, "user reordered the plan")


def _derive_parents(run_id: str) -> None:
    """Phases follow their tasks (deepest first)."""
    with db.session() as s:
        rows = list(s.scalars(select(db.Todo).where(db.Todo.run_id == run_id)))
        kids: dict[str, list[db.Todo]] = {}
        for t in rows:
            if t.parent_id:
                kids.setdefault(t.parent_id, []).append(t)
        depth = {}

        def d(t):
            if t.id not in depth:
                par = next((x for x in rows if x.id == t.parent_id), None)
                depth[t.id] = 0 if par is None else d(par) + 1
            return depth[t.id]

        for t in sorted((r for r in rows if r.id in kids), key=d, reverse=True):
            sts = [c.status for c in kids[t.id]]
            if all(x in CLOSED for x in sts):
                new = "skipped" if all(x == "skipped" for x in sts) else "done"
            elif "in_progress" in sts or any(x in CLOSED for x in sts):
                new = "failed" if "failed" in sts and "in_progress" not in sts else "in_progress"
            elif "blocked" in sts:
                new = "blocked"
            elif "failed" in sts:
                new = "failed"
            else:
                new = "pending"
            if new != t.status:
                t.status = new
                t.updated_at = time.time()
                if new == "in_progress" and not t.started_at:
                    t.started_at = min((c.started_at for c in kids[t.id] if c.started_at), default=time.time())
                if new in ("done", "skipped"):
                    t.completed_at = time.time()
                    t.evidence = t.evidence or ["all tasks in this phase are complete"]


# ------------------------------------------------------------------------------ context
def compact(run_id: str, max_chars: int = 6000) -> str:
    """The plan in a compact, always-pinned form."""
    items = tree(run_id)
    if not items:
        return ""
    p = progress(run_id)
    lines = [f"## Plan — {p['done']}/{p['total']} done" + (f"; in progress: {p['current']['title']} ({p['current']['id']})" if p["current"] else "")]

    def walk(lst, depth):
        for t in lst:
            ac = f" — AC: {t['acceptance_criteria']}" if t["acceptance_criteria"] and t["status"] not in CLOSED else ""
            extra = f" — note: {t['note']}" if t.get("note") and t["status"] in ("blocked", "skipped", "failed") else ""
            lines.append(f"{'  ' * depth}- {ICON[t['status']]} {t['id']} {t['title']}{ac}{extra}")
            walk(t["children"], depth + 1)

    walk(items, 0)
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[:max_chars] + "\n…(plan truncated — call todo_list for everything)"
    return out


def open_items(run_id: str) -> list[dict]:
    return [t for t in leaves(todos(run_id)) if t["status"] not in CLOSED]
