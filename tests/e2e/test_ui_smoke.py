"""Playwright smoke test of the web UI (against a running container).

    LUMA_E2E_URL=http://127.0.0.1:8080 pytest -m e2e tests/e2e/test_ui_smoke.py
Set LUMA_SCREENSHOTS=docs/screenshots to save README screenshots.
Uses the Chromium at $LUMA_CHROMIUM (or Playwright's default).

Note: Playwright's Chromium ships without an H.264 decoder, so the preview shows the end-card
poster instead of playing the MP4; ffprobe checks of the file live in test_e2e.py.
"""
import os
import time
import uuid
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.e2e
BASE = os.environ.get("LUMA_E2E_URL", "http://127.0.0.1:8080")
SHOTS = os.environ.get("LUMA_SCREENSHOTS")
SAMPLES = Path(__file__).resolve().parents[2] / "samples"
USERNAME = f"ui_{uuid.uuid4().hex[:8]}"
PASSWORD = "smoke test password"


def shot(page, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"))


@pytest.fixture(scope="module")
def browser():
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        exe = os.environ.get("LUMA_CHROMIUM")
        b = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        yield b
        b.close()


@pytest.fixture(scope="module")
def account():
    """Make sure the test account exists (tests can then run in any order or alone)."""
    r = httpx.post(f"{BASE}/api/auth/signup", headers={"X-Luma-Client": "1"}, json={"username": USERNAME, "password": PASSWORD}, timeout=30)
    assert r.status_code in (201, 409), r.text


def sign_in(page, create=False):
    page.goto(BASE)
    page.get_by_label("Username").wait_for()
    if create:
        page.get_by_role("tab", name="Create account").click()
    page.get_by_label("Username").fill(USERNAME)
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Create account" if create else "Sign in").last.click()
    page.get_by_label("Username").wait_for(state="detached", timeout=20000)
    page.get_by_role("region", name="Director").or_(page.get_by_text("Start a project")).first.wait_for(timeout=20000)


def test_signup_demo_upload_terminal_settings(browser):
    # Runs first and creates the account through the UI.
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    # 1. sign-in screen, then create an account
    page.goto(BASE)
    page.get_by_role("button", name="Sign in").last.wait_for()
    shot(page, "sign_in")
    sign_in(page, create=True)

    # 2. keyless demo → live run in the director timeline
    page.get_by_role("button", name="Run the demo").click()
    page.get_by_text("Rendered the film").first.wait_for(timeout=30000)
    page.get_by_text("Demo — Veyra outro").first.wait_for()
    page.get_by_text("Frame ").first.wait_for(timeout=120000)
    shot(page, "studio_rendering")
    deadline = time.time() + 1800
    while time.time() < deadline and not page.get_by_text("Film delivered").count():
        time.sleep(3)
    assert page.get_by_text("Film delivered").count(), "demo did not complete in the UI"
    page.get_by_role("tab", name="Preview").click()
    page.locator("[data-testid=player-video]").first.wait_for()
    shot(page, "studio_delivered")
    page.get_by_role("button", name="More panels").click()
    page.get_by_role("menuitem", name="QC").click()
    page.get_by_text("All checks passed").wait_for(timeout=20000)
    shot(page, "qc")
    page.get_by_role("button", name="More panels").click()
    page.get_by_role("menuitem", name="Media").click()
    page.get_by_role("heading", name="Final mix").wait_for()

    # 3. new project + upload via the Assets tab
    page.get_by_role("button", name="New project").first.click()
    page.get_by_role("button", name="Save").click()
    page.get_by_text("What are we making?").wait_for()
    shot(page, "new_project")
    page.get_by_role("tab", name="Assets").click()
    page.locator("[data-testid=asset-input]").last.set_input_files([str(SAMPLES / "veyra-symbol.svg"), str(SAMPLES / "veyra-brand.pdf")])
    page.get_by_text("2 / 20").wait_for(timeout=30000)
    page.get_by_role("button", name="Inspect veyra-symbol.svg").click()
    page.get_by_text("Automatic analysis").wait_for()
    shot(page, "asset_analysis")
    page.keyboard.press("Escape")

    # 4. terminal: live mirror + take over
    page.get_by_role("tab", name="Terminal").click()
    page.get_by_text("Live sandbox shell").wait_for(timeout=20000)
    page.get_by_role("switch", name="Take over the terminal").click(force=True)
    page.locator(".xterm").click()
    page.keyboard.type("echo smoke-$((40+2))\n")
    page.wait_for_function("() => document.querySelector('.xterm-rows')?.textContent.includes('smoke-42')", timeout=15000)
    shot(page, "terminal")

    # 5. settings: connections form with provider presets
    page.keyboard.press("Control+,")
    page.get_by_role("heading", name="Settings").wait_for()
    page.get_by_role("button", name="Load models").wait_for()
    page.get_by_role("radio", name="OpenAI").click()
    assert page.get_by_label("Base URL").input_value() == "https://api.openai.com/v1"
    page.get_by_role("radio", name="OpenRouter").click()
    assert page.get_by_label("Base URL").input_value() == "https://openrouter.ai/api/v1"
    shot(page, "settings")
    page.keyboard.press("Escape")
    assert not errors, errors
    ctx.close()


def test_data_follows_the_account_to_a_new_browser(browser, account):
    # A fresh context has no cookies or localStorage: it must see the same projects after login.
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    page = ctx.new_page()
    sign_in(page)
    if not page.get_by_text("Demo — Veyra outro").count():  # when run alone, create data via the API first
        page.request.post(f"{BASE}/api/projects", headers={"X-Luma-Client": "1"}, data={"name": "Demo — Veyra outro"})
        page.reload()
    page.get_by_text("Demo — Veyra outro").first.wait_for(timeout=20000)
    # sign out returns to the login screen
    page.get_by_role("button", name=USERNAME).click()
    page.get_by_role("menuitem", name="Sign out").click()
    page.get_by_role("button", name="Sign in").last.wait_for()
    ctx.close()


def test_mobile_layout(browser, account):
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    page = ctx.new_page()
    sign_in(page)
    page.get_by_role("radio", name="Studio").click()
    page.get_by_role("tab", name="Preview").wait_for()
    page.get_by_role("radio", name="Director").click()
    page.get_by_role("button", name="Open projects").click()
    page.get_by_role("dialog", name="Projects").wait_for()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    ctx.close()
    assert httpx.get(f"{BASE}/healthz").status_code == 200
