"""Self-review checklists (``prompts/checklists/*.md``) and QC-driven plan items.

Checklist lines look like ``- [auto:<check>] text`` (evaluated here) or ``- [manual] text``
(the agent must look and confirm with evidence)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
from sqlalchemy import select

from . import db, plan
from .config import config

LINE = re.compile(r"^\s*-\s*\[(auto:[a-z_.]+|manual)\]\s*(.+)$")


def checklist_dir() -> Path:
    return config.prompts_dir / "checklists"


def checklists() -> list[str]:
    return sorted(p.stem for p in checklist_dir().glob("*.md"))


def parse(name: str) -> list[dict]:
    p = checklist_dir() / f"{name}.md"
    if not re.fullmatch(r"[a-z_]+", name or "") or not p.exists():
        raise KeyError(name)
    items = []
    for ln in p.read_text().splitlines():
        m = LINE.match(ln)
        if m:
            tag, text = m.groups()
            items.append({"check": tag[5:] if tag.startswith("auto:") else None, "text": text.strip()})
    return items


# ------------------------------------------------------------------------------ evidence sources
def latest_qc(pdir: Path) -> tuple[dict | None, str | None]:
    cands = list((pdir / "outputs").glob("qc_*.json")) + list((pdir / "renders").glob("*/qc.json")) + list((pdir / "outputs").glob("qc_report.json"))
    cands = [c for c in cands if c.is_file()]
    if not cands:
        return None, None
    best = max(cands, key=lambda p: p.stat().st_mtime)
    try:
        return json.loads(best.read_text()), str(best.relative_to(pdir))
    except ValueError:
        return None, None


def final_video(pdir: Path) -> Path | None:
    vids = [p for p in (pdir / "outputs").glob("*.mp4") if "captioned" not in p.stem and "preview" not in p.parts]
    return max(vids, key=lambda p: p.stat().st_mtime) if vids else None


def _last_frame(video: Path) -> np.ndarray | None:
    import subprocess

    import cv2

    tmp = video.parent.parent / "work" / ".luma" / "review_last_frame.png"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-sseof", "-0.1", "-i", str(video), "-frames:v", "1", "-update", "1", str(tmp)],
                   capture_output=True, timeout=120)
    img = cv2.imread(str(tmp))
    return None if img is None else cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def _hex(h: str) -> np.ndarray:
    h = h.lstrip("#")
    return np.array([int(h[i : i + 2], 16) for i in (0, 2, 4)], np.int16)


def _share_near(img: np.ndarray, hx: str, tol: int) -> float:
    d = np.abs(img.astype(np.int16) - _hex(hx)).max(axis=2)
    return float((d <= tol).mean())


# ------------------------------------------------------------------------------ evaluation
def evaluate(name: str, pdir: Path, project_id: str, run_id: str, settings: dict) -> list[dict]:
    items = parse(name)
    qc, qc_path = latest_qc(pdir)
    checks = {c["name"]: c for c in (qc or {}).get("checks", [])}
    video = final_video(pdir)
    frame = None
    rows = []
    for it in items:
        c = it["check"]
        row = {"item": it["text"], "check": c or "manual", "status": "manual", "detail": "look and confirm with evidence"}
        if c is None:
            rows.append(row)
            continue
        try:
            if c.startswith("qc."):
                key = c[3:]
                if qc is None:
                    row.update(status="fail", detail="no QC report yet — run qc_report on the delivered MP4")
                elif key not in checks:
                    row.update(status="n/a", detail=f"not part of {qc_path}")
                else:
                    ch = checks[key]
                    row.update(status="pass" if ch["pass"] else "fail", detail=f"{json.dumps(ch.get('value'))[:160]} (expected {ch.get('expected')}) · {qc_path}")
            elif c == "brand.reference_used":
                ok = "final_frame_matches_reference" in checks
                row.update(status="pass" if ok else "fail", detail="QC compared against a reference" if ok else "run qc_report with reference=<end card PNG>")
            elif c in ("brand.avoid_colors", "brand.palette_present"):
                if video is None:
                    row.update(status="fail", detail="no MP4 in outputs/")
                else:
                    if frame is None:
                        frame = _last_frame(video)
                    if frame is None:
                        row.update(status="fail", detail="could not read the final frame")
                    elif c == "brand.avoid_colors":
                        avoid = settings.get("avoid_colors") or []
                        hits = [(h, _share_near(frame, h, 18)) for h in avoid]
                        bad = [f"{h} ({s * 100:.2f}% of pixels)" for h, s in hits if s > 0.001]
                        row.update(status="fail" if bad else "pass", detail=("found " + ", ".join(bad)) if bad else
                                   (f"none of {avoid} in the final frame" if avoid else "no colours to avoid are set"))
                    else:
                        bj = pdir / "work" / "brand.json"
                        cols = sorted({h.upper() for h in re.findall(r"#[0-9A-Fa-f]{6}\b", bj.read_text())}) if bj.exists() else []
                        cols = [h for h in cols if h not in {a.upper() for a in settings.get("avoid_colors") or []}][:8]
                        if not cols:
                            row.update(status="n/a", detail="no work/brand.json colours to compare")
                        else:
                            found = [h for h in cols if _share_near(frame, h, 30) > 0.0005]
                            ok = len(found) >= max(1, min(2, len(cols)))
                            row.update(status="pass" if ok else "fail", detail=f"found {found or 'none'} of {cols}")
            elif c in ("motion.springs", "motion.blur"):
                code = "\n".join(p.read_text(errors="replace") for p in (pdir / "work").glob("*.py"))
                if not code:
                    row.update(status="fail", detail="no scene code in work/")
                elif c == "motion.springs":
                    ok = bool(re.search(r"\bSpring\b|templates\.|ease|Channel", code))
                    row.update(status="pass" if ok else "fail", detail="springs/eased channels found" if ok else "no Spring/ease usage found in work/*.py")
                else:
                    off = re.search(r"shutter_deg\s*=\s*0(\.0)?\b|max_blur_samples\s*=\s*1\b", code)
                    row.update(status="fail" if off else "pass", detail="motion blur disabled in scene code" if off else "temporal motion blur on (engine default)")
            elif c == "audio.edges_clean":
                if video is None:
                    row.update(status="fail", detail="no MP4 in outputs/")
                else:
                    from luma_engine.encode import decode_audio

                    x = decode_audio(str(video))
                    if x is None or not len(x):
                        row.update(status="n/a", detail="no audio track")
                    else:
                        n = int(0.003 * 48000)
                        edge = float(max(np.abs(x[:n]).max(), np.abs(x[-n:]).max()))
                        row.update(status="pass" if edge < 0.01 else "fail", detail=f"edge peak {20 * np.log10(edge + 1e-9):.1f} dBFS (< −40 needed)")
            elif c == "delivery.formats":
                outs = {p.suffix.lower() for p in (pdir / "outputs").glob("*") if p.is_file()}
                need = [".mp4"] + ([".mov"] if "prores" in (settings.get("formats") or []) else [])
                voiced = any((pdir / "audio").glob("*.words.json"))
                if settings.get("captions") and voiced:
                    need.append(".srt")
                miss = [n for n in need if n not in outs]
                row.update(status="fail" if miss else "pass", detail=("missing " + ", ".join(miss)) if miss else f"found {', '.join(need)}")
            elif c == "delivery.file_names":
                bad = [p.name for p in (pdir / "outputs").glob("*") if p.is_file() and not re.fullmatch(r"[A-Za-z0-9._-]+", p.name)]
                row.update(status="fail" if bad else "pass", detail=("rename " + ", ".join(bad)) if bad else "all names safe")
            elif c == "delivery.presented":
                with db.session() as s:
                    arts = list(s.scalars(select(db.Artifact).where(db.Artifact.run_id == run_id)))
                vid = any(a.type == "video" and (a.path or "").startswith("outputs/") for a in arts)
                fil = any(a.type in ("file", "image") and (a.path or "").startswith("outputs/") for a in arts)
                ok = vid and fil
                row.update(status="pass" if ok else "fail", detail="presented" if ok else
                           ("present the final video with present_video; " if not vid else "") + ("present deliverables with present_file" if not fil else ""))
            else:
                row.update(status="n/a", detail=f"unknown check {c}")
        except Exception as e:  # noqa: BLE001 — a broken check must not break the review
            row.update(status="fail", detail=f"check error: {type(e).__name__}: {e}"[:300])
        rows.append(row)
    return rows


# ------------------------------------------------------------------------------ QC → plan
QC_PHASE = "Fix QC failures"


def sync_qc_todos(run_id: str, project_id: str, report: dict, report_path: str) -> dict:
    """Failing QC checks become `blocked` todos (acceptance = that check passes); when a later
    report passes those checks, the items are closed with the report as evidence."""
    items = plan.todos(run_id)
    phase = next((t for t in items if t["title"] == QC_PHASE and not t["parent_id"]), None)
    existing = {t["title"]: t for t in items if phase and t["parent_id"] == phase["id"]}
    failed = [c for c in report.get("checks", []) if not c.get("pass") and c.get("severity", "error") != "warning"]
    created, closed = [], []
    for c in failed:
        title = f"QC: {c['name'].replace('_', ' ')}"
        if title in existing and existing[title]["status"] not in plan.CLOSED:
            continue
        if phase is None:
            phase = plan.add(run_id, project_id, {"title": QC_PHASE, "priority": "high",
                                                  "acceptance_criteria": "qc_report passes on the delivered MP4"}, None, "system")
        t = plan.add(run_id, project_id, {"title": title, "priority": "high", "status": "blocked",
                                          "detail": f"value {json.dumps(c.get('value'))[:300]}; {c.get('detail') or ''}",
                                          "acceptance_criteria": f"qc_report check '{c['name']}' passes (expected {c.get('expected')})"},
                     phase["id"], "system")
        created.append(t["id"])
    passed = {c["name"] for c in report.get("checks", []) if c.get("pass")}
    for title, t in existing.items():
        name = title[4:].replace(" ", "_")
        if name in passed and t["status"] not in plan.CLOSED:
            plan.update(run_id, t["id"], "done", note="resolved: the check passes now", evidence=[{"qc_report": report_path, "check": name, "pass": True}],
                        author="system")
            closed.append(t["id"])
    return {"created": created, "closed": closed}
