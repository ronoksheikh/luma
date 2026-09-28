"""present_* tools, artifact versioning and the artifact REST API."""
import io
import json
import re
import zipfile

from conftest import events
from mock_llm import MockServer, call
from test_agent import finished, start

MAKE_MEDIA = (
    "mkdir -p outputs audio && "
    "ffmpeg -v error -y -f lavfi -i testsrc=size=320x180:rate=30:duration=2 -f lavfi -i sine=f=440:d=2 -shortest "
    "-c:v libx264 -pix_fmt yuv420p -c:a aac outputs/draft.mp4 && "
    "ffmpeg -v error -y -f lavfi -i testsrc2=size=320x180:rate=30:duration=2 -c:v libx264 -pix_fmt yuv420p outputs/draft_b.mp4 && "
    "ffmpeg -v error -y -f lavfi -i sine=f=220:d=1.5 audio/vo.wav && "
    "ffmpeg -v error -y -f lavfi -i testsrc=size=160x90:rate=1:duration=1 -frames:v 1 work/still.png && "
    "python3 -c \"import zipfile; z=zipfile.ZipFile('outputs/src.zip','w'); z.writestr('work/scene.py','x=1'); z.close()\" && "
    "echo '{\"words\": [{\"text\": \"Hello\", \"start\": 0.1, \"end\": 0.5}, {\"text\": \"world\", \"start\": 0.6, \"end\": 1.1}], \"duration\": 1.5}' > audio/vo.words.json && "
    "echo '{\"duration\": 2, \"fps\": 30, \"events\": [{\"name\": \"seed\", \"t\": 0.1, \"kind\": \"impact\"}, {\"name\": \"boom\", \"t\": 1.2, \"kind\": \"boom\"}], \"spans\": {}}' > work/events.json"
)

SCENE = """
from luma_engine import Scene
class S(Scene):
    bloom = None
    def draw(self, f, t):
        f.fill("#2970EC" if t < 1 else "#07358F")
scene = S(width=160, height=90, fps=10, duration=2)
"""


def arts_in(body):
    """artifact ids reported by earlier present_* results, in order."""
    return re.findall(r"artifact (art_[0-9a-f]+)", json.dumps([m for m in body["messages"] if m["role"] == "tool"]))


