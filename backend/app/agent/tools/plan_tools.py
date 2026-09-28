"""Planning & memory: todo_*, memory_*, notes_*, checkpoint_*, context_compact."""
from __future__ import annotations

import asyncio
import json
import time

from ... import checkpoints, memory, plan
from ...events import bus
from .base import ToolContext, ToolError, ToolOutput, tool

ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "detail": {"type": "string"},
        "priority": {"type": "string", "enum": ["high", "medium", "low"]},
        "acceptance_criteria": {"type": "string", "description": "How we'll know it's done, e.g. 'contact sheet reviewed; QC passes; final frame diff < 0.5/255'"},
        "status": {"type": "string", "enum": ["pending", "blocked", "skipped"]},
        "id": {"type": "string", "description": "keep an existing item (its status/evidence) when re-planning"},
        "children": {"type": "array", "items": {"type": "object"}, "description": "nested tasks (same shape)"},
    },
    "required": ["title"],
}


def _plan_text(run_id: str) -> str:
    return plan.compact(run_id) or "(no plan yet)"


@tool(
    "todo_write",
    """Create or REPLACE the whole plan in one call. Items nest (phases → tasks) via `children`; each has a title,
    detail, priority and acceptance_criteria. Any request needing more than ~5 steps MUST start with this. When
    re-planning, pass `reason` (the old plan is kept as a revision) and reuse ids of items you keep.""",
    {"items": {"type": "array", "items": ITEM_SCHEMA}, "reason": {"type": "string"}},
    ["items"],
)
async def todo_write(ctx: ToolContext, a: dict) -> ToolOutput:
    try:
        plan.write_plan(ctx.run_id, ctx.project_id, a.get("items") or [], str(a.get("reason") or ""), "agent")
    except plan.PlanError as e:
        raise ToolError(str(e)) from None
    return ToolOutput(_plan_text(ctx.run_id) + "\n\nNext: mark the first task in_progress with todo_update.")


@tool(
    "todo_update",
    """Update one item: status (pending | in_progress | blocked | done | skipped | failed), note, evidence. Only ONE item
    may be in_progress. `done` REQUIRES evidence (project file paths, artifact ids like art_…, or JSON objects such as
    QC results) or a note justifying why none applies; `skipped` requires a reason. Returns the updated plan — re-read
    it and decide the next step.""",
    {"id": {"type": "string"}, "status": {"type": "string", "enum": list(plan.STATUSES)}, "note": {"type": "string"},
     "evidence": {"type": "array", "items": {}}},
    ["id"],
)
async def todo_update(ctx: ToolContext, a: dict) -> ToolOutput:
    try:
        t = plan.update(ctx.run_id, str(a["id"]), a.get("status"), a.get("note"), a.get("evidence"), "agent", pdir=ctx.pdir)
    except plan.PlanError as e:
        raise ToolError(str(e)) from None
    nxt = ""
    if a.get("status") in ("done", "skipped", "failed"):
        open_ = plan.open_items(ctx.run_id)
        nxt = ("\n\nRe-read the plan above and pick the next item." if open_ else
               "\n\nEvery item is closed. Run the pre-delivery checks, present the results and call finish.")
    return ToolOutput(f"{t['id']} → {t['status']}\n\n{_plan_text(ctx.run_id)}{nxt}")


@tool(
    "todo_add",
    "Add one item to the plan (optionally under a parent phase).",
    {"item": ITEM_SCHEMA, "parent_id": {"type": "string"}},
    ["item"],
)
async def todo_add(ctx: ToolContext, a: dict) -> ToolOutput:
    try:
        t = plan.add(ctx.run_id, ctx.project_id, a.get("item") or {}, a.get("parent_id"), "agent")
    except plan.PlanError as e:
        raise ToolError(str(e)) from None
    return ToolOutput(f"added {t['id']}\n\n{_plan_text(ctx.run_id)}")


@tool(
    "todo_list",
    "The plan with ids, statuses, acceptance criteria, notes and evidence. `filter` = a status or 'open'.",
    {"filter": {"type": "string"}},
)
async def todo_list(ctx: ToolContext, a: dict) -> ToolOutput:
    f = str(a.get("filter") or "").strip()
    items = plan.todos(ctx.run_id)
    if f == "open":
        items = [t for t in items if t["status"] not in plan.CLOSED]
    elif f:
        items = [t for t in items if t["status"] == f]
    slim = [{k: t[k] for k in ("id", "parent_id", "title", "status", "priority", "acceptance_criteria", "note", "evidence")} for t in items]
    return ToolOutput(json.dumps({"progress": plan.progress(ctx.run_id), "items": slim}, indent=1, default=str))


