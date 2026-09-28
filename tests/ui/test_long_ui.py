"""Playwright: the long-job UI with the scripted director — plan panel updates live, the approval card
works (button and `A`), presentation cards render, artifacts version + compare slider works, memory,
checkpoints, notifications, command palette."""
import time
from pathlib import Path

import httpx
import pytest
from conftest import shot
from mock_director import followup_turns, turns
from mock_llm import MockServer

pytestmark = pytest.mark.ui
ROOT = Path(__file__).resolve().parents[2]
USER, PASSWORD = "director_ui", "a long password 123"


@pytest.fixture(scope="module")
def setup(server):
    mock = MockServer(turns() + followup_turns())
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
    yield {"project": p, "client": c, "mock": mock}
    mock.__exit__()
    c.close()


def login(page, base):
    page.goto(base)
    page.get_by_label("Username").fill(USER)
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Sign in").last.click()
    page.get_by_label("Username").wait_for(state="detached", timeout=20000)


def test_long_job_ui(server, browser, setup):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    login(page, server["url"])
    page.get_by_text("Veyra outro").first.click()
    page.get_by_role("textbox", name="Message the director").fill("Make a 3 s logo outro from the Veyra mark: unfold the blades, then hold the lockup.")
    page.get_by_role("button", name="Send").click()

    # --- plan panel updates live
    page.get_by_role("tab", name="Plan").click()
    page.locator("[data-testid=todo-row]").first.wait_for(timeout=30000)
    assert page.locator("[data-testid=todo-row]").count() == 6
    page.locator("[data-testid=todo-row][data-status=done]").first.wait_for(timeout=30000)  # "Read the brand kit" done, live
    assert "/ 4" in page.locator("[data-testid=plan-progress]").inner_text()

    # --- approval card with the storyboard; approve with the button
    card = page.locator("[data-testid=request-approval]")
    card.wait_for(timeout=120000)
    card.locator("[data-testid=card-storyboard]").wait_for()
    shot(page, "approval")
    page.get_by_role("tab", name="Plan").click()
    shot(page, "plan_panel")
    card.get_by_role("button", name="Approve").click()
    card.get_by_text("Answered:").wait_for(timeout=20000)

    # --- the run delivers: video card with chapters, timeline, file, code, palette
    page.get_by_text("Film delivered").wait_for(timeout=600000)
    video = page.locator("[data-testid=card-video]").last
    video.scroll_into_view_if_needed()
    for ch in ("Seed", "Unfold", "Lockup"):
        assert video.get_by_role("button", name=ch).count() == 1
    for t in ("timeline", "file", "code", "palette", "storyboard"):
        assert page.locator(f"[data-testid=card-{t}]").count() >= 1, t
    assert page.locator("[data-testid=plan-progress]").inner_text().strip() == "4 / 4"
    shot(page, "delivered")
    page.locator("[data-testid=card-timeline]").scroll_into_view_if_needed()
    shot(page, "timeline_card")
    for t in ("palette", "code", "file", "table", "storyboard"):
        page.locator(f"[data-testid=card-{t}]").first.scroll_into_view_if_needed()
        shot(page, f"card_{t}")

    # --- memory, checkpoints, notifications, palette
    page.get_by_role("button", name="More panels").click()
    page.get_by_role("menuitem", name="Memory").click()
    page.locator("[data-testid=memory-panel]").get_by_text("palette").wait_for()
    shot(page, "memory")
    page.get_by_role("button", name="More panels").click()
    page.get_by_role("menuitem", name="Checkpoints").click()
    page.locator("[data-testid=checkpoints-panel]").get_by_text("Storyboard approved").wait_for()
    shot(page, "checkpoints")
    page.get_by_role("button", name="Notifications (1 unread)").click()
    page.get_by_role("button", name="Final render done — Veyra outro delivered").wait_for()
    shot(page, "notifications")
    page.keyboard.press("Escape")
    page.keyboard.press("Control+k")
    page.get_by_role("textbox", name="Search commands").fill("render the outro")
    page.get_by_role("option").first.wait_for()
    shot(page, "command_palette")
    page.keyboard.press("Escape")

    # --- follow-up → v2 → compare slider in Artifacts
    page.get_by_role("textbox", name="Message the director").fill("Try a deeper night background")
    page.get_by_role("button", name="Send").click()
    page.get_by_text("v2 is on the shelf").wait_for(timeout=600000)
    page.locator("[data-testid=card-grid]").first.scroll_into_view_if_needed()
    assert page.locator("[data-testid=card-grid]").first.locator("img").count() == 2
    shot(page, "card_grid")
    page.locator("[data-testid=card-image]").last.scroll_into_view_if_needed()
    shot(page, "card_reframe")
    page.get_by_role("tab", name="Artifacts").click()
    panel = page.locator("[data-testid=artifacts-panel]")
    tile = panel.locator("[data-testid=artifact-tile]", has_text="Veyra outro").first
    tile.get_by_role("button", name="Compare versions").click()
    comp = page.locator("[data-testid=comparison]")
    comp.wait_for()
    slider = comp.locator("[data-testid=compare-slider]")
    slider.fill("20")
    clip = comp.locator("div[style*='clip-path']").first.get_attribute("style")
    assert "80%" in clip, clip  # the A layer is clipped to 20 %
    shot(page, "compare")
    page.keyboard.press("Escape")
    shot(page, "artifacts")
    assert not errors, errors
    ctx.close()


