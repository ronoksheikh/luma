"""Small git wrapper used for toolbox versioning.

* the GLOBAL toolbox (``/data/toolbox``) is an ordinary repository owned by the backend
  (the sandbox can read it but not write it);
* each project's ``work/`` gets a repository whose git dir lives OUTSIDE the workspace
  (``/data/git/<project>.work``, like the checkpoint repo) and which tracks only
  ``work/tools/`` and ``work/scripts/`` — so the agent can neither rewrite history nor
  corrupt the repository from its shell.

Every create / update / revert / promotion is a commit; tool versions are tags
``<tool>@<version>``.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

_locks: dict[str, threading.RLock] = {}


class GitError(Exception):
    pass


class Repo:
    def __init__(self, git_dir: Path, work_tree: Path, include: list[str] | None = None):
        self.git_dir, self.work_tree, self.include = Path(git_dir), Path(work_tree), include
        self.lock = _locks.setdefault(str(self.git_dir), threading.RLock())

    def git(self, *args: str, check: bool = True, author: str = "Luma Studio", timeout: float = 60) -> str:
        cmd = ["git", f"--git-dir={self.git_dir}", f"--work-tree={self.work_tree}", "-c", "safe.directory=*", "-c", f"user.name={author}",
               "-c", "user.email=luma@localhost", "-c", "core.autocrlf=false", "-c", "gc.auto=0", "-c", "commit.gpgsign=false",
               "-c", "tag.gpgsign=false", *args]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(self.work_tree))
        if check and r.returncode != 0:
            raise GitError(f"git {args[0]} failed: {(r.stderr or r.stdout).strip()[:800]}")
        return r.stdout

    def ensure(self) -> "Repo":
        with self.lock:
            self.work_tree.mkdir(parents=True, exist_ok=True)
            if not (self.git_dir / "HEAD").exists():
                self.git_dir.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(["git", "init", "-q", "--bare", str(self.git_dir)], check=True, capture_output=True)
                self.git("config", "core.bare", "false")
                try:
                    os.chmod(self.git_dir, 0o700)
                except OSError:
                    pass
            info = self.git_dir / "info"
            info.mkdir(exist_ok=True)
            lines = ["__pycache__/", "*.pyc", ".pytest_cache/", ".ruff_cache/", ".DS_Store"]
            if self.include is not None:  # track only these top-level directories
                lines = ["/*"] + [f"!/{d.strip('/')}/" for d in self.include] + lines
            (info / "exclude").write_text("\n".join(lines) + "\n")
        return self

    def commit(self, message: str, author: str = "Luma Studio", paths: list[str] | None = None) -> str | None:
        """Stage ``paths`` (default: everything) and commit; returns the sha or None when nothing changed."""
        with self.lock:
            self.ensure()
            spec = paths or ["."]
            self.git("add", "-A", "--", *spec)
            has_head = bool(self.head())
            changed = subprocess.run(["git", f"--git-dir={self.git_dir}", f"--work-tree={self.work_tree}", "diff", "--cached", "--quiet",
                                      *([] if has_head else ["--root"]), "--", *spec], cwd=str(self.work_tree), capture_output=True).returncode != 0
            if has_head and not changed:
                return None
            if not has_head and not self.git("diff", "--cached", "--name-only").strip() and not self.git("ls-files").strip():
                return None
            self.git("commit", "-q", "-m", message, author=author)
            return self.head()

    def head(self) -> str | None:
        out = self.git("rev-parse", "--verify", "-q", "HEAD", check=False).strip()
        return out or None

    def tag(self, name: str, force: bool = True) -> None:
        with self.lock:
            if self.head():
                self.git("tag", *(["-f"] if force else []), name)

    def tags(self, prefix: str) -> list[str]:
        return [t for t in self.git("tag", "-l", f"{prefix}*", "--sort=-creatordate").split() if t]

    def log(self, path: str, limit: int = 50) -> list[dict]:
        if not self.head():
            return []
        out = self.git("log", f"-n{limit}", "--format=%H%x1f%an%x1f%at%x1f%s%x1f%D", "--", path, check=False)
        rows = []
        for line in out.splitlines():
            parts = line.split("\x1f")
            if len(parts) < 5:
                continue
            sha, author, ts, subject, refs = parts
            tags = [r.strip()[5:] for r in refs.split(",") if r.strip().startswith("tag: ")]
            rows.append({"sha": sha, "author": author, "ts": float(ts), "message": subject, "tags": tags})
        return rows

    def diff(self, a: str, b: str | None, path: str) -> str:
        args = ["diff", "--no-color", "-M", a] + ([b] if b else []) + ["--", path]
        return self.git(*args, check=False)

    def show(self, rev: str, path: str) -> str | None:
        r = self.git("show", f"{rev}:{path}", check=False)
        return r if r else None

    def files_at(self, rev: str, path: str) -> list[str]:
        return [f for f in self.git("ls-tree", "-r", "--name-only", rev, "--", path, check=False).splitlines() if f]

    def restore(self, rev: str, path: str) -> None:
        """Make ``path`` (a directory) exactly as it was at ``rev`` (files added later are removed)."""
        with self.lock:
            target = self.work_tree / path
            keep = set(self.files_at(rev, path))
            if not keep:
                raise GitError(f"{path} does not exist at {rev}")
            if target.exists():
                for f in sorted(target.rglob("*"), reverse=True):
                    rel = f.relative_to(self.work_tree).as_posix()
                    if f.is_file() and rel not in keep and "__pycache__" not in rel:
                        f.unlink()
            self.git("checkout", rev, "--", path)


def now() -> float:
    return time.time()
