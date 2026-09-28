"""Runtime for Luma Studio toolbox tools.

A tool is a directory with ``tool.yaml`` and ``main.py`` exposing ``run(params, ctx) -> dict``.
Luma Studio runs it as ``python -m luma_engine.toolkit`` in the sandbox (same user, limits and
scrubbed environment as the terminal):

* stdin, first line: a JSON job ``{tool_dir, params, workspace, out_dir, network, max_memory_mb, ...}``;
* stdout: JSON lines — ``log`` / ``progress`` / ``image`` / ``call`` (RPC) / ``result`` / ``error``.
  Anything the tool itself prints goes to stderr (shown as log output), so stdout stays clean;
* ``ctx.call_tool(name, params)`` writes a ``call`` line and reads the answer from stdin — the
  backend executes it through its normal, budgeted tool layer (tools never see API keys).

Guards (defence in depth; the container is the security boundary):
memory (RLIMIT_DATA), file size (RLIMIT_FSIZE), no core dumps; a permanent audit hook that
refuses writes outside the workspace / out_dir / temp dir, reads of secrets and process
environments, raw sockets, sudo, and any network socket when the manifest says
``network: false``.

For tests, :func:`make_test_ctx` gives a context that records logs, progress and images:

    from luma_engine.toolkit import make_test_ctx
    def test_it(tmp_path):
        ctx = make_test_ctx(tmp_path)
        out = run({"svg": str(FIXTURES / "logo.svg")}, ctx)
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import signal
import socket
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

BLOCKED_READ = ("/proc/self/environ", "/var/run/docker.sock", "/run/docker.sock")


class ToolCallError(RuntimeError):
    """A ``ctx.call_tool`` request was refused or failed."""


def _jsonable(o: Any):
    try:
        import numpy as np

        if isinstance(o, np.generic):
            return o.item()
        if isinstance(o, np.ndarray):
            return o.tolist()
    except ImportError:  # pragma: no cover
        pass
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, (set, tuple)):
        return list(o)
    raise TypeError(f"{type(o).__name__} is not JSON serialisable — return plain dicts, lists, numbers and strings")


class Ctx:
    """What a tool's ``run(params, ctx)`` gets."""

    def __init__(self, workspace: str | Path, out_dir: str | Path, tool_dir: str | Path | None = None,
                 send: Callable[[dict], None] | None = None, rpc: Callable[[str, dict], Any] | None = None):
        self.workspace = Path(workspace).resolve()
        self.out_dir = Path(out_dir).resolve()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.tool_dir = Path(tool_dir).resolve() if tool_dir else None
        self._send = send or (lambda m: None)
        self._rpc = rpc
        self._cancelled = False

    # -- reporting ---------------------------------------------------------------------
    def log(self, msg: str) -> None:
        self._send({"type": "log", "msg": str(msg)})

    def progress(self, pct: float, msg: str = "") -> None:
        self._send({"type": "progress", "pct": max(0.0, min(100.0, float(pct))), "msg": str(msg)})

    def emit_image(self, path: str | Path) -> str:
        p = self.path(path)
        if not p.exists():
            raise FileNotFoundError(f"emit_image: {p} does not exist")
        self._send({"type": "image", "path": str(p)})
        return str(p)

    def cancelled(self) -> bool:
        return self._cancelled

    # -- paths ---------------------------------------------------------------------------
    def path(self, p: str | Path) -> Path:
        """A path given by the caller (relative to the workspace) → absolute, confined to the workspace/out_dir."""
        q = Path(p)
        q = (q if q.is_absolute() else self.workspace / q).resolve()
        roots = [self.workspace, self.out_dir] + ([self.tool_dir] if self.tool_dir else [])
        if not any(q == r or r in q.parents for r in roots):
            raise PermissionError(f"{p} is outside the workspace ({self.workspace})")
        return q

    def out(self, name: str) -> Path:
        """A file path inside ``out_dir`` (sub-folders are created)."""
        q = (self.out_dir / name).resolve()
        if self.out_dir not in q.parents:
            raise PermissionError(f"{name} escapes out_dir")
        q.parent.mkdir(parents=True, exist_ok=True)
        return q

    def fixture(self, name: str) -> Path:
        assert self.tool_dir is not None
        return self.tool_dir / "fixtures" / name

    # -- RPC -------------------------------------------------------------------------------
    def call_tool(self, name: str, params: dict | None = None) -> Any:
        """Call another tool (e.g. ``el_tts``) through Luma Studio's budgeted tool layer."""
        if self._rpc is None:
            raise ToolCallError("call_tool is not available here")
        return self._rpc(name, params or {})


