"""Shared snippets for the toolbox tests: a tiny tool, its tests and README."""
import json

MANIFEST = {
    "description": "Squares an integer and writes the result to a text file; a tiny tool used by the tests.",
    "parameters": {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"], "additionalProperties": False},
    "returns": {"type": "object", "properties": {"square": {"type": "integer"}}, "required": ["square"]},
    "tags": ["math", "test"],
    "timeout_s": 60,
}
MAIN = '''def run(params, ctx):
    n = int(params["n"])
    ctx.log(f"squaring {n}")
    ctx.progress(50, "half way")
    ctx.out("square.txt").write_text(str(n * n))
    return {"square": n * n}
'''
TEST = '''from main import run

from luma_engine.toolkit import make_test_ctx


def test_square(tmp_path):
    ctx = make_test_ctx(tmp_path)
    assert run({"n": 3}, ctx)["square"] == 9
    assert ctx.logs == ["squaring 3"]
'''
README = "# square\n\nSquares an integer (what), because the tests need a tool (why), with ctx.out (how).\n\nExample: {\"n\": 3} → 9.\n\nLimitations: integers only.\n"


def body(name, scope="project", project_id=None, **over):
    b = {"scope": scope, "project_id": project_id, "name": name, "manifest": dict(MANIFEST), "main_py": MAIN, "test_py": TEST, "readme": README}
    b.update(over)
    return b


def tool_args(name, **over) -> dict:
    a = {"name": name, "manifest": dict(MANIFEST), "main_py": MAIN, "test_py": TEST, "readme": README}
    a.update(over)
    return a


def dumps(o) -> str:
    return json.dumps(o)
