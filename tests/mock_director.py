"""A scripted director for UI tests, screenshots and the restart e2e: plan → memory → storyboard →
approval → render → QC → present (video with chapters, files, palette, timeline, A/B) → self-review
→ finish. Every turn is a function of the request, so ids produced at runtime can be referenced."""
from __future__ import annotations

import json
import re

from mock_llm import call

SCENE = """
from luma_engine.templates.fan_unfold import FanUnfold
scene = FanUnfold(logo="assets/veyra-symbol.svg", wordmark="Veyra", background="#0B0F2A", width=1080, height=1080, fps=24, duration=3)
"""
SCENE_B = SCENE.replace('background="#0B0F2A"', 'background="#101A3A"')

PLAN = [
    {"title": "Discovery", "children": [
        {"title": "Read the brand kit", "acceptance_criteria": "colours and fonts recorded in memory"},
        {"title": "Storyboard", "acceptance_criteria": "storyboard approved by the user"}]},
    {"title": "Production", "children": [
        {"title": "Render the outro", "priority": "high", "acceptance_criteria": "render finished; QC passes"},
        {"title": "Present & deliver", "acceptance_criteria": "video with chapters + deliverables presented; self-review clean"}]},
]


def _tools(body) -> str:
    return "\n".join(m["content"] for m in body["messages"] if m["role"] == "tool")


def _arts(body) -> list[str]:
    return re.findall(r"as artifact (art_[0-9a-f]+)", _tools(body))


def turns(render_wait_s: int = 600, slow_render: bool = False) -> list:
    """The scripted conversation. With slow_render the render is spawned as a job and polled, so a
    restart can interrupt it."""
    t = [
        {"text": "I'll plan this outro first, then read the brand kit.",
         "tool_calls": [call("todo_write", items=PLAN), call("todo_update", id="Read the brand kit", status="in_progress"),
                        call("report_progress", stage="Discovery", percent=5)]},
        {"text": "The mark is a five-blade fan with a warm gradient. Recording the palette.",
         "tool_calls": [call("inspect_asset", path="assets/veyra-symbol.svg"),
                        call("memory_write", key="palette", value="#FF5A5F → #FFB547 gradient on #0B0F2A; wordmark #FFE0A3"),
                        call("present_palette", colors=[{"hex": "#FF5A5F", "name": "Coral", "usage": "gradient start"},
                                                        {"hex": "#FFB547", "name": "Amber", "usage": "gradient end"},
                                                        {"hex": "#0B0F2A", "name": "Night", "usage": "background"},
                                                        {"hex": "#FFE0A3", "name": "Cream", "usage": "wordmark"}])]},
        {"tool_calls": [call("todo_update", id="Read the brand kit", status="done", evidence=["assets/veyra-symbol.svg"], note="palette in memory"),
                        call("todo_update", id="Storyboard", status="in_progress"),
                        call("write_file", path="work/scene.py", content=SCENE), call("write_file", path="work/scene_b.py", content=SCENE_B)]},
        {"text": "Here is the storyboard — three beats in three seconds.",
         "tool_calls": [call("present_storyboard", scene_path="work/scene.py", timings=[0.3, 1.2, 2.6],
                             notes=["Seed of light at the hinge", "Blades unfold on staggered springs", "Lockup settles and holds"]),
                        call("report_progress", stage="Storyboard", percent=20)]},
        lambda body: {"tool_calls": [call("request_approval", title="Storyboard & final render",
                                          summary="Three beats, 3 s at 1080×1080 / 24 fps. Next: final render (~1 min) and QC.",
                                          artifacts=[_arts(body)[-1]])]},
        lambda body: {"tool_calls": [call("todo_update", id="Storyboard", status="done", evidence=[_arts(body)[-1]], note="approved by the user"),
                                     call("todo_update", id="Render the outro", status="in_progress"),
                                     call("checkpoint_create", label="Storyboard approved"),
                                     call("report_progress", stage="Final render", percent=40, eta_s=60)]},
    ]
    if slow_render:
        t += [
            {"tool_calls": [call("terminal_spawn", name="render-final", command="python -m luma_engine pipeline scene.py --out ../renders/final --name final --workers 1")]},
            {"tool_calls": [call("terminal_run", command="sleep 20; ls renders/final | head", timeout_s=120)]},
            {"tool_calls": [call("terminal_poll", name="render-final")]},
        ]
    else:
        t += [{"tool_calls": [call("render_final", scene_path="work/scene.py", wait_s=render_wait_s)]}]
    t += [
        {"tool_calls": [call("terminal_run", command="mkdir -p outputs && cp renders/final/final.mp4 outputs/veyra_outro.mp4 && "
                                                     "cp renders/final/end_card.png outputs/veyra_outro_end_card.png", timeout_s=120)]},
        {"tool_calls": [call("qc_report", output="outputs/veyra_outro.mp4", reference="renders/final/end_card.png", events_path="renders/final/events.json")]},
        lambda body: {"text": "QC passes. Here is the film — chapters mark the key beats.",
                      "tool_calls": [call("present_video", path="outputs/veyra_outro.mp4", title="Veyra outro", caption="final",
                                          chapters=[{"t": 0.3, "label": "Seed"}, {"t": 1.2, "label": "Unfold"}, {"t": 2.6, "label": "Lockup"}]),
                                     call("present_timeline", events_path="renders/final/events.json", video="outputs/veyra_outro.mp4"),
                                     call("present_file", path="outputs/veyra_outro_end_card.png", title="End card", description="Reference lockup PNG"),
                                     call("present_code", path="work/scene.py", highlight_lines=[[2, 3]])]},
        {"tool_calls": [call("todo_update", id="Render the outro", status="done", evidence=["outputs/veyra_outro.mp4", "outputs/qc_veyra_outro.json"]),
                        call("todo_update", id="Present & deliver", status="in_progress"),
                        call("self_review", checklist_name="delivery")]},
        {"tool_calls": [call("todo_update", id="Present & deliver", status="done", evidence=["outputs/veyra_outro.mp4"]),
                        call("notify", message="Final render done — Veyra outro delivered", level="success"),
                        call("report_progress", stage="Delivered", percent=100)]},
        {"tool_calls": [call("finish", summary="**Veyra outro** — a 3 s fan unfold: seed of light, staggered springs, exact lockup hold. QC passes.",
                             outputs=["outputs/veyra_outro.mp4", "outputs/veyra_outro_end_card.png"])]},
    ]
    return t


def followup_turns() -> list:
    """A follow-up that re-presents a second version (v2) of the video for A/B comparison."""
    return [
        {"tool_calls": [call("render_final", scene_path="work/scene_b.py", name="final_b", wait_s=600)]},
        {"tool_calls": [call("terminal_run", command="cp renders/final_b/final_b.mp4 outputs/veyra_outro_b.mp4")]},
        lambda body: {"tool_calls": [call("present_video", path="outputs/veyra_outro_b.mp4", title="Veyra outro", caption="deeper night background",
                                          chapters=[{"t": 0.3, "label": "Seed"}, {"t": 1.2, "label": "Unfold"}, {"t": 2.6, "label": "Lockup"}])]},
        {"text": "v2 is on the shelf — compare it with v1 in Artifacts."},
    ]


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps([x if isinstance(x, dict) else "<dynamic>" for x in turns()], indent=1)[:2000])