class TestCtx(Ctx):
    """A context for pytest: records everything; ``tools`` maps names to fake call_tool handlers."""

    __test__ = False

    def __init__(self, workspace, out_dir, tool_dir=None, tools: dict[str, Callable[[dict], Any]] | None = None):
        self.logs: list[str] = []
        self.progress_log: list[tuple[float, str]] = []
        self.images: list[str] = []
        self.calls: list[tuple[str, dict]] = []
        self.tools = tools or {}

        def send(m):
            if m["type"] == "log":
                self.logs.append(m["msg"])
            elif m["type"] == "progress":
                self.progress_log.append((m["pct"], m["msg"]))
            elif m["type"] == "image":
                self.images.append(m["path"])

        def rpc(name, params):
            self.calls.append((name, params))
            if name not in self.tools:
                raise ToolCallError(f"no fake for tool {name!r} in this test")
            return self.tools[name](params)

        super().__init__(workspace, out_dir, tool_dir, send, rpc)


def make_test_ctx(tmp_path: str | Path, workspace: str | Path | None = None, tool_dir: str | Path | None = None,
                  tools: dict | None = None) -> TestCtx:
    tmp = Path(tmp_path)
    if tool_dir is None:  # the caller's tool directory (test_tool.py lives next to main.py)
        f = sys._getframe(1).f_globals.get("__file__")
        tool_dir = Path(f).resolve().parent if f else None
    return TestCtx(workspace or tmp, tmp / "out", tool_dir, tools)


