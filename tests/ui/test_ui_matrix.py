"""Screenshot matrix for the UI refresh: the same seven views in light/dark at desktop and phone size.

    LUMA_CHROMIUM=... LUMA_MATRIX=docs/ui-refresh/after pytest -m ui tests/ui/test_ui_matrix.py

Screenshots are taken only when LUMA_MATRIX is set (files are named <view>-<theme>-<w>x<h>.png); the layout checks
(no horizontal page scroll, no clipped inspector tabs, More button on screen) always run, at 390 / 768 / 1280 / 1440 px.
The theme is chosen with `localStorage["luma.theme"]` (ignored by builds that predate the theme switch, so it also
captures "before").
"""
import os
import time
from pathlib import Path

import httpx
import pytest
from mock_director import turns
from mock_llm import MockServer

pytestmark = pytest.mark.ui
OUT = os.environ.get("LUMA_MATRIX")
ROOT = Path(__file__).resolve().parents[2]
USER, PASSWORD = "matrix_ui", "a long password 123"
COMBOS = [(1440, 900, "light"), (1440, 900, "dark"), (390, 844, "light"), (390, 844, "dark"), (1280, 800, "dark"), (768, 1024, "light")]
SHOT_SIZES = {(1440, 900), (390, 844)}
AXE = os.environ.get("LUMA_AXE")  # path to axe-core's axe.min.js: also audit colour contrast (WCAG AA) on every view


@pytest.fixture(scope="module")
def setup(server):
    from app.toolbox import boot, venv

    t0 = time.time()
    while not (venv.ready.is_set() and boot.state.get("done")) and time.time() - t0 < 300:
        time.sleep(0.5)
    mock = MockServer(turns())
    mock.__enter__()
    c = httpx.Client(base_url=server["url"], headers={"X-Luma-Client": "1"}, timeout=60)
    c.post("/api/auth/signup", json={"username": USER, "password": PASSWORD})
    p = c.post("/api/projects", json={"name": "Veyra outro"}).json()
    c.patch(f"/api/projects/{p['id']}", json={"brief": "A 3 s square logo outro for Veyra.",
                                              "settings": {**p["settings"], "width": 1080, "height": 1080, "fps": 24, "duration": 3}})
    with open(ROOT / "samples/veyra-symbol.svg", "rb") as f:
        c.post(f"/api/projects/{p['id']}/assets", files={"files": ("veyra-symbol.svg", f, "image/svg+xml")})
    c.post("/api/settings/remember", json={"llm_api_key": "sk-mock-ui-0123456789", "llm_base_url": mock.url, "llm_model": "mock-director"})
    c.put("/api/settings", json={"llm_base_url": mock.url, "llm_model": "mock-director"})
    rid = c.post(f"/api/projects/{p['id']}/runs", json={"text": "Make a 3 s logo outro from the Veyra mark."}).json()["id"]
    t0 = time.time()
    while time.time() - t0 < 600:
        for r in c.get(f"/api/runs/{rid}/requests").json():
            if r.get("status") == "pending" and r["kind"] == "approval":
                c.post(f"/api/runs/{rid}/requests/{r['id']}/answer", json={"choice": (r["payload"].get("choices") or ["Approve"])[0]})
        if c.get(f"/api/runs/{rid}").json()["status"] in ("idle", "completed"):
            break
        time.sleep(1)
    yield {"project": p}
    mock.__exit__()
    c.close()


def _ctx(browser, w, h, theme):
    ctx = browser.new_context(viewport={"width": w, "height": h}, color_scheme="light", device_scale_factor=1)
    ctx.add_init_script(f"try{{localStorage.setItem('luma.theme','{theme}')}}catch(e){{}}")
    return ctx


AXE_FINDINGS: list = []


def _axe(page, name, w, theme):
    if not AXE:
        return
    page.evaluate(Path(AXE).read_text())
    res = page.evaluate("() => axe.run(document, {runOnly: ['color-contrast'], resultTypes: ['violations']})")
    for v in res["violations"]:
        for n in v["nodes"]:
            d = n["any"][0]["data"] if n.get("any") else {}
            AXE_FINDINGS.append({"view": name, "theme": theme, "w": w, "target": n["target"], "ratio": d.get("contrastRatio"), "fg": d.get("fgColor"),
                                 "bg": d.get("bgColor"), "size": d.get("fontSize"), "text": (n.get("html") or "")[:120]})


def _snap(page, name, w, h, theme):
    page.wait_for_timeout(700)  # let open/close animations settle (axe would measure blended colours mid-fade)
    _axe(page, name, w, theme)
    over = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
    assert over <= 1, f"{name} at {w}x{h}: page scrolls horizontally by {over}px"
    if OUT and (w, h) in SHOT_SIZES:
        Path(OUT).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(OUT) / f"{name}-{theme}-{w}x{h}.png"))


