"""Parallel sub-tasks: spawn_subagent runs a child agent (own context, restricted tools, own budget)
and returns its structured report. Children are capped in number and cost and are cancelled
with their parent."""
from __future__ import annotations

import asyncio
import json

from ... import db
from ...events import bus
from .base import REGISTRY, ToolContext, ToolError, ToolOutput, tool

NEVER_FOR_CHILDREN = {"spawn_subagent", "finish", "ask_user", "request_approval", "present_options", "checkpoint_restore"}
ALWAYS_FOR_CHILDREN = {"subagent_report", "todo_write", "todo_update", "todo_add", "todo_list", "notes_append", "notes_read", "list_files",
                       "read_file", "memory_read", "memory_search", "budget_status"}
_sems: dict[str, asyncio.Semaphore] = {}


@tool(
    "spawn_subagent",
    """Delegate a self-contained sub-task to a child agent with its own context and a restricted toolset, e.g. "design 3
    voice options" or "render 3 spring variants and pick the best by QC". It works in the same workspace, reports back a
    structured result, and is cancelled with you. Call several in one turn to run them in parallel (capped by the project).
    budget: {max_steps, max_cost_usd, max_el_chars}.""",
    {"task": {"type": "string", "description": "complete brief: goal, inputs (paths), constraints, what to return"},
     "tools_allowed": {"type": "array", "items": {"type": "string"}},
     "budget": {"type": "object", "properties": {"max_steps": {"type": "integer"}, "max_cost_usd": {"type": "number"}, "max_el_chars": {"type": "integer"}}},
     "label": {"type": "string"}},
    ["task", "tools_allowed"],
    requires="subagents",
)
async def spawn_subagent(ctx: ToolContext, a: dict) -> ToolOutput:
    st = ctx.settings.get("project", {})
    tools = [str(t) for t in a.get("tools_allowed") or []]
    unknown = [t for t in tools if t not in REGISTRY]
    if unknown:
        raise ToolError(f"unknown tool(s) {unknown}")
    forbidden = [t for t in tools if t in NEVER_FOR_CHILDREN]
    if forbidden:
        raise ToolError(f"sub-agents cannot use {forbidden} (they cannot talk to the user, deliver or spawn further agents)")
    b = a.get("budget") or {}
    cap = float(st.get("subagent_max_cost_usd") or 1.0)
    limits = {
        "task": str(a["task"])[:8000], "label": str(a.get("label") or a["task"][:60]),
        "tools": sorted(set(tools) | ALWAYS_FOR_CHILDREN),
        "max_steps": max(1, min(int(b.get("max_steps") or 30), int(ctx.settings.get("max_steps", 120)))),
        "max_cost_usd": min(float(b.get("max_cost_usd") or cap), cap) if cap else float(b.get("max_cost_usd") or 0),
        "max_el_chars": max(0, min(int(b.get("max_el_chars") or 0), ctx.el_budget.remaining() if ctx.el_budget else 0)),
    }
    R = ctx.runner
    with db.session() as s:
        child = db.Run(project_id=ctx.project_id, kind="subagent", status="idle", model=ctx.creds.llm_model, parent_run_id=ctx.run_id, limits=limits)
        s.add(child)
        s.flush()
        cid = child.id
        s.add(db.Message(run_id=cid, role="user", content={"role": "user", "content": _brief(limits)}))
    R.creds[cid] = ctx.creds
    R.children.setdefault(ctx.run_id, set()).add(cid)
    bus.publish(ctx.run_id, "subagent_start", {"child_run_id": cid, "label": limits["label"], "task": limits["task"][:1000], "tools": limits["tools"],
                                               "budget": {k: limits[k] for k in ("max_steps", "max_cost_usd", "max_el_chars")},
                                               "tool_call_id": ctx.tool_call_id})
    sem = _sems.setdefault(ctx.project_id, asyncio.Semaphore(int(st.get("subagent_max_concurrency") or 2)))
    try:
        async with sem:
            R.start(cid)
            task = R.tasks.get(cid)
            if task is not None:
                await asyncio.shield(task)
    except asyncio.CancelledError:
        await R.cancel(cid)
        bus.publish(ctx.run_id, "subagent_end", {"child_run_id": cid, "status": "cancelled", "label": limits["label"], "tool_call_id": ctx.tool_call_id})
        raise
    finally:
        R.children.get(ctx.run_id, set()).discard(cid)
    with db.session() as s:
        c = s.get(db.Run, cid)
        p = s.get(db.Run, ctx.run_id)
        p.cost_usd = (p.cost_usd or 0) + (c.cost_usd or 0)  # children's cost counts against the parent
        p.el_chars = (p.el_chars or 0) + (c.el_chars or 0)
        report = (c.outputs or [{}])[0] if isinstance(c.outputs, list) and c.outputs else {}
        res = {"child_run_id": cid, "status": c.status, "steps": c.steps, "cost_usd": round(c.cost_usd or 0, 4), "el_chars": c.el_chars or 0,
               "result": report.get("result"), "summary": c.summary or report.get("summary"), "artifacts": report.get("artifacts") or [],
               "error": c.error}
    bus.publish(ctx.run_id, "subagent_end", {**res, "label": limits["label"], "tool_call_id": ctx.tool_call_id})
    if c.status != "completed":
        res["note"] = "the sub-agent did not report a result (it stopped, failed or ran out of budget); check its timeline or do the task yourself"
    return ToolOutput(json.dumps(res, indent=1, default=str), status="success" if c.status == "completed" else "error")


def _brief(limits: dict) -> str:
    return (f"[Sub-agent task from the director]\n{limits['task']}\n\n"
            f"You are a SUB-AGENT: you cannot talk to the user. Work only with your tools ({', '.join(limits['tools'])}). "
            f"Budget: {limits['max_steps']} steps, ${limits['max_cost_usd']:.2f}, {limits['max_el_chars']} ElevenLabs characters. "
            "When done — or if you cannot finish — call subagent_report with a structured `result` (JSON), a short `summary` "
            "and the artifact ids / paths you produced.")


@tool(
    "subagent_report",
    "Sub-agents only: report the structured result of your task to the director and stop.",
    {"result": {}, "summary": {"type": "string"}, "artifacts": {"type": "array", "items": {"type": "string"}}},
    ["result", "summary"],
    requires="subagent_child",
)
async def subagent_report(ctx: ToolContext, a: dict) -> ToolOutput:
    rep = {"result": a.get("result"), "summary": str(a.get("summary") or "")[:4000], "artifacts": [str(x) for x in a.get("artifacts") or []][:50]}
    ctx.runner.finished[ctx.run_id] = {"summary": rep["summary"], "outputs": [rep]}
    return ToolOutput("reported", finish=True)