def test_keyboard_approve(server, browser, setup):
    """`A` approves a pending approval (new run with a one-turn script)."""
    from mock_llm import call

    c, p, mock = setup["client"], setup["project"], setup["mock"]
    mock.push({"tool_calls": [call("request_approval", title="Quick check", summary="press A")]}, {"text": "thanks"})
    r = c.post(f"/api/projects/{p['id']}/runs", json={"text": "ask me"}).json()
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    page = ctx.new_page()
    login(page, server["url"])
    page.get_by_text("Veyra outro").first.click()
    page.locator("[data-testid=request-approval]").wait_for(timeout=60000)
    page.locator("body").click(position={"x": 5, "y": 5})
    page.keyboard.press("a")
    t0 = time.time()
    while time.time() - t0 < 30 and c.get(f"/api/runs/{r['id']}").json()["status"] not in ("idle", "completed"):
        time.sleep(0.5)
    assert c.get(f"/api/runs/{r['id']}/requests").json()[0]["answer"]["choice"] == "Approve"
    ctx.close()


def test_subagents_and_settings_ui(server, browser, setup):
    """Sub-agent cards expand into nested timelines; collaboration settings are editable."""
    import json
    import re

    from mock_llm import MockServer, call

    c = setup["client"]
    p = c.post("/api/projects", json={"name": "Voice options"}).json()
    c.patch(f"/api/projects/{p['id']}", json={"settings": {**p["settings"], "subagents": True}})

    def brain(body):
        sysmsg = body["messages"][0]["content"]
        tools = [m for m in body["messages"] if m["role"] == "tool"]
        if "You are a sub-agent" in sysmsg:
            label = re.search(r"option (\w+)", next(m["content"] for m in body["messages"] if m["role"] == "user")).group(1)
            if not tools:
                return {"text": f"Writing the {label} script.", "tool_calls": [call("write_file", path=f"work/voices/{label}.md", content=f"# {label}")]}
            return {"tool_calls": [call("subagent_report", result={"voice": label, "script": f"work/voices/{label}.md"}, summary=f"{label} voice direction ready")]}
        if not tools:
            return {"text": "Exploring three voice directions in parallel.", "tool_calls": [
                call("todo_write", items=[{"title": "Voice directions"}]),
                *[call("spawn_subagent", task=f"Write voice option {n}: a 2-line read in a {n} tone", tools_allowed=["write_file"], label=n)
                  for n in ("calm", "bold", "playful")]]}
        return {"text": "All three directions are ready."}

    mock = MockServer([brain] * 20)
    mock.__enter__()
    try:
        c.post("/api/settings/remember", json={"llm_api_key": "sk-mock-ui-0123456789", "llm_base_url": mock.url, "llm_model": "mock-director"})
        rid = c.post(f"/api/projects/{p['id']}/runs", json={"text": "Give me three voice directions"}).json()["id"]
        t0 = time.time()
        while time.time() - t0 < 60 and c.get(f"/api/runs/{rid}").json()["status"] not in ("idle", "completed"):
            time.sleep(0.5)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        login(page, server["url"])
        page.get_by_text("Voice options").first.click()
        cards = page.locator("[data-testid=subagent]")
        cards.first.wait_for(timeout=30000)
        assert cards.count() == 3
        cards.first.locator("button").first.click()
        cards.first.get_by_text("Wrote a file").wait_for(timeout=20000)
        shot(page, "subagents")
        page.get_by_role("button", name="Brief and output settings").click()
        page.get_by_role("switch", name="Autopilot").wait_for()
        page.get_by_role("switch", name="Autopilot").scroll_into_view_if_needed()
        shot(page, "collaboration_settings")
        ctx.close()
    finally:
        c.post("/api/settings/remember", json={"llm_api_key": "sk-mock-ui-0123456789", "llm_base_url": setup["mock"].url, "llm_model": "mock-director"})
        mock.__exit__()
    assert json