def _tabs_fit(page, w):
    """Inspector tabs: nothing clipped, and the More button is on screen."""
    bar = page.get_by_role("tablist", name="Inspector")
    dims = bar.evaluate("el => ({sw: el.scrollWidth, cw: el.clientWidth, tabs: [...el.children].map(c => Math.round(c.getBoundingClientRect().width))})")
    assert dims["sw"] <= dims["cw"] + 1, f"inspector tabs overflow at {w}px: {dims}"
    box = page.get_by_role("button", name="More panels").bounding_box()
    assert box and box["x"] + box["width"] <= w + 1, f"More button off screen at {w}px"


def _open_tab(page, name, mobile):
    if mobile:
        try:
            page.locator("[data-slot=toast]").first.wait_for(state="detached", timeout=20000)
        except Exception:
            pass
        page.locator("button:visible, [role=radio]:visible", has_text="Studio").first.click()
    tab = page.get_by_role("tab", name=name)
    if tab.count() and tab.first.is_visible():
        tab.first.click()
    else:
        page.get_by_role("button", name="More panels").click()
        page.get_by_role("menuitem", name=name).click()
    page.wait_for_timeout(300)


@pytest.mark.parametrize("w,h,theme", COMBOS)
def test_matrix(server, browser, setup, w, h, theme):
    mobile = w < 1024  # sidebar becomes a drawer and the panes a Director/Studio switch below `lg`
    # sign-in (logged-out context)
    ctx = _ctx(browser, w, h, theme)
    page = ctx.new_page()
    page.goto(server["url"])
    page.get_by_label("Username").wait_for()
    _snap(page, "signin", w, h, theme)
    page.get_by_label("Username").fill(USER)
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Sign in").last.click()
    page.get_by_label("Username").wait_for(state="detached", timeout=20000)
    if mobile:
        page.get_by_role("button", name="Projects", exact=True).first.click()
    page.locator("span:visible", has_text="Veyra outro").first.click()
    page.wait_for_timeout(500)
    # chat (top of the run, then the presentation cards)
    page.locator("[data-testid=card-video]").last.wait_for(timeout=30000)
    page.evaluate("document.querySelector('[aria-live=polite]').scrollTop = 0")
    _snap(page, "chat", w, h, theme)
    page.locator("[data-testid=card-video]").last.scroll_into_view_if_needed()
    _snap(page, "run-cards", w, h, theme)
    for kind in ("timeline", "file", "code", "palette", "storyboard", "table"):
        loc = page.locator(f"[data-testid=card-{kind}]").first
        if loc.count():
            loc.scroll_into_view_if_needed()
            _snap(page, f"card-{kind}", w, h, theme)
    grp = page.locator("[data-testid=step-group]").first
    if grp.count():
        grp.scroll_into_view_if_needed()
        grp.get_by_role("button").first.click()
        grp.locator("[data-testid=tool-step] button").first.click()
        _snap(page, "steps-open", w, h, theme)
    page.keyboard.press("Control+k")
    page.get_by_role("textbox", name="Search commands").fill("render")
    _snap(page, "palette", w, h, theme)
    page.keyboard.press("Escape")
    if not mobile:
        page.get_by_text(USER, exact=True).first.click()
        _snap(page, "account-menu", w, h, theme)
        page.keyboard.press("Escape")
    # inspector tabs
    _open_tab(page, "Plan", mobile)
    _tabs_fit(page, w)
    _snap(page, "inspector", w, h, theme)
    for tab in ("Artifacts", "Toolbox", "Terminal", "Assets", "Preview"):  # the longest selected label must fit too
        page.get_by_role("tab", name=tab).click()
        _tabs_fit(page, w)
    _open_tab(page, "Preview", False)
    page.locator("[data-testid=player-video]:visible").first.wait_for()
    _snap(page, "preview", w, h, theme)
    _open_tab(page, "QC", False)
    page.get_by_text("All checks passed").wait_for(timeout=20000)
    _snap(page, "qc", w, h, theme)
    # toolbox + tool detail
    _open_tab(page, "Toolbox", False)
    panel = page.locator("[data-testid=toolbox-panel]")
    panel.locator("[data-testid=tool-row][data-name=svg_split_exact]").wait_for(timeout=20000)
    _snap(page, "toolbox", w, h, theme)
    panel.locator("[data-testid=tool-row][data-name=svg_split_exact]").click()
    page.locator("[data-testid=tool-detail]").locator("[data-testid=tool-readme]").wait_for()
    _snap(page, "tool-detail", w, h, theme)
    page.keyboard.press("Escape")
    # settings
    page.keyboard.press("Control+,")
    page.get_by_role("dialog").first.wait_for()
    _snap(page, "settings", w, h, theme)
    ctx.close()


def test_zz_axe_report():
    """Runs last: prints and stores the contrast findings when LUMA_AXE is set."""
    if not AXE:
        pytest.skip("LUMA_AXE not set")
    import json

    out = Path(OUT or ".") / "axe-contrast.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(AXE_FINDINGS, indent=1))
    for f in AXE_FINDINGS:
        print(f"AXE {f['theme']:5} {f['w']:4} {f['view']:11} {f['ratio']} fg={f['fg']} bg={f['bg']} {f['size']} {f['target']} {f['text'][:70]!r}")
    assert not AXE_FINDINGS, f"{len(AXE_FINDINGS)} contrast violations (see {out})"
