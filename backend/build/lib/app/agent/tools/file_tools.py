"""list_files / read_file / write_file / edit_file (workspace-confined)."""
from __future__ import annotations

import difflib
import os

from .base import ToolContext, ToolError, ToolOutput, tool, truncate

HIDDEN = {".luma", "__pycache__", ".git", "node_modules", ".thumbs", ".pages", "frames"}


@tool(
    "list_files",
    "List files under a project path (default: the project root) with sizes. Hidden/internal folders and rendered frame folders are summarised.",
    {"path": {"type": "string", "default": "."}, "depth": {"type": "integer", "default": 3}},
)
async def list_files(ctx: ToolContext, a: dict) -> ToolOutput:
    root = ctx.resolve(a.get("path") or ".", must_exist=True)
    depth = max(1, min(int(a.get("depth") or 3), 8))
    lines = []
    base_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        d = len(os.path.normpath(dirpath).split(os.sep)) - len(os.path.normpath(str(root)).split(os.sep))
        skipped = [x for x in dirnames if x in HIDDEN]
        dirnames[:] = sorted(x for x in dirnames if x not in HIDDEN)
        rel = os.path.relpath(dirpath, ctx.pdir)
        for x in skipped:
            if x == "frames":
                n = len(os.listdir(os.path.join(dirpath, x)))
                lines.append(f"{os.path.join(rel, x)}/  ({n} frame files)")
        for f in sorted(filenames):
            p = os.path.join(dirpath, f)
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            lines.append(f"{os.path.normpath(os.path.join(rel, f))}  {_size(size)}")
        if d + 1 >= depth:
            dirnames[:] = []
            for x in sorted(os.listdir(dirpath)):
                if os.path.isdir(os.path.join(dirpath, x)) and x not in HIDDEN:
                    lines.append(f"{os.path.normpath(os.path.join(rel, x))}/ …")
        if len(lines) > 800:
            lines.append("… (truncated)")
            break
    _ = base_depth
    return ToolOutput("\n".join(lines) or "(empty)")


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


@tool(
    "read_file",
    "Read a text file (line-numbered). Use offset/limit (1-based lines) for large files. Binary files are refused (use inspect_asset / view_image / audio_analyze).",
    {"path": {"type": "string"}, "offset": {"type": "integer", "default": 1}, "limit": {"type": "integer", "default": 400}},
    ["path"],
)
async def read_file(ctx: ToolContext, a: dict) -> ToolOutput:
    p = ctx.resolve(a["path"], must_exist=True)
    if p.is_dir():
        raise ToolError(f"{a['path']} is a directory; use list_files")
    raw = p.read_bytes()
    if b"\x00" in raw[:8192]:
        raise ToolError("binary file; use inspect_asset, view_image or audio_analyze")
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    off = max(1, int(a.get("offset") or 1))
    lim = max(1, min(int(a.get("limit") or 400), 4000))
    chunk = lines[off - 1 : off - 1 + lim]
    body = "\n".join(f"{i:>5}  {ln}" for i, ln in enumerate(chunk, off))
    more = f"\n… ({len(lines) - (off - 1 + len(chunk))} more lines; continue with offset={off + len(chunk)})" if off - 1 + len(chunk) < len(lines) else ""
    return ToolOutput(truncate(f"{ctx.rel(p)} — {len(lines)} lines\n{body}{more}", ctx, "read"))


@tool(
    "write_file",
    "Create or overwrite a text file inside the project (parent folders are created). Prefer edit_file for small changes to existing files.",
    {"path": {"type": "string"}, "content": {"type": "string"}},
    ["path", "content"],
)
async def write_file(ctx: ToolContext, a: dict) -> ToolOutput:
    p = ctx.resolve(a["path"])
    if ".luma" in p.parts:
        raise ToolError("the .luma folder is managed by Luma Studio")
    content = str(a.get("content") if a.get("content") is not None else "")
    old = p.read_text(errors="replace") if p.exists() and p.is_file() else None
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    try:
        os.chmod(p, 0o664)
    except OSError:
        pass
    diff = _diff(old or "", content, ctx.rel(p)) if old is not None else None
    ui = {"path": ctx.rel(p), "diff": diff[:60000] if diff else None, "created": old is None, "lines": content.count("\n") + 1}
    return ToolOutput(f"{'created' if old is None else 'overwrote'} {ctx.rel(p)} ({len(content)} chars, {ui['lines']} lines)", ui=ui)


@tool(
    "edit_file",
    "Replace an exact string in a file. old_string must match exactly once (include surrounding lines to make it unique); set replace_all to change every occurrence.",
    {"path": {"type": "string"}, "old_string": {"type": "string"}, "new_string": {"type": "string"},
     "replace_all": {"type": "boolean", "default": False}},
    ["path", "old_string", "new_string"],
)
async def edit_file(ctx: ToolContext, a: dict) -> ToolOutput:
    p = ctx.resolve(a["path"], must_exist=True)
    old_s, new_s = str(a["old_string"]), str(a["new_string"])
    if not old_s:
        raise ToolError("old_string is empty; use write_file to create files")
    if old_s == new_s:
        raise ToolError("old_string and new_string are identical")
    text = p.read_text(errors="replace")
    n = text.count(old_s)
    if n == 0:
        hint = ""
        close = difflib.get_close_matches(old_s.strip().splitlines()[0] if old_s.strip() else old_s, text.splitlines(), n=1, cutoff=0.6)
        if close:
            hint = f" Closest line in the file: {close[0]!r}"
        raise ToolError(f"old_string not found in {ctx.rel(p)}.{hint} Re-read the file and copy the text exactly (whitespace matters).")
    if n > 1 and not a.get("replace_all"):
        raise ToolError(f"old_string occurs {n} times in {ctx.rel(p)}; add surrounding context to make it unique or set replace_all=true.")
    new_text = text.replace(old_s, new_s) if a.get("replace_all") else text.replace(old_s, new_s, 1)
    p.write_text(new_text)
    diff = _diff(text, new_text, ctx.rel(p))
    return ToolOutput(f"edited {ctx.rel(p)} ({n if a.get('replace_all') else 1} replacement(s))\n{diff[:4000]}", ui={"path": ctx.rel(p), "diff": diff[:60000]})


def _diff(a: str, b: str, name: str) -> str:
    return "".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), f"a/{name}", f"b/{name}", n=3))
