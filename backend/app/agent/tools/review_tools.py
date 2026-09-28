"""self_review: run a named checklist (prompts/checklists/*.md) against the current output."""
from __future__ import annotations

import asyncio

from ... import artifacts, review
from .base import ToolContext, ToolError, ToolOutput, tool


@tool(
    "self_review",
    """Run a named quality checklist against the current output and get a pass/fail table you MUST address:
    brand_fidelity (exact logo, colours, avoided colours, lockup), motion_quality (springs, motion blur, no pops, holds),
    audio_quality (sync, LUFS, peaks, clean edges), delivery (formats, duration, frame count, file names, presented).
    Automated items are measured; `manual` items need you to look and confirm with evidence.""",
    {"checklist_name": {"type": "string", "enum": ["brand_fidelity", "motion_quality", "audio_quality", "delivery"]}},
    ["checklist_name"],
)
async def self_review(ctx: ToolContext, a: dict) -> ToolOutput:
    name = str(a["checklist_name"])
    try:
        rows = await asyncio.to_thread(review.evaluate, name, ctx.pdir, ctx.project_id, ctx.run_id, ctx.settings.get("project", {}))
    except KeyError:
        raise ToolError(f"no checklist {name!r}; available: {', '.join(review.checklists())}") from None
    fails = [r for r in rows if r["status"] == "fail"]
    manual = [r for r in rows if r["status"] == "manual"]
    icon = {"pass": "PASS", "fail": "FAIL", "n/a": "n/a", "manual": "CHECK"}
    art = artifacts.create(ctx.project_id, ctx.run_id, "table", f"Self-review · {name.replace('_', ' ')}", None, f"self-review-{name}",
                           {"columns": ["Result", "Item", "Detail"], "rows": [[icon[r["status"]], r["item"], r["detail"]] for r in rows],
                            "checklist": name, "fails": len(fails)})
    ctx.emit("present", {"card": "table", "artifact": art})
    lines = [f"{icon[r['status']]:5} {r['item']} — {r['detail']}" for r in rows]
    verdict = (f"{len(fails)} automated item(s) FAILED — fix them (add todos if needed) and re-run self_review."
               if fails else "All automated items pass.")
    if manual:
        verdict += f" {len(manual)} manual item(s): look at the previews/end card and confirm them with evidence in your todos."
    return ToolOutput("\n".join(lines) + f"\n\n{verdict} (artifact {art['id']})")
