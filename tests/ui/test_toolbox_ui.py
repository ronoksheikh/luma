"""Playwright: the Toolbox — left-nav section and right-pane tab, filters, tool detail (README, source, tests),
the "Try it" form generated from the JSON Schema, version diff + revert, skills viewer, templates gallery, and the
run-timeline cards (tool_create diffs + tests, "New tool available" badge, promotion approval with the diff)."""
import asyncio
import sys
import time
from pathlib import Path

import httpx
import pytest
from conftest import shot
from mock_llm import MockServer, call

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from toolbox_helpers import MAIN, body  # noqa: E402

pytestmark = pytest.mark.ui
ROOT = Path(__file__).resolve().parents[2]
USER, PASSWORD = "toolbox_ui", "a long password 123"

LUMA_MAIN = '''import cv2


def run(params, ctx):
    img = cv2.imread(str(ctx.path(params["image"])), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError(f"cannot read {params['image']}")
    ctx.log(f"{img.shape[1]}x{img.shape[0]}")
    return {"mean_luma": float(img.mean()), "dark": bool(img.mean() < float(params.get("threshold", 40)))}
'''
LUMA_TEST = '''import cv2
import numpy as np
from main import run

from luma_engine.toolkit import make_test_ctx


def test_mean_and_dark_flag(tmp_path):
    cv2.imwrite(str(tmp_path / "a.png"), np.full((8, 8), 20, np.uint8))
    out = run({"image": "a.png"}, make_test_ctx(tmp_path, workspace=tmp_path))
    assert out["mean_luma"] == 20 and out["dark"]
'''
LUMA_MANIFEST = {"description": "Mean luma (0-255) of a frame and whether it counts as dark — checks the first frame of an outro is near black.",
                 "parameters": {"type": "object", "properties": {"image": {"type": "string"}, "threshold": {"type": "number", "default": 40}},
                                "required": ["image"]},
                 "returns": {"type": "object", "properties": {"mean_luma": {"type": "number"}, "dark": {"type": "boolean"}}, "required": ["mean_luma"]},
                 "tags": ["qc", "luma"], "timeout_s": 60}
LUMA_README = ("# frame_mean_luma\n\nMean luma of a frame (what), to prove an outro starts from black (why), via OpenCV grayscale mean (how).\n\n"
               "Example: {\"image\": \"work/f0.png\"} → {mean_luma: 3.1, dark: true}\n\nLimitations: 8-bit images only.\n")


@pytest.fixture(scope="module")
def setup(server):
    from app.toolbox import boot, venv

    t0 = time.time()
    while not (venv.ready.is_set() and boot.state.get("done")) and time.time() - t0 < 300:
        time.sleep(0.5)
    c = httpx.Client(base_url=server["url"], headers={"X-Luma-Client": "1"}, timeout=120)
    c.post("/api/auth/signup", json={"username": USER, "password": PASSWORD})
    p = c.post("/api/projects", json={"name": "Toolbox demo"}).json()
    pid = p["id"]
    pdir = server["data"] / "projects" / pid
    # a hand-authored project tool with two versions (for the Try-it form and the diff view)
    assert c.post("/api/toolbox/tools", json=body("square_it", project_id=pid)).status_code == 201
    c.post("/api/toolbox/tools/project/square_it/enable", params={"project_id": pid})
    c.put("/api/toolbox/tools/project/square_it", params={"project_id": pid},
          json={"main_py": MAIN.replace('ctx.log(f"squaring {n}")', 'ctx.log(f"squaring {n} (v1.1)")'), "reason": "clearer log line", "bump": "minor",
                "test_py": "from main import run\nfrom luma_engine.toolkit import make_test_ctx\n\n\ndef test_square(tmp_path):\n"
                           "    assert run({'n': 3}, make_test_ctx(tmp_path))['square'] == 9\n"})
    # a saved template for the gallery
    import subprocess

    with open(ROOT / "samples/veyra-symbol.svg", "rb") as f:
        c.post(f"/api/projects/{pid}/assets", files={"files": ("veyra-symbol.svg", f, "image/svg+xml")})
    (pdir / "work/scene.py").write_text("from luma_engine.templates.fan_unfold import FanUnfold\nscene = FanUnfold(logo='assets/veyra-symbol.svg', "
                                        "wordmark='Veyra', background='#0B0F2A', width=640, height=360, fps=12, duration=2, sound=False)\n")
    subprocess.run([sys.executable, "-m", "luma_engine", "preview", "work/scene.py", "--times", "1.9", "--scale", "1", "--out", "work/end.png"],
                   cwd=str(pdir), capture_output=True, check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(pdir / "work/end.png"), "-t", "1", "-r", "12", "-pix_fmt", "yuv420p",
                    "-vf", "scale=640:-2", str(pdir / "outputs/preview.mp4")], check=True)
    from app.toolbox import plugins as P

    schema = {"type": "object", "properties": {"logo": {"type": "string", "default": "assets/veyra-symbol.svg"},
                                               "wordmark": {"type": ["string", "null"], "default": "Veyra"},
                                               "background": {"type": "string", "default": "#0B0F2A"}}}
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(1) as ex:  # Playwright's sync API owns this thread's event loop
        res = ex.submit(asyncio.run, P.template_save(pdir, pid, "fan_outro_night", "work/scene.py", schema, "outputs/preview.mp4",
                                                     "Fan unfold outro on a night background, saved from the Toolbox demo project.",
                                                     "Fan outro — night")).result()
    assert res["enabled"], res["test"]
    import cv2
    import numpy as np

    cv2.imwrite(str(pdir / "work/f0.png"), np.full((36, 64), 6, np.uint8))
    yield {"client": c, "project": p, "pdir": pdir}
    c.close()


