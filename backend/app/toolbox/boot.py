"""Boot-time toolbox setup: layout + git repo, seeding (once), the venv check, index sync, and
testing/registering seeded tools in the background (the studio is usable meanwhile)."""
from __future__ import annotations

import asyncio
import json
import logging
import shutil

from ..config import config
from . import plugins as P
from . import skills as K
from . import store, venv

log = logging.getLogger("luma.toolbox")
state: dict = {"seeded": [], "registered": [], "failed": [], "done": False}


def seed() -> list[str]:
    """Copy the bundled seed tools and skills into /data/toolbox — once per item (a seed the user deleted
    stays deleted)."""
    src = config.toolbox_seed_dir
    marker = config.toolbox_dir / ".seeded.json"
    done = set(json.loads(marker.read_text())) if marker.exists() else set()
    added = []
    for kind, dst_root in (("tools", store.tools_root("global", None)), ("skills", store.skills_dir())):
        root = src / kind
        if not root.exists():
            continue
        for d in sorted(root.iterdir()):
            key = f"{kind}/{d.name}"
            if not d.is_dir() or d.name.startswith((".", "_")) or key in done:
                continue
            dst = dst_root / d.name
            if not dst.exists():
                shutil.copytree(d, dst, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc"))
                from .lifecycle import _perms

                _perms(dst, "global")
                added.append(key)
            done.add(key)
    marker.write_text(json.dumps(sorted(done)))
    if added:
        try:
            store.global_repo().commit("Seed the toolbox: " + ", ".join(added), author="Luma Studio")
        except Exception as e:  # noqa: BLE001
            log.warning("toolbox seed commit failed: %s", e)
        store.audit("system", "seed", "toolbox", None, {"items": added})
        log.info("toolbox: seeded %s", ", ".join(added))
    state["seeded"] = added
    return added


def init() -> None:
    store.ensure_layout()
    seed()
    from .lifecycle import reserved_names

    store.sync(None, reserved_names())
    K.sync()
    P.sync()
    venv.start_background()


async def register_pending() -> None:
    """Test + register global tools that have never been tested (fresh seeds), once the venv is ready."""
    from sqlalchemy import select

    from .. import db
    from .lifecycle import ToolboxError, register

    await asyncio.to_thread(venv.ready.wait, 900)
    with db.session() as s:
        pending = list(s.scalars(select(db.ToolboxTool).where(db.ToolboxTool.scope == "global", db.ToolboxTool.test_status == "untested")))
    for r in pending:
        try:
            await register(r, None, "system")
            state["registered"].append(r.name)
        except ToolboxError as e:
            state["failed"].append(r.name)
            log.warning("toolbox: seed tool %s not registered: %s", r.name, str(e)[:500])
        except Exception:  # noqa: BLE001
            state["failed"].append(r.name)
            log.exception("toolbox: registering %s failed", r.name)
    state["done"] = True
    log.info("toolbox ready: %d tool(s) registered at boot%s", len(state["registered"]),
             f", {len(state['failed'])} failed ({', '.join(state['failed'])})" if state["failed"] else "")
