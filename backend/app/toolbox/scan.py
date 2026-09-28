"""Static checks run before a tool (or plugin) is registered, and the stricter promotion scan.

* ruff: auto-format, safe auto-fixes, then lint (syntax errors and undefined names block);
* blocked patterns: process environments (``/proc/*/environ``), the Docker socket, Luma's
  secrets / database, raw sockets, privilege escalation, API keys from the environment, and
  network code when the manifest says ``network: false``;
* secret detection (the redaction patterns + generic ``key = "…"`` assignments + high-entropy
  literals);
* size limits (code files, fixtures ≤ 2 MB, whole tool), no symlinks, binaries only in fixtures/.

Promotion (project → global) additionally blocks project-specific values: absolute paths,
project ids, the project's asset paths, its brand colours and its name — the agent is asked
to turn them into parameters.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..secrets_store import PATTERNS as KEY_PATTERNS

TEXT_EXT = {".py", ".md", ".yaml", ".yml", ".txt", ".json", ".toml", ".cfg", ".csv", ".svg"}
MAX_CODE = 200 * 1024
MAX_FIXTURES = 2 * 1024 * 1024
MAX_TOTAL = 3 * 1024 * 1024
BLOCKING_RUFF = ("E9", "F63", "F7", "F82")


@dataclass
class Finding:
    file: str
    line: int
    rule: str
    message: str


@dataclass
class Report:
    blocking: list[Finding] = field(default_factory=list)
    warnings: list[Finding] = field(default_factory=list)
    formatted: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.blocking

    def to_dict(self) -> dict:
        return {"ok": self.ok, "blocking": [asdict(f) for f in self.blocking], "warnings": [asdict(f) for f in self.warnings],
                "formatted": self.formatted}

    def text(self) -> str:
        lines = []
        for f in self.blocking:
            lines.append(f"BLOCKING {f.file}:{f.line} [{f.rule}] {f.message}")
        for f in self.warnings[:20]:
            lines.append(f"warning  {f.file}:{f.line} [{f.rule}] {f.message}")
        return "\n".join(lines) or "clean"


BLOCKED = [
    (re.compile(r"/proc/[^\s'\"]*environ"), "proc-environ", "reads a process environment (/proc/*/environ)"),
    (re.compile(r"docker\.sock|/var/run/docker|/run/docker"), "docker-socket", "touches the Docker socket"),
    (re.compile(r"/data/secrets|secrets_dir|studio\.db|/data/[^'\"\s]*secret", re.I), "luma-secrets", "reads Luma Studio's secrets or database"),
    (re.compile(r"\bSOCK_RAW\b|\bAF_PACKET\b"), "raw-socket", "opens a raw socket"),
    (re.compile(r"""["'`]\s*sudo\b|\bsudo\s+-|\bshell=True[^\n]*sudo"""), "privilege", "escalates privileges (sudo)"),
    (re.compile(r"""os\.(?:environ(?:\.get)?\s*[\[(]|getenv\s*\()\s*["'][A-Z0-9_]*(?:API_KEY|_TOKEN|SECRET|PASSWORD)["']"""), "env-secret",
     "reads an API key / token from the environment — tools never get keys; use ctx.call_tool for ElevenLabs"),
]
NETWORK = re.compile(r"^\s*(?:import|from)\s+(socket|urllib\.request|urllib3|requests|httpx|http\.client|aiohttp|websockets?|ftplib|smtplib)\b",
                     re.M)
GENERIC_SECRET = re.compile(r"""(?i)\b(api[_-]?key|secret|token|passw(?:or)?d|bearer|access[_-]?key)\w*\s*[:=]\s*["']([A-Za-z0-9_\-./+=]{12,})["']""")
LITERAL = re.compile(r"""["']([A-Za-z0-9_\-+/=]{32,})["']""")


def _entropy(s: str) -> float:
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in (s.count(ch) for ch in set(s)))


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def ruff(files: list[Path], fix: bool = True) -> tuple[list[Finding], list[str]]:
    """Format + safe fixes + lint. Returns (findings, formatted files)."""
    py = [str(f) for f in files if f.suffix == ".py"]
    if not py:
        return [], []
    formatted: list[str] = []
    base = [sys.executable, "-m", "ruff"]
    nc = ["--no-cache"]
    try:
        if fix:
            r = subprocess.run(base + ["format", *nc, "--line-length", "120", *py], capture_output=True, text=True, timeout=60)
            if r.returncode == 0 and "reformatted" in r.stdout + r.stderr:
                formatted = [Path(f).name for f in py]
            subprocess.run(base + ["check", *nc, "--fix", "--select", "I,F401,UP", "--line-length", "120", "--quiet", *py],
                           capture_output=True, text=True, timeout=60)
        r = subprocess.run(base + ["check", *nc, "--output-format", "json", "--select", "E,F,W,B", "--ignore", "E501,E741,B008,B905,E731",
                                   "--line-length", "120", *py], capture_output=True, text=True, timeout=60)
        items = json.loads(r.stdout or "[]")
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        return [Finding("-", 0, "ruff", f"ruff unavailable: {e}")], formatted
    out = []
    for it in items:
        out.append(Finding(Path(it["filename"]).name, (it.get("location") or {}).get("row", 0), it.get("code") or "ruff", it.get("message", "")))
    return out, formatted


