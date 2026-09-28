"""Tool registry and execution context."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from ...config import config
from ...events import Coalescer, bus
from ...secrets_store import Credentials, redact
from ...terminal import sandbox_argv, sandbox_env

MAX_TOOL_CHARS = 12000
HEAD_CHARS = 4000
TAIL_CHARS = 6000


class ToolError(Exception):
    """An expected failure: its message is returned to the model verbatim."""


@dataclass
class ToolOutput:
    content: str
    status: str = "success"  # success | error
    images: list[str] = field(default_factory=list)  # project-relative paths to show the model (vision)
    ui: dict = field(default_factory=dict)  # extra data for the UI tool card
    finish: bool = False


@dataclass
class ToolContext:
    run_id: str
    project_id: str
    pdir: Path
    creds: Credentials
    settings: dict
    vision: bool
    runner: Any
    tool_call_id: str = ""
    coalescer: Coalescer | None = None
    el_budget: Any = None

    def url(self, rel: str) -> str:
        return f"/api/files/{self.project_id}/{rel}"

    def rel(self, p: Path) -> str:
        return str(Path(p).resolve().relative_to(self.pdir.resolve()))

    def resolve(self, path: str, must_exist: bool = False) -> Path:
        """Project-relative (or absolute inside the project) path → safe absolute path."""
        if not path or "\x00" in path:
            raise ToolError("path is required")
        p = Path(path)
        if not p.is_absolute():
            p = self.pdir / p
        p = p.resolve()
        base = self.pdir.resolve()
        if p != base and base not in p.parents:
            raise ToolError(f"path {path!r} is outside the project workspace ({base}); use paths like work/scene.py")
        if must_exist and not p.exists():
            raise ToolError(f"{self.rel(p) if base in p.parents else path} does not exist")
        return p

    def output(self, text: str) -> None:
        """Stream live output into the tool card."""
        if self.coalescer is not None and text:
            self.coalescer.add("tool_output_delta", self.tool_call_id, redact(text), id=self.tool_call_id)

    def emit(self, type: str, data: dict) -> None:
        bus.publish(self.run_id, type, {"tool_call_id": self.tool_call_id, **data})


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable[[ToolContext, dict], Awaitable[ToolOutput]]
    elevenlabs: bool = False

    def schema(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}


REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, properties: dict, required: list[str] | None = None, elevenlabs: bool = False):
    def deco(fn):
        REGISTRY[name] = Tool(name, description.strip(), {"type": "object", "properties": properties, "required": required or [],
                                                           "additionalProperties": False}, fn, elevenlabs)
        return fn

    return deco


def truncate(text: str, ctx: ToolContext | None = None, label: str = "output") -> str:
    """Head + tail; the full text is saved to a file whose path is given to the model."""
    if len(text) <= MAX_TOOL_CHARS:
        return text
    note = ""
    if ctx is not None:
        d = ctx.pdir / "work" / ".luma" / "logs"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{label}_{ctx.tool_call_id or int(time.time())}.txt"
        p.write_text(text)
        note = f" Full {label} saved to {ctx.rel(p)} (read it with read_file offset/limit)."
    omitted = len(text) - HEAD_CHARS - TAIL_CHARS
    return f"{text[:HEAD_CHARS]}\n\n…[{omitted} characters truncated.{note}]…\n\n{text[-TAIL_CHARS:]}"


async def run_engine(ctx: ToolContext, args: list[str], timeout: float = 900, stream: bool = True, cwd: Path | None = None) -> tuple[int, str, list[dict]]:
    """Run ``python -m luma_engine <args>`` as the sandbox user (agent code is never
    executed in the backend process).  Returns (exit code, text output, JSON objects)."""
    py = config.sandbox_python or sys.executable
    env = sandbox_env(str(cwd or ctx.pdir))
    argv = sandbox_argv([py, "-m", "luma_engine", *args], env)
    proc = await asyncio.create_subprocess_exec(*argv, cwd=str(cwd or ctx.pdir), env=env, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT, start_new_session=True)
    out: list[str] = []
    objs: list[dict] = []

    async def reader():
        assert proc.stdout is not None
        buf = ""
        while True:
            chunk = await proc.stdout.read(4096)
            if not chunk:
                break
            s = chunk.decode(errors="replace")
            buf += s
            out.append(s)
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                st = line.strip()
                if st.startswith('{"type"'):
                    try:
                        o = json.loads(st)
                        objs.append(o)
                        if o.get("type") == "progress":
                            ctx.emit("progress", {k: v for k, v in o.items() if k != "type"})
                            continue
                    except ValueError:
                        pass
                if stream:
                    ctx.output(line + "\n")

    try:
        await asyncio.wait_for(reader(), timeout)
        rc = await proc.wait()
    except asyncio.TimeoutError:
        _kill(proc)
        raise ToolError(f"luma_engine {args[0]} timed out after {timeout:.0f}s — for long work use render_final or terminal_spawn") from None
    except asyncio.CancelledError:
        _kill(proc)
        raise
    text = "".join(out)
    return rc, text, objs


def _kill(proc) -> None:
    import signal

    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def parse_json_tail(text: str) -> Any:
    """The CLI prints a JSON document last; find and parse it."""
    i = text.rfind("\n{")
    cands = [text[i + 1 :]] if i != -1 else []
    j = text.find("{")
    if j != -1:
        cands.append(text[j:])
    for c in cands:
        try:
            return json.loads(c)
        except ValueError:
            continue
    # last resort: scan for the last parsable top-level object
    for k in range(len(text) - 1, -1, -1):
        if text[k] == "{" and (k == 0 or text[k - 1] == "\n"):
            try:
                return json.loads(text[k:])
            except ValueError:
                continue
    return None
