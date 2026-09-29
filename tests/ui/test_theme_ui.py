"""Playwright: the theme switch (System / Light / Dark) — account menu and Settings, persisted in localStorage,
applied before first paint (no flash), following prefers-color-scheme on System."""
import httpx
import pytest

pytestmark = pytest.mark.ui
USER, PASSWORD = "theme_ui", "a long password 123"


@pytest.fixture(scope="module")
def account(server):
    c = httpx.Client(base_url=server["url"], headers={"X-Luma-Client": "1"}, timeout=30)
    c.post("/api/auth/signup", json={"username": USER, "password": PASSWORD})
    c.post("/api/projects", json={"name": "Theme check"})
    c.close()


def login(page, base):
    page.goto(base)
    page.get_by_label("Username").fill(USER)
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Sign in").last.click()
    page.get_by_label("Username").wait_for(state="detached", timeout=20000)


def html(page):
    return page.evaluate("""() => { const r = document.documentElement;
      return {dark: r.classList.contains('dark'), light: r.classList.contains('light'), theme: r.dataset.theme,
              scheme: r.style.colorScheme, bg: getComputedStyle(document.body).backgroundColor,
              stored: localStorage.getItem('luma.theme')}; }""")


def test_dark_toggle_persists_and_follows_system(server, browser, account):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="light")
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    login(page, server["url"])
    assert html(page)["theme"] == "light" and not html(page)["dark"]
    light_bg = html(page)["bg"]

    # --- account menu → Dark
    page.get_by_text(USER, exact=True).first.click()
    page.get_by_role("menuitem", name="Dark theme").click()
    h = html(page)
    assert h["dark"] and not h["light"] and h["theme"] == "dark" and h["scheme"] == "dark" and h["stored"] == "dark"
    assert h["bg"] != light_bg

    # --- persists across a reload, and is applied before first paint (checked at DOMContentLoaded, before React renders)
    page.reload(wait_until="domcontentloaded")
    early = page.evaluate("document.documentElement.classList.contains('dark') && document.documentElement.dataset.theme === 'dark'")
    assert early, "theme must be applied by the blocking head script, before the app renders"
    page.get_by_text(USER, exact=True).first.wait_for()
    assert html(page)["dark"] and html(page)["stored"] == "dark"

    # --- Settings → Appearance → Light, then System follows the OS
    page.keyboard.press("Control+,")
    dlg = page.get_by_role("dialog").first
    dlg.get_by_role("tab", name="Appearance").click()
    dlg.get_by_role("radio", name="Light").click()
    assert not html(page)["dark"] and html(page)["stored"] == "light"
    dlg.get_by_role("radio", name="System").click()
    assert html(page)["stored"] is None and not html(page)["dark"]
    page.emulate_media(color_scheme="dark")
    page.wait_for_function("document.documentElement.classList.contains('dark')")
    page.emulate_media(color_scheme="light")
    page.wait_for_function("!document.documentElement.classList.contains('dark')")
    assert not errors, errors
    ctx.close()


def test_system_dark_on_first_visit(server, browser, account):
    """No saved choice + OS dark → dark from the first paint."""
    ctx = browser.new_context(color_scheme="dark")
    page = ctx.new_page()
    page.goto(server["url"], wait_until="domcontentloaded")
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"
    ctx.close()
