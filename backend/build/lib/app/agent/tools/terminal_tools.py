"""terminal_run / terminal_spawn / terminal_poll / terminal_kill / terminal_send_keys."""
from __future__ import annotations

import asyncio
import codecs
import json
from pathlib import Path

from ...jobs import JobError, jobs
from ...terminal import strip_ansi, terminals
from .base import ToolContext, ToolError, ToolOutput, tool, truncate


@tool(
    "terminal_run",
    """Run a shell command in the project's PERSISTENT bash session (cwd, exported env vars, venvs and background
    `&` jobs persist between calls; the shell starts in the project root which contains assets/ work/ renders/ audio/
    outputs/). Output streams live to the user's terminal pane. Returns the exit code and the output (long output is
    truncated to head+tail and saved to a file). Full terminal: you may pip/npm/apt-get install (sudo works inside the
    container), git clone, download fonts, run any code. For anything that can take more than ~2 minutes (full renders,
    big installs) use terminal_spawn instead.""",
    {
        "command": {"type": "string", "description": "Bash command(s); multi-line scripts and heredocs are fine."},
        "timeout_s": {"type": "integer", "description": "Seconds before Ctrl-C (default 120, max 1800).", "default": 120},
    },
    ["command"],
)
async def terminal_run(ctx: ToolContext, a: dict) -> ToolOutput:
    cmd = str(a.get("command") or "")
    if not cmd.strip():
        raise ToolError("command is empty")
    timeout = max(1, min(int(a.get("timeout_s") or 120), 1800))
    term = terminals.get(ctx.project_id)
    res = await term.run(cmd, timeout, on_output=lambda t: ctx.output(strip_ansi(t)))
    body = truncate(res.output, ctx, "terminal")
    head = f"exit_code: {res.exit_code if res.exit_code is not None else 'unknown'} ({res.seconds:.1f}s)"
    if res.timed_out:
        head += f"\nTIMED OUT after {timeout}s and was interrupted with Ctrl-C. Use terminal_spawn for long-running work."
    status = "success" if res.exit_code == 0 else "error"
    return ToolOutput(f"{head}\n{body}" if body.strip() else head + "\n(no output)", status, ui={"exit_code": res.exit_code, "timed_out": res.timed_out})


@tool(
    "terminal_spawn",
    """Start a long-running background job (e.g. a full render) as a detached process. It keeps running after this
    call returns and can be polled/killed; its log streams to the UI. JSON lines like {"type":"progress","frame":10,
    "total":300} printed by the job become progress bars. Runs in a fresh login shell with cwd = work/ (so pass
    absolute or ../ paths as needed). Jobs are queued if the concurrency limit is reached.""",
    {
        "command": {"type": "string"},
        "name": {"type": "string", "description": "Unique job name, [A-Za-z0-9._-]"},
    },
    ["command", "name"],
)
async def terminal_spawn(ctx: ToolContext, a: dict) -> ToolOutput:
    try:
        job = await asyncio.to_thread(jobs.spawn, ctx.project_id, str(a["name"]), str(a["command"]), ctx.run_id, str(ctx.pdir / "work"), ctx.tool_call_id)
    except JobError as e:
        raise ToolError(str(e)) from None
    await asyncio.sleep(1.0)
    p = jobs.poll(ctx.project_id, job["name"], 10)
    return ToolOutput(json.dumps({"name": p["name"], "status": p["status"], "pid": p["pid"], "log": ctx.rel(Path(p["log_path"])),
                                  "first_output": p.get("log_tail", "")[-1500:]}, indent=1), ui={"job": job["name"]})


@tool(
    "terminal_poll",
    "Status of a spawned job: running/done/failed/killed, exit code, elapsed time, last progress and the log tail.",
    {"name": {"type": "string"}, "tail_lines": {"type": "integer", "default": 40}},
    ["name"],
)
async def terminal_poll(ctx: ToolContext, a: dict) -> ToolOutput:
    try:
        p = jobs.poll(ctx.project_id, str(a["name"]), int(a.get("tail_lines") or 40))
    except JobError as e:
        raise ToolError(str(e)) from None
    keep = {k: p.get(k) for k in ("name", "status", "exit_code", "pid", "elapsed_s", "progress", "command")}
    keep["log_tail"] = p.get("log_tail", "")
    return ToolOutput(truncate(json.dumps(keep, indent=1), ctx, "poll"))


@tool("terminal_kill", "Kill a spawned job (SIGTERM to its process group, then SIGKILL).", {"name": {"type": "string"}}, ["name"])
async def terminal_kill(ctx: ToolContext, a: dict) -> ToolOutput:
    try:
        j = await asyncio.to_thread(jobs.kill, ctx.project_id, str(a["name"]))
    except JobError as e:
        raise ToolError(str(e)) from None
    return ToolOutput(json.dumps({"name": j["name"], "status": j["status"], "exit_code": j["exit_code"]}))


@tool(
    "terminal_send_keys",
    """Send raw keystrokes to the persistent shell (for interactive programs: answering prompts, quitting pagers,
    Ctrl-C). Escapes are interpreted: \\n Enter, \\t Tab, \\x03 Ctrl-C, \\x04 Ctrl-D, \\x1b Esc. Returns output produced
    during wait_ms.""",
    {"text": {"type": "string"}, "wait_ms": {"type": "integer", "default": 800}},
    ["text"],
)
async def terminal_send_keys(ctx: ToolContext, a: dict) -> ToolOutput:
    raw = str(a.get("text") or "")
    try:
        text = codecs.decode(raw, "unicode_escape")
    except Exception:
        text = raw
    term = terminals.get(ctx.project_id)
    got: list[str] = []
    cb = got.append
    term.listeners.add(cb)
    try:
        term.send_keys(text)
        await asyncio.sleep(max(0.05, min(int(a.get("wait_ms") or 800), 15000)) / 1000)
    finally:
        term.listeners.discard(cb)
    out = strip_ansi("".join(got))
    return ToolOutput(truncate(out, ctx, "keys") or "(no output yet)")