def scan_dir(d: Path, network: bool, fix: bool = True, code_files: tuple[str, ...] = ("main.py",)) -> Report:
    """Checks for a tool / plugin directory."""
    rep = Report()
    total = fixtures = 0
    texts: dict[str, str] = {}
    for f in sorted(d.rglob("*")):
        rel = f.relative_to(d).as_posix()
        if "__pycache__" in rel or ".pytest_cache" in rel or ".ruff_cache" in rel:
            continue
        if f.is_symlink():
            rep.blocking.append(Finding(rel, 0, "symlink", "symlinks are not allowed in a tool directory"))
            continue
        if not f.is_file():
            continue
        size = f.stat().st_size
        total += size
        in_fix = rel.startswith(("fixtures/", "assets/", "preview", "thumb"))
        if in_fix:
            fixtures += size
        if f.suffix.lower() in TEXT_EXT and not in_fix:
            if size > MAX_CODE:
                rep.blocking.append(Finding(rel, 0, "size", f"{size // 1024} KB — code/doc files must be ≤ {MAX_CODE // 1024} KB"))
                continue
            texts[rel] = f.read_text(errors="replace")
        elif not in_fix:
            rep.blocking.append(Finding(rel, 0, "binary", "binary files belong in fixtures/"))
        elif f.suffix.lower() in TEXT_EXT and size <= MAX_CODE:
            texts[rel] = f.read_text(errors="replace")
    if fixtures > MAX_FIXTURES and not (d / "plugin.yaml").exists():
        rep.blocking.append(Finding("fixtures/", 0, "size", f"fixtures total {fixtures / 1e6:.1f} MB — keep them ≤ 2 MB"))
    if total > MAX_TOTAL and not (d / "plugin.yaml").exists():
        rep.blocking.append(Finding(".", 0, "size", f"the tool directory is {total / 1e6:.1f} MB — keep it ≤ 3 MB"))
    for rel, text in texts.items():
        if rel.endswith(".py"):
            for rx, rule, msg in BLOCKED:
                for m in rx.finditer(text):
                    rep.blocking.append(Finding(rel, _line_of(text, m.start()), rule, msg))
            if not network and not rel.startswith("test"):
                for m in NETWORK.finditer(text):
                    rep.blocking.append(Finding(rel, _line_of(text, m.start()), "network",
                                                f"imports {m.group(1)} but the manifest says network: false"))
            if "ctypes" in text:
                rep.warnings.append(Finding(rel, _line_of(text, text.index("ctypes")), "ctypes", "uses ctypes — keep it to pure computation"))
        rep.blocking += secrets(rel, text)
    py = [d / rel for rel in texts if rel.endswith(".py")]
    findings, rep.formatted = ruff(py, fix=fix)
    for fnd in findings:
        (rep.blocking if fnd.rule.startswith(BLOCKING_RUFF) or fnd.rule == "ruff" else rep.warnings).append(fnd)
    return rep


def secrets(rel: str, text: str) -> list[Finding]:
    out = []
    for rx in KEY_PATTERNS:
        for m in rx.finditer(text):
            out.append(Finding(rel, _line_of(text, m.start()), "secret", "looks like an API key — never put secrets in code"))
    for m in GENERIC_SECRET.finditer(text):
        val = m.group(2)
        if val.lower() in ("changeme", "your_api_key_here") or val.startswith(("{", "$")):
            continue
        out.append(Finding(rel, _line_of(text, m.start()), "secret", f"hard-coded credential in {m.group(1)!r} — never put secrets in code"))
    for m in LITERAL.finditer(text):
        s = m.group(1)
        if _entropy(s) > 4.6 and any(c.isdigit() for c in s) and any(c.isupper() for c in s) and any(c.islower() for c in s):
            out.append(Finding(rel, _line_of(text, m.start()), "secret", "high-entropy string literal (possible secret)"))
    seen = set()
    uniq = []
    for f in out:
        if (f.line, f.rule) not in seen:
            seen.add((f.line, f.rule))
            uniq.append(f)
    return uniq


ABS_PATH = re.compile(r"""["'](/(?:data|home|tmp|Users|opt/luma|root|mnt|srv)/[^"']*|[A-Za-z]:\\\\[^"']*)["']""")
PROJECT_ID = re.compile(r"\bp_[0-9a-f]{12}\b")
HEX = re.compile(r"#[0-9A-Fa-f]{6}\b")


def promotion_scan(d: Path, brand_colors: set[str], asset_names: set[str], project_names: set[str]) -> list[Finding]:
    """Project-specific values in a tool that is about to become global."""
    out: list[Finding] = []
    for f in sorted(d.rglob("*.py")):
        rel = f.relative_to(d).as_posix()
        if "__pycache__" in rel or rel.startswith("test"):
            continue
        text = f.read_text(errors="replace")
        for m in ABS_PATH.finditer(text):
            out.append(Finding(rel, _line_of(text, m.start()), "hard-coded-path", f"absolute path {m.group(1)!r} — make it a parameter"))
        for m in PROJECT_ID.finditer(text):
            out.append(Finding(rel, _line_of(text, m.start()), "project-id", f"project id {m.group(0)} — tools must not depend on one project"))
        for m in re.finditer(r"""["']((?:assets|work|renders|outputs)/[^"']+)["']""", text):
            out.append(Finding(rel, _line_of(text, m.start()), "hard-coded-path", f"project path {m.group(1)!r} — make it a parameter"))
        for name in asset_names:
            if len(name) > 4 and name in text:
                out.append(Finding(rel, _line_of(text, text.index(name)), "project-asset", f"refers to the project asset {name!r} — take it as a parameter"))
        low = {c.upper() for c in brand_colors}
        for m in HEX.finditer(text):
            if m.group(0).upper() in low:
                out.append(Finding(rel, _line_of(text, m.start()), "brand-value", f"brand colour {m.group(0)} is hard-coded — make it a parameter"))
        for nm in project_names:
            if len(nm) >= 4 and re.search(rf"\b{re.escape(nm)}\b", text):
                out.append(Finding(rel, _line_of(text, text.index(nm)), "brand-value", f"the brand/project name {nm!r} is hard-coded — make it a parameter"))
    return out