# ----------------------------------------------------------------------------- memory
@tool(
    "memory_write",
    """Store a durable project fact or decision (key → value): brand colours, chosen voice_id, approved style, user
    preferences ("never use navy"). Memory is pinned into every future run of this project. Same key = overwrite.""",
    {"key": {"type": "string"}, "value": {"type": "string"}},
    ["key", "value"],
)
async def memory_write(ctx: ToolContext, a: dict) -> ToolOutput:
    try:
        e = memory.write(ctx.project_id, a["key"], a["value"], "agent")
    except memory.MemoryError_ as err:
        raise ToolError(str(err)) from None
    bus.publish(ctx.run_id, "memory_update", {"action": "write", "entry": e})
    return ToolOutput(f"remembered {e['key']!r}")


@tool("memory_read", "Read project memory (one key, or everything).", {"key": {"type": "string"}})
async def memory_read(ctx: ToolContext, a: dict) -> ToolOutput:
    es = memory.read(ctx.project_id, a.get("key"))
    return ToolOutput(json.dumps([{k: e[k] for k in ("key", "value", "source")} for e in es], indent=1) if es else "(empty)")


@tool("memory_search", "Search project memory by words.", {"query": {"type": "string"}}, ["query"])
async def memory_search(ctx: ToolContext, a: dict) -> ToolOutput:
    es = memory.search(ctx.project_id, a["query"])
    return ToolOutput(json.dumps([{k: e[k] for k in ("key", "value", "source")} for e in es], indent=1) if es else "(no matches)")


# ----------------------------------------------------------------------------- notes
def notes_path(ctx: ToolContext):
    p = ctx.pdir / "work" / ".luma" / "notes" / f"{ctx.run_id}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@tool(
    "notes_append",
    "Append to your working scratchpad for this run (hypotheses, measurements, what you tried). It survives context compaction.",
    {"text": {"type": "string"}},
    ["text"],
)
async def notes_append(ctx: ToolContext, a: dict) -> ToolOutput:
    text = str(a.get("text") or "").strip()
    if not text:
        raise ToolError("text is empty")
    p = notes_path(ctx)
    with open(p, "a") as f:
        f.write(f"\n### {time.strftime('%H:%M:%S')}\n{text}\n")
    return ToolOutput(f"noted ({p.stat().st_size} bytes in {ctx.rel(p)})")


@tool("notes_read", "Read your scratchpad for this run.", {})
async def notes_read(ctx: ToolContext, a: dict) -> ToolOutput:
    p = notes_path(ctx)
    return ToolOutput(p.read_text()[-12000:] if p.exists() and p.stat().st_size else "(no notes yet)")


# ----------------------------------------------------------------------------- checkpoints
@tool(
    "checkpoint_create",
    "Snapshot the workspace (scene code, audio, settings, plan) after a milestone. Renders are checkpointed automatically.",
    {"label": {"type": "string"}},
    ["label"],
)
async def checkpoint_create(ctx: ToolContext, a: dict) -> ToolOutput:
    try:
        c = await asyncio.to_thread(checkpoints.create, ctx.project_id, str(a["label"]), ctx.run_id)
    except checkpoints.CheckpointError as e:
        raise ToolError(str(e)) from None
    return ToolOutput(f"checkpoint {c['id']} “{c['label']}” ({c['meta'].get('stats') or 'no changes'})")


@tool("checkpoint_list", "List checkpoints (newest first).", {})
async def checkpoint_list(ctx: ToolContext, a: dict) -> ToolOutput:
    cs = checkpoints.list_(ctx.project_id)
    return ToolOutput(json.dumps([{"id": c["id"], "label": c["label"], "auto": bool(c["auto"]), "at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(c["created_at"]))}
                                  for c in cs[:50]], indent=1) if cs else "(no checkpoints)")


@tool(
    "checkpoint_restore",
    "Restore the workspace, settings and plan to a checkpoint (the current state is checkpointed first, so it can be undone).",
    {"id": {"type": "string"}},
    ["id"],
)
async def checkpoint_restore(ctx: ToolContext, a: dict) -> ToolOutput:
    try:
        r = await asyncio.to_thread(checkpoints.restore, ctx.project_id, str(a["id"]), ctx.run_id)
    except checkpoints.CheckpointError as e:
        raise ToolError(str(e)) from None
    return ToolOutput(f"restored “{r['restored']['label']}” (undo with checkpoint_restore('{r['safety_checkpoint']}'))\n\n{_plan_text(ctx.run_id)}")


# ----------------------------------------------------------------------------- context
@tool(
    "context_compact",
    """Summarise older turns into a structured digest (decisions, files, open issues) to free context. The plan, memory,
    output settings, brand facts, event list, notes and unresolved user requests are always kept. Also happens
    automatically near 75% of the context budget.""",
    {"reason": {"type": "string"}},
    ["reason"],
)
async def context_compact(ctx: ToolContext, a: dict) -> ToolOutput:
    ctx.runner.compact_requests[ctx.run_id] = str(a.get("reason") or "requested")
    return ToolOutput("Compaction scheduled: older turns will be summarised before your next step.")
