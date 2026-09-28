"""Persistent PTY bash session per project, mirrored to the browser (xterm.js).

The agent's commands run in the *same* interactive shell (cwd, env vars, venvs and
background jobs persist).  Each command is written to a script file and executed via a
shell function that prints OSC 777 markers around it; the reader thread strips the
markers from the displayed stream and uses them to frame the command's output and exit
code.  The environment is scrubbed (no API keys) and all output is redacted.
"""
from __future__ import annotations

import asyncio
import codecs
import fcntl
import os
import re
import secrets
import struct
import sys
import termios
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ptyprocess import PtyProcess

from .config import config
from .secrets_store import redact

OSC_RE = re.compile(r"\x1b\]777;luma;(start|done);([A-Za-z0-9]+)(?:;(-?\d+))?\x07")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b[()][A-Za-z0-9]|\x1b[=>]|\r(?!\n)")
SCROLLBACK = 256 * 1024

RCFILE = r"""
# Luma Studio sandbox shell
export PS1='\[\e[38;5;215m\]luma\[\e[0m\]:\[\e[38;5;111m\]\w\[\e[0m\]\$ '
export TERM=xterm-256color PYTHONUNBUFFERED=1 MPLBACKEND=Agg PIP_DISABLE_PIP_VERSION_CHECK=1
umask 002
shopt -s checkwinsize 2>/dev/null
alias ll='ls -alh --color=auto' ls='ls --color=auto'
__luma_run() {
  printf '\033]777;luma;start;%s\007' "$1"
  source "$2"
  local __rc=$?
  printf '\033]777;luma;done;%s;%s\007' "$1" "$__rc"
}
[ -f "$HOME/.luma_venv/bin/activate" ] && . "$HOME/.luma_venv/bin/activate"
"""


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s).replace("\r\n", "\n")


def sandbox_python() -> str:
    """Python for sandbox processes: the persistent tool venv (/data/venv, layered over the image's
    venv) when it is healthy, else the image's interpreter."""
    try:
        from .toolbox import venv

        return venv.python()
    except Exception:  # pragma: no cover
        return config.sandbox_python or sys.executable


