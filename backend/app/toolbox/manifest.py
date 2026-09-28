"""``tool.yaml`` — the tool manifest — parsing and validation.

```yaml
name: fit_lockup_to_reference         # snake_case, unique within scope
version: 1.2.0                        # semver; bump on change
scope: global | project
description: >                        # shown to the LLM; 1–3 sentences
parameters: {type: object, ...}       # JSON Schema (draft 2020-12) → the tool's function schema
returns: {type: object}               # JSON Schema of the result
dependencies: [numpy==2.1.*]          # pinned (==)
timeout_s: 300
resources: {max_memory_mb: 2048}
network: false
produces_files: true
tags: [layout, brand]
author: agent | user
created_from_run: <run_id>
```
"""
from __future__ import annotations

import re

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,47}$")
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
DEP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*(\[[A-Za-z0-9_,\-]+\])?==[0-9][0-9A-Za-z.*+!\-]*$")

META_SCHEMA = {
    "type": "object",
    "required": ["name", "version", "scope", "description", "parameters", "returns"],
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string"},
        "version": {"type": "string"},
        "scope": {"enum": ["global", "project"]},
        "description": {"type": "string", "minLength": 20, "maxLength": 1200},
        "parameters": {"type": "object"},
        "returns": {"type": "object"},
        "dependencies": {"type": "array", "items": {"type": "string"}, "maxItems": 40},
        "timeout_s": {"type": "number", "minimum": 1, "maximum": 3600},
        "resources": {"type": "object", "properties": {"max_memory_mb": {"type": "integer", "minimum": 64, "maximum": 32768}},
                      "additionalProperties": False},
        "network": {"type": "boolean"},
        "produces_files": {"type": "boolean"},
        "tags": {"type": "array", "items": {"type": "string", "pattern": "^[a-z0-9][a-z0-9_\\-]{0,31}$"}, "maxItems": 16},
        "author": {"enum": ["agent", "user"]},
        "created_from_run": {"type": ["string", "null"]},
        "deprecated": {"type": ["object", "null"], "properties": {"reason": {"type": "string"}, "replacement": {"type": ["string", "null"]}}},
    },
}
DEFAULTS = {"dependencies": [], "timeout_s": 300, "resources": {"max_memory_mb": 2048}, "network": False, "produces_files": False,
            "tags": [], "author": "agent", "created_from_run": None}


class ManifestError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def parse(text: str) -> dict:
    try:
        m = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ManifestError([f"tool.yaml is not valid YAML: {e}"]) from None
    if not isinstance(m, dict):
        raise ManifestError(["tool.yaml must be a mapping"])
    return m


def dump(m: dict) -> str:
    order = ["name", "version", "scope", "description", "parameters", "returns", "dependencies", "timeout_s", "resources", "network",
             "produces_files", "tags", "author", "created_from_run", "deprecated"]
    out = {k: m[k] for k in order if k in m and (m[k] is not None or k == "created_from_run")}
    out.update({k: v for k, v in m.items() if k not in out})
    return yaml.safe_dump(out, sort_keys=False, allow_unicode=True, width=110)


def validate(m: dict, reserved: set[str] | None = None) -> dict:
    """Return the manifest with defaults filled in, or raise ManifestError with every problem."""
    errors: list[str] = []
    for e in sorted(Draft202012Validator(META_SCHEMA).iter_errors(m), key=lambda e: list(e.path)):
        where = ".".join(str(p) for p in e.path) or "(top level)"
        errors.append(f"{where}: {e.message}")
    name = m.get("name")
    if isinstance(name, str) and not NAME_RE.match(name):
        errors.append(f"name {name!r} must be snake_case: 3–48 chars of a-z, 0-9, _ starting with a letter")
    if isinstance(name, str) and reserved and name in reserved:
        errors.append(f"name {name!r} clashes with a built-in tool; choose another name")
    v = m.get("version")
    if isinstance(v, str) and not SEMVER_RE.match(v):
        errors.append(f"version {v!r} must be semver MAJOR.MINOR.PATCH (e.g. 1.0.0)")
    for key in ("parameters", "returns"):
        s = m.get(key)
        if isinstance(s, dict):
            try:
                Draft202012Validator.check_schema(s)
            except SchemaError as e:
                errors.append(f"{key} is not a valid JSON Schema: {e.message}")
            if s.get("type") != "object":
                errors.append(f"{key}.type must be 'object'")
    for d in m.get("dependencies") or []:
        if isinstance(d, str) and not DEP_RE.match(d.strip()):
            errors.append(f"dependency {d!r} must be pinned with == (e.g. numpy==2.1.* or scikit-image==0.24.0)")
    if errors:
        raise ManifestError(errors)
    out = {**{k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v) for k, v in DEFAULTS.items()}, **m}
    out["description"] = " ".join(str(out["description"]).split())
    return out


def bump(version: str, part: str = "patch") -> str:
    a, b, c = (int(x) for x in version.split("."))
    if part == "major":
        return f"{a + 1}.0.0"
    if part == "minor":
        return f"{a}.{b + 1}.0"
    return f"{a}.{b}.{c + 1}"


def newer(a: str, b: str) -> bool:
    return tuple(int(x) for x in a.split(".")) > tuple(int(x) for x in b.split("."))


def function_schema(m: dict) -> dict:
    """The OpenAI function schema the LLM sees."""
    params = dict(m["parameters"])
    params.setdefault("properties", {})
    tag = f"[toolbox {m['scope']} v{m['version']}] "
    return {"type": "function", "function": {"name": m["name"], "description": tag + m["description"], "parameters": params}}


def validate_instance(schema: dict, value, what: str) -> list[str]:
    errs = []
    for e in sorted(Draft202012Validator(schema).iter_errors(value), key=lambda e: list(e.path))[:8]:
        where = "/".join(str(p) for p in e.path) or "(root)"
        errs.append(f"{what} {where}: {e.message}")
    return errs
