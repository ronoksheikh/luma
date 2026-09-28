"""The persistent Python environment for tool dependencies: ``/data/venv``.

The container image stays immutable; everything the agent installs for tools lives here, so
it survives ``docker compose up --build``.  The venv is *layered* over the image's venv: a
``_luma_base.pth`` adds the image's site-packages (luma_engine, numpy, skia, opencv …) after
the venv's own, so tool dependencies are installed next to — never over — the engine.

``/data/toolbox/requirements.lock`` is the pinned union of every tool dependency (an exact
``pip freeze`` of the venv's own site-packages).  On boot :func:`ensure` recreates the venv
if the interpreter changed or vanished, compares it with the lock and reinstalls anything
missing (logged).  Pip runs as the sandbox user with the sandbox environment (no secrets).
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

from ..config import config

log = logging.getLogger("luma.toolbox")
ready = threading.Event()
state: dict = {"status": "unknown", "log": [], "python": None}
_lock = threading.RLock()
PIP_ENV = ("PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_FIND_LINKS", "PIP_NO_INDEX", "PIP_TRUSTED_HOST")


def base_python() -> str:
    return config.sandbox_python or sys.executable


def venv_python() -> Path:
    return config.venv_dir / "bin" / "python"


def lock_path() -> Path:
    return config.toolbox_dir / "requirements.lock"


def python() -> str:
    """The interpreter tools (and the terminal) use: the persistent venv when healthy."""
    return str(venv_python()) if state.get("status") == "ok" and venv_python().exists() else base_python()


def _note(msg: str) -> None:
    log.info("[venv] %s", msg)
    state["log"] = (state["log"] + [msg])[-50:]


def _sandbox_run(argv: list[str], timeout: float = 900, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    from ..terminal import sandbox_argv, sandbox_env

    env = sandbox_env(str(config.data_dir), {k: os.environ[k] for k in PIP_ENV if os.environ.get(k)} | (extra_env or {}))
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    return subprocess.run(sandbox_argv(argv, env), capture_output=True, text=True, timeout=timeout, env=env, cwd=str(config.data_dir))


def _py_version(py: str) -> str | None:
    try:
        r = subprocess.run([py, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"], capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _base_sites() -> list[str]:
    r = subprocess.run([base_python(), "-c", "import site,sysconfig,json;print(json.dumps(sorted({sysconfig.get_paths()['purelib'], sysconfig.get_paths()['platlib']})))"],
                       capture_output=True, text=True, timeout=30)
    import json

    return json.loads(r.stdout) if r.returncode == 0 else []


def site_dir() -> Path:
    v = _py_version(str(venv_python())) or "%d.%d" % sys.version_info[:2]
    return config.venv_dir / "lib" / f"python{v}" / "site-packages"


def parse_lock(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;]+)$", line)
        if m:
            out[canon(m.group(1))] = m.group(2)
    return out


def canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def installed() -> dict[str, str]:
    """Distributions installed in the venv's OWN site-packages (not the image's)."""
    from importlib.metadata import distributions

    sd = site_dir()
    if not sd.exists():
        return {}
    return {canon(d.metadata["Name"]): d.version for d in distributions(path=[str(sd)]) if d.metadata["Name"]}


def ensure(install_missing: bool = True) -> dict:
    """Create / repair the venv and reconcile it with the lock file. Safe to call repeatedly."""
    with _lock:
        ready.clear()
        try:
            return _ensure(install_missing)
        except Exception as e:  # noqa: BLE001 — the studio must boot even if the venv cannot be built
            state["status"] = "error"
            _note(f"venv unavailable ({type(e).__name__}: {e}); tools run on the image's Python")
            return dict(state)
        finally:
            ready.set()


def _ensure(install_missing: bool) -> dict:
    vd = config.venv_dir
    want = _py_version(base_python())
    have = _py_version(str(venv_python())) if venv_python().exists() else None
    if have is None or have != want:
        if vd.exists() and any(vd.iterdir()) and have and have != want:
            _note(f"interpreter changed ({have} → {want}): recreating {vd}")
            argv = [base_python(), "-m", "venv", "--clear", str(vd)]
        else:
            _note(f"creating {vd}" if not (vd / "pyvenv.cfg").exists() else f"repairing {vd} (interpreter missing)")
            argv = [base_python(), "-m", "venv", str(vd)]
        vd.mkdir(parents=True, exist_ok=True)
        r = _sandbox_run(argv, timeout=300)
        if r.returncode != 0:
            raise RuntimeError(f"python -m venv failed: {(r.stderr or r.stdout)[-600:]}")
    sd = site_dir()
    pth = sd / "_luma_base.pth"
    lines = "".join(f"import site; site.addsitedir({p!r})\n" for p in _base_sites())
    if not pth.exists() or pth.read_text() != lines:
        r = _sandbox_run([str(venv_python()), "-c", f"open({str(pth)!r}, 'w').write({lines!r})"], timeout=60)
        if r.returncode != 0:
            raise RuntimeError(f"cannot write {pth}: {r.stderr[-300:]}")
    r = _sandbox_run([str(venv_python()), "-c", "import luma_engine"], timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"the venv cannot import luma_engine: {r.stderr[-400:]}")
    state["status"] = "ok"
    state["python"] = str(venv_python())
    lock = parse_lock(lock_path().read_text()) if lock_path().exists() else {}
    have_pkgs = installed()
    missing = sorted(f"{n}=={v}" for n, v in lock.items() if have_pkgs.get(n) != v and n not in ("pip", "setuptools"))
    state["missing"] = missing
    if missing and install_missing:
        _note(f"reinstalling {len(missing)} locked package(s) missing from {vd}: {', '.join(missing)}")
        r = _sandbox_run([str(venv_python()), "-m", "pip", "install", "--no-deps", *missing])
        if r.returncode != 0:
            state["status"] = "degraded"
            _note(f"reinstall failed (tools that need these will fail until it succeeds): {(r.stderr or r.stdout)[-600:]}")
        else:
            _note("reinstalled: " + ", ".join(missing))
            state["missing"] = []
    elif not missing:
        _note(f"{vd} matches {lock_path().name} ({len(lock)} locked package(s))")
    return dict(state)


def install(specs: list[str]) -> tuple[bool, str]:
    """pip install tool dependencies into the venv (as the sandbox user), then refresh the lock."""
    if not specs:
        return True, "no dependencies"
    with _lock:
        if state.get("status") not in ("ok", "degraded"):
            ensure(install_missing=False)
        if state.get("status") not in ("ok", "degraded"):
            return False, "the tool venv is not available: " + "; ".join(state["log"][-2:])
        r = _sandbox_run([str(venv_python()), "-m", "pip", "install", *specs])
        out = (r.stdout + r.stderr)[-4000:]
        if r.returncode != 0:
            return False, out
        write_lock()
        return True, out


def write_lock() -> None:
    pk = installed()
    lock_path().parent.mkdir(parents=True, exist_ok=True)
    body = "# Pinned union of toolbox tool dependencies (pip freeze of /data/venv's own site-packages).\n" \
           "# Managed by Luma Studio: verified and reinstalled on boot.\n" + "".join(f"{n}=={v}\n" for n, v in sorted(pk.items()) if n not in ("pip", "setuptools"))
    lock_path().write_text(body)


def start_background() -> None:
    threading.Thread(target=ensure, name="venv-ensure", daemon=True).start()