def sandbox_env(workdir: str, extra: dict | None = None) -> dict:
    """A minimal, secret-free environment for sandbox processes."""
    home = os.path.expanduser(f"~{config.sandbox_user}") if config.sandbox_user else os.environ.get("HOME", "/tmp")
    path = os.environ.get("LUMA_SANDBOX_PATH") or os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    for py_bin in (os.path.dirname(config.sandbox_python or sys.executable), os.path.dirname(sandbox_python())):
        if py_bin and py_bin not in path.split(":"):
            path = py_bin + ":" + path  # the terminal's python is the engine's python (the tool venv first)
    env = {
        "PATH": path,
        "HOME": home,
        "USER": config.sandbox_user or os.environ.get("USER", "luma"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TERM": "xterm-256color",
        "LUMA_WORKSPACE": workdir,
        "PYTHONUNBUFFERED": "1",
        "MPLBACKEND": "Agg",
        "LUMA_SAMPLES_DIR": str(config.samples_dir),
        "LUMA_PLUGIN_DIRS": str(config.toolbox_dir / "plugins"),
    }
    for k in ("LUMA_FONT_DIRS", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy",
              "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "PIP_CERT", "NODE_EXTRA_CA_CERTS", "PLAYWRIGHT_BROWSERS_PATH"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    try:  # fonts installed into the project (install_font) are visible to the engine
        rel = Path(workdir).resolve().relative_to(config.projects_dir.resolve())
        fonts = str(config.projects_dir / rel.parts[0] / "fonts")
        env["LUMA_FONT_DIRS"] = ":".join(x for x in (fonts, env.get("LUMA_FONT_DIRS", "")) if x)
    except (ValueError, IndexError):
        pass
    env.update(extra or {})
    return env


def sandbox_argv(argv: list[str], env: dict, group: str | None = None) -> list[str]:
    """Wrap ``argv`` to run as the sandbox user with exactly ``env`` (no inheritance).
    ``group`` runs it with that primary group (same UID) — e.g. the no-network group whose
    sockets the firewall rejects (docker/luma-netctl)."""
    envargs = ["env", "-i"] + [f"{k}={v}" for k, v in env.items()]
    if config.sandbox_user and os.environ.get("USER") != config.sandbox_user:
        return ["sudo", "-n", "-u", config.sandbox_user, *(["-g", group] if group else []), "-H", "--"] + envargs + argv
    return envargs + argv


def nonet_group() -> str | None:
    """The group whose traffic the firewall rejects, when available (docker: `lumanonet`)."""
    g = os.environ.get("LUMA_NONET_GROUP", "")
    return g if g and config.sandbox_user and config.netctl else None


@dataclass
class CommandResult:
    exit_code: int | None
    output: str
    timed_out: bool = False
    interrupted: bool = False
    seconds: float = 0.0


@dataclass
class _Pending:
    id: str
    started: bool = False
    chunks: list = field(default_factory=list)
    done: threading.Event = field(default_factory=threading.Event)
    exit_code: int | None = None
    on_output: Callable[[str], None] | None = None


class TerminalSession:
    def __init__(self, project_id: str, workdir: str, cols: int = 120, rows: int = 32):
        self.project_id = project_id
        self.workdir = workdir
        self.cols, self.rows = cols, rows
        self.proc: PtyProcess | None = None
        self.scrollback = ""
        self.listeners: set[Callable[[str], None]] = set()
        self.status_listeners: set[Callable[[dict], None]] = set()
        self.takeover = False
        self._pending: dict[str, _Pending] = {}
        self._lock = asyncio.Lock()
        self._reader: threading.Thread | None = None
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._carry = ""
        self._alive = False
        self._start_lock = threading.Lock()
        self._out_lock = threading.Lock()
        self.cmd_dir = Path(workdir) / ".luma" / "cmd"

    # -- lifecycle ------------------------------------------------------------------------
    def start(self) -> None:
        self.cmd_dir.mkdir(parents=True, exist_ok=True)
        rc = Path(self.workdir) / ".luma" / "bashrc"
        rc.write_text(RCFILE)
        for p in (self.cmd_dir, rc.parent):
            try:
                os.chmod(p, 0o2775)
            except OSError:
                pass
        os.chmod(rc, 0o664)
        env = sandbox_env(self.workdir)
        argv = sandbox_argv(["bash", "--noediting", "--noprofile", "--rcfile", str(rc), "-i"], env)
        self.proc = PtyProcess.spawn(argv, cwd=self.workdir, env=env, dimensions=(self.rows, self.cols))
        self._alive = True
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._reader = threading.Thread(target=self._read_loop, name=f"pty-{self.project_id}", daemon=True)
        self._reader.start()
        self._notify_status()

    @property
    def alive(self) -> bool:
        return bool(self.proc and self.proc.isalive())

    def ensure(self) -> None:
        with self._start_lock:  # WebSocket + agent may race to start the shell
            if not self.alive:
                self._append(f"\r\n\x1b[2m[luma] starting shell in {self.workdir}\x1b[0m\r\n")
                self.start()

    def close(self) -> None:
        if self.proc is not None:
            try:
                self.proc.terminate(force=True)
            except Exception:
                pass
        self._alive = False

    # -- reading ----------------------------------------------------------------------------
    def _read_loop(self) -> None:
        proc = self.proc
        assert proc is not None
        fd = proc.fd
        while True:
            try:
                data = os.read(fd, 65536)
            except OSError:
                break
            if not data:
                break
            self._handle(self._decoder.decode(data))
        self._alive = False
        for p in list(self._pending.values()):
            p.exit_code = None
            p.done.set()
        self._append("\r\n\x1b[2m[luma] shell exited\x1b[0m\r\n")
        self._notify_status()

    def _handle(self, text: str) -> None:
        text = self._carry + text
        self._carry = ""
        # keep an incomplete OSC sequence for the next read
        i = text.rfind("\x1b]777;")
        if i != -1 and "\x07" not in text[i:]:
            text, self._carry = text[:i], text[i:]
        pos = 0
        for m in OSC_RE.finditer(text):
            self._emit(text[pos : m.start()])
            kind, cid, rc = m.group(1), m.group(2), m.group(3)
            p = self._pending.get(cid)
            if p is not None:
                if kind == "start":
                    p.started = True
                else:
                    p.exit_code = int(rc) if rc is not None else None
                    p.done.set()
            pos = m.end()
        self._emit(text[pos:])

    def _emit(self, text: str) -> None:
        if not text:
            return
        text = redact(text)
        for p in self._pending.values():
            if p.started and not p.done.is_set():
                p.chunks.append(text)
                if p.on_output:
                    try:
                        p.on_output(text)
                    except Exception:
                        pass
        self._append(text)

    def _append(self, text: str) -> None:
        with self._out_lock:
            self.scrollback = (self.scrollback + text)[-SCROLLBACK:]
            listeners = list(self.listeners)
        for cb in listeners:
            try:
                cb(text)
            except Exception:
                pass

    def attach(self, cb: Callable[[str], None]) -> str:
        """Subscribe to output and get the scrollback atomically (no gaps, no dups)."""
        with self._out_lock:
            self.listeners.add(cb)
            return self.scrollback

    def _notify_status(self) -> None:
        st = self.status()
        for cb in list(self.status_listeners):
            try:
                cb(st)
            except Exception:
                pass

    def status(self) -> dict:
        return {"type": "status", "alive": self.alive, "takeover": self.takeover, "cols": self.cols, "rows": self.rows,
                "busy": any(not p.done.is_set() for p in self._pending.values())}

    # -- writing ----------------------------------------------------------------------------
    def write(self, data: str) -> None:
        self.ensure()
        assert self.proc is not None
        self.proc.write(data.encode())

    def set_echo(self, on: bool) -> None:
        if not self.alive:
            return
        try:
            attrs = termios.tcgetattr(self.proc.fd)
            attrs[3] = attrs[3] | termios.ECHO if on else attrs[3] & ~termios.ECHO
            termios.tcsetattr(self.proc.fd, termios.TCSANOW, attrs)
        except termios.error:
            pass

    def resize(self, cols: int, rows: int) -> None:
        self.cols, self.rows = max(20, min(cols, 400)), max(5, min(rows, 200))
        if self.alive:
            try:
                # the kernel delivers SIGWINCH to the foreground process group itself
                fcntl.ioctl(self.proc.fd, termios.TIOCSWINSZ, struct.pack("HHHH", self.rows, self.cols, 0, 0))
            except Exception:
                pass

    def interrupt(self) -> None:
        if self.alive:
            self.proc.write(b"\x03")

    def set_takeover(self, on: bool) -> None:
        self.takeover = bool(on)
        self._notify_status()

    async def run(self, command: str, timeout: float = 120.0, on_output: Callable[[str], None] | None = None) -> CommandResult:
        """Run ``command`` in the persistent shell; returns exit code and output."""
        async with self._lock:
            self.ensure()
            cid = secrets.token_hex(6)
            script = self.cmd_dir / f"{cid}.sh"
            script.write_text(command + "\n")
            os.chmod(script, 0o664)
            p = _Pending(cid, on_output=on_output)
            self._pending[cid] = p
            shown = command if len(command) < 2000 else command[:2000] + " …"
            self._append(redact(shown).replace("\n", "\r\n") + "\r\n")
            self.set_echo(False)
            t0 = time.monotonic()
            self.proc.write(f"__luma_run {cid} {script}\n".encode())
            loop = asyncio.get_running_loop()
            timed_out = interrupted = False
            try:
                ok = await loop.run_in_executor(None, p.done.wait, timeout)
                if not ok:
                    timed_out = True
                    ok = await loop.run_in_executor(None, self._abort, p)
            except asyncio.CancelledError:
                interrupted = True
                await loop.run_in_executor(None, self._abort, p)
                raise
            finally:
                self._pending.pop(cid, None)
                self.set_echo(True)
                try:
                    script.unlink()
                except OSError:
                    pass
            out = strip_ansi("".join(p.chunks))
            return CommandResult(p.exit_code if p.done.is_set() else None, out, timed_out, interrupted, time.monotonic() - t0)

    def _abort(self, p: _Pending) -> bool:
        """Ctrl-C the running command.  SIGINT also aborts the ``__luma_run`` wrapper,
        so once the shell is back at its prompt we print the done-marker ourselves."""
        for _ in range(3):
            self.interrupt()
            if p.done.wait(0.4):
                return True
            if self.alive:
                self.proc.write(f"printf '\\033]777;luma;done;%s;130\\007' {p.id}\n".encode())
            if p.done.wait(1.5):
                return True
        return p.done.is_set()

    def send_keys(self, text: str) -> None:
        """Raw keystrokes (for interactive programs). Supports escapes like \\n, \\x03."""
        self.write(text)


class TerminalManager:
    def __init__(self):
        self.sessions: dict[str, TerminalSession] = {}

    def get(self, project_id: str, create: bool = True) -> TerminalSession | None:
        s = self.sessions.get(project_id)
        if s is None and create:
            wd = str(config.projects_dir / project_id)
            s = TerminalSession(project_id, wd)
            self.sessions[project_id] = s
        return s

    def close(self, project_id: str) -> None:
        s = self.sessions.pop(project_id, None)
        if s:
            s.close()

    def close_all(self) -> None:
        for pid in list(self.sessions):
            self.close(pid)


terminals = TerminalManager()