def test_present_tools_versioning_and_rest(client, project):
    chapters = [{"t": 0.1, "label": "Seed"}, {"t": 1.3, "label": "Unfold"}]
    script = [
        {"tool_calls": [call("todo_write", items=[{"title": "Present"}]), call("terminal_run", command=MAKE_MEDIA, timeout_s=120),
                        call("write_file", path="work/scene.py", content=SCENE)]},
        {"tool_calls": [call("present_video", path="outputs/draft.mp4", title="Outro draft", chapters=chapters, caption="first pass")]},
        {"tool_calls": [call("present_video", path="outputs/draft_b.mp4", title="Outro draft")]},  # same group → v2
        {"tool_calls": [call("present_video", path="outputs/draft.mp4", title="Bad", chapters=[{"t": 9, "label": "late"}])]},
        lambda body: {"tool_calls": [call("present_comparison", a=arts_in(body)[0], b=arts_in(body)[1], mode="slider", labels=["v1", "v2"])]},
        {"tool_calls": [
            call("present_image", paths=["work/still.png", "work/still.png"], title="Stills", layout="grid"),
            call("present_audio", path="audio/vo.wav", title="Voice", transcript="audio/vo.words.json"),
            call("present_file", path="outputs/src.zip", title="Scene source"),
            call("present_file", path="work/events.json", title="Events"),
            call("present_timeline", events_path="work/events.json", video="outputs/draft.mp4"),
            call("present_code", path="work/scene.py", highlight_lines=[[4, 5]]),
            call("present_table", title="Specs", columns=["k", "v"], rows=[["fps", 30], ["size", "320x180"]]),
            call("present_palette", colors=[{"hex": "#2970ec", "name": "Lumademy Blue", "usage": "primary"}, {"hex": "#07358F", "name": "Deep"}]),
        ]},
        {"tool_calls": [call("present_storyboard", scene_path="work/scene.py", timings=[0.0, 1.5], notes=["open on blue", "deep blue close"])]},
        {"text": "done"},
    ]
    with MockServer(script) as mock:
        rid = start(client, project, mock)
        finished(client, rid)
        reqs = mock.state["requests"]
    outs = lambda i: [m["content"] for m in reqs[i]["messages"] if m["role"] == "tool"]  # noqa: E731
    assert "v1" in outs(2)[-1] and "v2" in outs(3)[-1]
    assert "outside the video" in outs(4)[-1]
    assert all(o.startswith("Presented") for o in outs(6)[-8:]), outs(6)[-8:]
    assert "storyboard with 2 shots" in outs(7)[-1]

    pres = [e["data"] for e in events(client, rid) if e["type"] == "present"]
    assert sorted(p["card"] for p in pres) == sorted(["video", "video", "comparison", "image", "audio", "file", "file", "timeline", "code",
                                                      "table", "palette", "storyboard"])
    by = {}
    for p in pres:
        by.setdefault(p["card"], []).append(p["artifact"])
    v1, v2 = by["video"]
    assert v1["meta"]["chapters"] == [{"t": 0.1, "label": "Seed"}, {"t": 1.3, "label": "Unfold"}]
    assert v1["meta"]["frames"] == 60 and v1["meta"]["has_audio"] and v1["meta"]["poster_url"]
    assert v2["version"] == 2 and v2["version_group"] == v1["version_group"]
    comp = by["comparison"][0]["meta"]
    assert comp["a"]["artifact_id"] == v1["id"] and comp["kind"] == "video" and comp["labels"] == ["v1", "v2"]
    audio = by["audio"][0]["meta"]
    assert audio["words"][1]["text"] == "world" and len(audio["peaks"]) == 480 and audio["duration"] == 1.5
    files = {a["title"]: a["meta"]["preview"] for a in by["file"]}
    assert files["Scene source"]["kind"] == "zip" and files["Scene source"]["entries"][0]["name"] == "work/scene.py"
    assert files["Events"]["language"] == "json"
    tl = by["timeline"][0]["meta"]
    assert [e["track"] for e in tl["events"]] == ["picture", "audio"] and tl["video"]["kind"] == "video"
    code = by["code"][0]["meta"]
    assert code["highlight"] == [4, 5] and code["language"] == "python"
    assert by["palette"][0]["meta"]["colors"][0]["hex"] == "#2970EC"
    assert len(by["image"][0]["meta"]["images"]) == 2 and by["image"][0]["meta"]["layout"] == "grid"
    shots = by["storyboard"][0]["meta"]["shots"]
    assert [s["t"] for s in shots] == [0.0, 1.5] and shots[0]["url"].endswith(".png")

    # --- REST
    pid = project["id"]
    arts = client.get(f"/api/projects/{pid}/artifacts").json()
    assert len(arts) == 12
    vers = client.get(f"/api/projects/{pid}/artifacts/versions/{v1['version_group']}").json()
    assert [v["version"] for v in vers] == [1, 2]
    r = client.patch(f"/api/artifacts/{v1['id']}", json={"favorite": True, "title": "Outro v1 ⭐"})
    assert r.json()["favorite"] is True and r.json()["title"] == "Outro v1 ⭐"
    z = client.get(f"/api/projects/{pid}/artifacts.zip")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert "outputs/draft.mp4" in names and "audio/vo.wav" in names and "ARTIFACTS.txt" in names
    fav = zipfile.ZipFile(io.BytesIO(client.get(f"/api/projects/{pid}/artifacts.zip", params={"favorites": True}).content)).namelist()
    assert "outputs/draft.mp4" in fav and "audio/vo.wav" not in fav
    assert client.delete(f"/api/artifacts/{by['table'][0]['id']}").status_code == 200
    assert len(client.get(f"/api/projects/{pid}/artifacts").json()) == 11


def test_artifacts_are_private_to_their_owner(client, project, server):
    from conftest import login_client

    other = login_client(server, "someone_else", "another password 1")
    try:
        assert other.get(f"/api/projects/{project['id']}/artifacts").status_code == 404
        assert other.patch("/api/artifacts/art_nope", json={"favorite": True}).status_code == 404
    finally:
        other.close()