def login(page, base):
    page.goto(base)
    page.get_by_label("Username").fill(USER)
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Sign in").last.click()
    page.get_by_label("Username").wait_for(state="detached", timeout=20000)


def test_toolbox_ui(server, browser, setup):
    c, p = setup["client"], setup["project"]
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    login(page, server["url"])
    page.get_by_text("Toolbox demo").first.click()

    # --- left nav → Toolbox tab, list + filters
    nav = page.locator("[data-testid=toolbox-nav]")
    nav.wait_for()
    page.locator("[data-testid=nav-tools]").click()
    panel = page.locator("[data-testid=toolbox-panel]")
    panel.locator("[data-testid=tool-row][data-name=svg_split_exact]").wait_for(timeout=20000)
    assert panel.locator("[data-testid=tool-row]").count() >= 8
    shot(page, "toolbox_tools")
    panel.get_by_role("textbox", name="Search tools").fill("split logo parts")
    page.wait_for_timeout(700)
    assert panel.locator("[data-testid=tool-row]").first.get_attribute("data-name") == "svg_split_exact"
    panel.get_by_role("textbox", name="Search tools").fill("")
    page.wait_for_timeout(500)

    # --- tool detail: README, source, tests
    panel.locator("[data-testid=tool-row][data-name=svg_split_exact]").click()
    det = page.locator("[data-testid=tool-detail]")
    det.locator("[data-testid=tool-readme]").get_by_text("prove", exact=False).first.wait_for()
    shot(page, "tool_detail")
    det.get_by_role("tab", name="Source").click()
    det.get_by_text("def run(params, ctx)").wait_for()
    det.get_by_role("tab", name="Tests").click()
    det.get_by_role("button", name="Run tests").click()
    det.locator("[data-testid=test-output]").get_by_text("passed").wait_for(timeout=60000)
    shot(page, "tool_tests")
    page.keyboard.press("Escape")

    # --- "Try it" form generated from the schema (project tool square_it)
    panel.locator("[data-testid=tool-row][data-name=square_it]").click()
    det = page.locator("[data-testid=tool-detail]")
    det.get_by_role("tab", name="Try it").click()
    form = det.locator("[data-testid=schema-form]")
    form.get_by_role("spinbutton", name="n").fill("12")
    det.get_by_role("button", name="Run").click()
    res = det.locator("[data-testid=try-result]")
    res.get_by_text('"square": 144').wait_for(timeout=60000)
    shot(page, "tool_try_it")

    # --- history: diff between versions, revert
    det.get_by_role("tab", name="History").click()
    hist = det.locator("[data-testid=tool-history]")
    hist.get_by_role("button", name="Show diff").click()
    d = hist.locator("[data-testid=diff]")
    d.get_by_text("(v1.1)").first.wait_for()
    assert "text-success" in (d.locator("div", has_text="(v1.1)").first.get_attribute("class") or "")
    shot(page, "tool_diff")
    hist.get_by_role("button", name="Revert").first.click()
    page.get_by_text("Restored v1.0.0 as v1.1.1").wait_for(timeout=60000)
    page.keyboard.press("Escape")

    # --- skills viewer
    panel.get_by_role("button", name="Skills").click()
    panel.locator("[data-testid=skill-row]", has_text="motion_blur_and_hdr_compositing").click()
    sk = page.locator("[data-testid=skill-modal]")
    sk.locator("[data-testid=skill-body]").get_by_text("Pitfalls").wait_for()
    shot(page, "skill_viewer")
    page.keyboard.press("Escape")

    # --- templates gallery from the left nav
    page.locator("[data-testid=nav-templates]").click()
    gal = page.locator("[data-testid=templates-gallery]")
    gal.locator("[data-testid=template-card]", has_text="Fan outro — night").wait_for()
    shot(page, "templates_gallery")
    page.keyboard.press("Escape")
    assert not errors, errors
    ctx.close()


