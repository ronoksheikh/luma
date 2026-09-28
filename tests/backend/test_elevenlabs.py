"""ElevenLabs tools against a mocked HTTP API: probe, budget enforcement, caching, sidecars."""
import asyncio
import json

import pytest
from mock_elevenlabs import MockElevenLabs

KEY = "el-test-key-123456"


@pytest.fixture()
def el(server):
    from app.config import config

    with MockElevenLabs() as m:
        old = config.elevenlabs_base_url
        config.elevenlabs_base_url = m.url
        yield m
        config.elevenlabs_base_url = old


@pytest.fixture()
def ctx(server, project, el):
    from app import db
    from app.agent.loop import ElBudget
    from app.agent.tools.base import ToolContext
    from app.routes_projects import ensure_workspace
    from app.secrets_store import Credentials

    with db.session() as s:
        r = db.Run(project_id=project["id"], kind="agent")
        s.add(r)
    return ToolContext(r.id, project["id"], ensure_workspace(project["id"]), Credentials(elevenlabs_api_key=KEY), {}, False, None,
                       tool_call_id="tc_el", el_budget=ElBudget(r.id, 1000))


def tools():
    from app.agent.tools import available_tools

    return available_tools(True)


def test_el_tools_only_offered_with_key():
    from app.agent.tools import available_tools

    assert not any(n.startswith("el_") for n in available_tools(False))
    assert {"el_tts", "el_sound_effect", "el_music", "el_design_voice", "el_save_designed_voice", "el_speech_to_text",
            "el_list_voices", "el_list_models"} <= set(available_tools(True))


def test_probe_reports_account_and_capabilities(client, el):
    r = client.post("/api/elevenlabs/test", json={"api_key": KEY}).json()
    assert r["ok"] and r["account"]["tier"] == "creator" and r["account"]["characters_remaining"] == 98800
    assert r["capabilities"] == {"tts": True, "voice_design": True, "sfx": True, "music": False, "speech_to_text": True}
    bad = client.post("/api/elevenlabs/test", json={"api_key": "wrong-key-000000"}).json()
    assert not bad["ok"] and bad["checks"][0]["status"] == "fail"


def test_tts_word_timings_budget_and_cache(ctx, el):
    T = tools()
    out = asyncio.run(T["el_tts"].handler(ctx, {"text": "Meet Veyra. Light that opens.", "voice_id": "voice_calm_f", "name": "vo"}))
    assert out.status == "success" and out.ui["chars"] == 29 and not out.ui["cached"]
    words = json.loads((ctx.pdir / "audio/vo.words.json").read_text())["words"]
    assert [w["text"] for w in words] == ["Meet", "Veyra.", "Light", "that", "opens."]
    assert all(w["end"] > w["start"] for w in words) and words == sorted(words, key=lambda w: w["start"])
    side = json.loads((ctx.pdir / "audio/vo.wav.json").read_text())
    assert side["params"]["voice_id"] == "voice_calm_f" and side["chars"] == 29
    # identical request → cache hit, no API call, no charge
    calls = el.state["calls"]["tts"]
    again = asyncio.run(T["el_tts"].handler(ctx, {"text": "Meet Veyra. Light that opens.", "voice_id": "voice_calm_f", "name": "vo_again"}))
    assert again.ui["cached"] and again.ui["chars"] == 0 and el.state["calls"]["tts"] == calls
    assert ctx.el_budget.used() == 29
    # hard budget: refused BEFORE calling the API
    from app.agent.tools.base import ToolError

    with pytest.raises(ToolError, match="budget"):
        asyncio.run(T["el_tts"].handler(ctx, {"text": "x" * 2000, "voice_id": "voice_calm_f"}))
    assert el.state["calls"]["tts"] == calls


def test_long_scripts_are_chunked_and_stitched(ctx, el):
    ctx.el_budget.limit = 100000
    text = " ".join(f"Sentence number {i} is here." for i in range(200))
    out = asyncio.run(tools()["el_tts"].handler(ctx, {"text": text, "voice_id": "voice_calm_f", "name": "long"}))
    assert "chunk" in out.content
    bodies = el.state["tts_bodies"][-3:]
    assert len(el.state["tts_bodies"]) >= 3
    assert bodies[1].get("previous_request_ids") and bodies[2].get("previous_request_ids")  # request stitching
    words = json.loads((ctx.pdir / "audio/long.words.json").read_text())["words"]
    assert len(words) == len(text.split())
    assert all(b["start"] >= a["start"] for a, b in zip(words, words[1:]))  # offsets continue across chunks


def test_sfx_cache_music_unavailable_and_stt(ctx, el):
    T = tools()
    a = asyncio.run(T["el_sound_effect"].handler(ctx, {"prompt": "deep cinematic sub impact", "duration_s": 2}))
    b = asyncio.run(T["el_sound_effect"].handler(ctx, {"prompt": "deep cinematic sub impact", "duration_s": 2}))
    assert el.state["calls"]["sfx"] == 1 and b.ui["cached"] and a.ui["chars"] == 80
    from app.agent.tools.base import ToolError

    with pytest.raises(ToolError):
        asyncio.run(T["el_sound_effect"].handler(ctx, {"prompt": "x", "duration_s": 60}))
    m = asyncio.run(T["el_music"].handler(ctx, {"prompt": "ambient", "duration_s": 10}))
    assert m.status == "error" and "unavailable" in m.content
    asyncio.run(T["el_tts"].handler(ctx, {"text": "hello world", "voice_id": "voice_calm_f", "name": "hw"}))
    s = asyncio.run(T["el_speech_to_text"].handler(ctx, {"path": "audio/hw.wav"}))
    assert "hello world" in s.content
    d = asyncio.run(T["el_design_voice"].handler(ctx, {"description": "a calm warm narrator with a soft british accent"}))
    previews = json.loads(d.content)["previews"]
    assert len(previews) == 3 and (ctx.pdir / previews[0]["path"]).exists()
    v = asyncio.run(T["el_save_designed_voice"].handler(ctx, {"preview_id": previews[0]["preview_id"], "name": "Warm"}))
    assert json.loads(v.content)["voice_id"] == "saved_gen_0"


def test_key_never_reaches_sandbox_or_events(ctx, el, client):
    from app.secrets_store import register_secret

    register_secret(KEY)
    asyncio.run(tools()["el_sound_effect"].handler(ctx, {"prompt": "whoosh", "duration_s": 1}))
    evs = client.get(f"/api/runs/{ctx.run_id}").json()
    assert KEY not in json.dumps(evs)
    for f in (ctx.pdir / "audio").rglob("*.json"):
        assert KEY not in f.read_text()