def load_tool(tool_dir: str | Path):
    """Import ``main.py`` of a tool directory as a fresh module."""
    d = Path(tool_dir).resolve()
    spec = importlib.util.spec_from_file_location(f"luma_tool_{d.name}", d / "main.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(d))
    spec.loader.exec_module(mod)
    if not callable(getattr(mod, "run", None)):
        raise AttributeError(f"{d / 'main.py'} must define run(params, ctx)")
    return mod


# ======================================================================================
# guards
# ======================================================================================
def _under(p: str, roots: list[str]) -> bool:
    return any(p == r or p.startswith(r.rstrip("/") + "/") for r in roots)


def install_guards(write_roots: list[str], network: bool, deny_read: list[str] | None = None) -> None:
    roots = [os.path.realpath(r) for r in write_roots]
    denied = [os.path.realpath(r) for r in deny_read or []]
    WRITE = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    PATH_EVENTS = {"os.remove": 0, "os.rmdir": 0, "os.mkdir": 0, "os.rename": (0, 1), "os.replace": (0, 1), "os.symlink": (1,),
                   "os.link": (0, 1), "os.chmod": 0, "os.chown": 0, "os.truncate": 0, "shutil.rmtree": 0, "os.utime": 0}
    ip_families = {socket.AF_INET, getattr(socket, "AF_INET6", -1)}

    def blocked_read(p: str) -> bool:
        return (p in BLOCKED_READ or (p.startswith("/proc/") and p.endswith("/environ")) or "docker.sock" in p
                or _under(p, denied))

    def check_write(path) -> None:
        if isinstance(path, int) or path is None:
            return
        p = os.path.realpath(os.fsdecode(path))
        if not _under(p, roots):
            raise PermissionError(f"toolbox tools may only write inside the workspace / out_dir (refused: {p})")

    def hook(event: str, args: tuple) -> None:
        if event == "open":
            path, mode, flags = args
            if isinstance(path, int) or path is None:
                return
            p = os.path.realpath(os.fsdecode(path))
            if blocked_read(p):
                raise PermissionError(f"toolbox tools may not read {p}")
            write = (mode is not None and any(c in str(mode) for c in "wax+")) or (mode is None and flags and flags & WRITE)
            if write and not _under(p, roots):
                raise PermissionError(f"toolbox tools may only write inside the workspace / out_dir (refused: {p})")
        elif event in PATH_EVENTS:
            idx = PATH_EVENTS[event]
            for i in (idx if isinstance(idx, tuple) else (idx,)):
                if i < len(args):
                    check_write(args[i])
        elif event == "socket.__new__":
            _, family, type_, _proto = args
            if type_ == socket.SOCK_RAW or family == getattr(socket, "AF_PACKET", -1):
                raise PermissionError("raw sockets are not allowed in toolbox tools")
            if not network and family in ip_families:
                raise PermissionError("network access is off for this tool (manifest network: false, or disabled in Settings)")
        elif event in ("socket.getaddrinfo", "socket.gethostbyname", "socket.connect") and not network:
            raise PermissionError("network access is off for this tool (manifest network: false, or disabled in Settings)")
        elif event in ("subprocess.Popen", "os.system", "os.exec", "os.posix_spawn"):
            exe = args[0] if args else ""
            argv = args[1] if len(args) > 1 else None
            words = [os.fsdecode(exe)] if isinstance(exe, (str, bytes)) else []
            if isinstance(argv, (list, tuple)):
                words += [os.fsdecode(a) for a in argv[:1] if isinstance(a, (str, bytes))]
            elif isinstance(argv, (str, bytes)):
                words.append(os.fsdecode(argv))
            if any(os.path.basename(w.split()[0]) in ("sudo", "su", "doas") for w in words if w.strip()):
                raise PermissionError("toolbox tools may not escalate privileges")

    sys.addaudithook(hook)


def apply_limits(max_memory_mb: int | None, max_file_mb: int | None = 2048) -> None:
    import resource

    def setlim(kind, value):
        try:
            soft, hard = resource.getrlimit(kind)
            v = value if hard == resource.RLIM_INFINITY else min(value, hard)
            resource.setrlimit(kind, (v, hard))
        except (ValueError, OSError):
            pass

    if max_memory_mb:
        setlim(resource.RLIMIT_DATA, int(max_memory_mb) * 1024 * 1024)
    if max_file_mb:
        setlim(resource.RLIMIT_FSIZE, int(max_file_mb) * 1024 * 1024)
    setlim(resource.RLIMIT_CORE, 0)


# ======================================================================================
# harness
# ======================================================================================
def main() -> int:
    proto = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
    os.dup2(2, 1)  # the tool's own prints go to stderr
    sys.stdout = io.TextIOWrapper(os.fdopen(1, "wb", buffering=0), encoding="utf-8", write_through=True)
    stdin = sys.stdin

    def send(m: dict) -> None:
        proto.write(json.dumps(m, default=_jsonable) + "\n")
        proto.flush()

    job = json.loads(stdin.readline())
    ws, out_dir = Path(job["workspace"]), Path(job["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / ".tmp"
    tmp.mkdir(exist_ok=True)
    os.environ["TMPDIR"] = str(tmp)
    os.environ.setdefault("MPLCONFIGDIR", str(tmp / "mpl"))
    import tempfile

    tempfile.tempdir = str(tmp)
    calls = [0]

    def rpc(name: str, params: dict):
        calls[0] += 1
        cid = calls[0]
        send({"type": "call", "id": cid, "name": name, "params": params})
        line = stdin.readline()
        if not line:
            raise ToolCallError("the studio closed the RPC channel")
        ans = json.loads(line)
        if not ans.get("ok"):
            raise ToolCallError(ans.get("error") or f"{name} failed")
        return ans.get("result")

    ctx = Ctx(ws, out_dir, job["tool_dir"], send, rpc)
    signal.signal(signal.SIGTERM, lambda *_: setattr(ctx, "_cancelled", True))
    apply_limits(job.get("max_memory_mb"), job.get("max_file_mb", 2048))
    home_cache = os.path.join(os.path.expanduser("~"), ".cache")
    install_guards([str(ws), str(out_dir), str(tmp), home_cache] + job.get("extra_write_roots", []), bool(job.get("network")),
                   job.get("deny_read", []))
    os.chdir(ws)
    try:
        mod = load_tool(job["tool_dir"])
        result = mod.run(job.get("params") or {}, ctx)
        if result is None:
            result = {}
        json.dumps(result, default=_jsonable)  # fail here (as the tool's error) if it cannot be serialised
        send({"type": "result", "result": result})
        return 0
    except BaseException as e:  # noqa: BLE001 — a crash is reported, never propagated to the studio
        tb = traceback.format_exc()
        send({"type": "error", "error": f"{type(e).__name__}: {e}", "traceback": tb[-6000:]})
        return 1


if __name__ == "__main__":
    # run the harness from the importable module, so tools that `from luma_engine.toolkit import ToolCallError`
    # catch the same class the RPC raises (not a copy living in __main__)
    from luma_engine.toolkit import main as _main

    sys.exit(_main())