def test_timeline_cards_and_promotion_approval(server, browser, setup):
    c, p = setup["client"], setup["project"]

    def promote(body):
        return {"tool_calls": [call("tool_promote", name="frame_mean_luma", reason="every outro should start from black")]}

    script = [
        {"text": "Checking the toolbox first.", "tool_calls": [call("todo_write", items=[{"title": "First frame is black", "acceptance_criteria": "mean luma < 40"}]),
                                                                call("toolbox_search", query="average brightness of the first frame")]},
        {"text": "Nothing fits — building a small tool.", "tool_calls": [call("tool_create", name="frame_mean_luma", manifest=LUMA_MANIFEST,
                                                                                main_py=LUMA_MAIN, test_py=LUMA_TEST, readme=LUMA_README)]},
        {"tool_calls": [call("tool_register", name="frame_mean_luma")]},
        {"tool_calls": [call("frame_mean_luma", image="work/f0.png")]},
        promote,
        {"text": "Promoted."},
    ]
    with MockServer(script) as mock:
        c.post("/api/settings/remember", json={"llm_api_key": "sk-mock-ui-0123456789", "llm_base_url": mock.url, "llm_model": "mock-director"})
        rid = c.post(f"/api/projects/{p['id']}/runs", json={"text": "Make sure the outro starts from black"}).json()["id"]
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        login(page, server["url"])
        page.get_by_text("Toolbox demo").first.click()
        created = page.locator("[data-testid=toolbox-tool_created]")
        created.wait_for(timeout=90000)
        created.locator("[data-testid=test-chip]").get_by_text("tests passed").wait_for()
        created.get_by_role("button", name="main.py").click()
        created.locator("[data-testid=diff]").get_by_text("def run(params, ctx):").wait_for()
        created.scroll_into_view_if_needed()
        shot(page, "card_tool_created")
        page.locator("[data-testid=toolbox-registered]").get_by_text("New tool available:").wait_for(timeout=60000)
        card = page.locator("[data-testid=request-approval]")
        card.wait_for(timeout=90000)
        card.locator("[data-testid=promotion-details]").wait_for()
        card.get_by_role("button", name="main.py").click()
        card.locator("[data-testid=diff]").get_by_text("cv2.imread").wait_for()
        card.scroll_into_view_if_needed()
        shot(page, "card_promotion_approval")
        card.get_by_role("button", name="Approve").click()
        page.locator("[data-testid=toolbox-tool_promoted]").wait_for(timeout=90000)
        page.locator("[data-testid=toolbox-registered]").scroll_into_view_if_needed()
        shot(page, "card_tool_registered")
        ctx.close()
    t = c.get("/api/toolbox/tools/global/frame_mean_luma").json()
    assert t["status"] == "enabled" and t["author"] == "agent" and t["created_from_run"] == rid
