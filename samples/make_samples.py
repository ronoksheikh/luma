"""Generate the ORIGINAL sample brand used by the demo and tests.

"Veyra" is a fictional brand created for Luma Studio.  The symbol is a five-blade fan
opening from a single hinge (a rivet), with each blade filled by an objectBoundingBox
linear gradient — a good stress test for gradients, pivots and splitting.

Run:  python samples/make_samples.py   (writes veyra-symbol.svg and veyra-brand.pdf)
"""
from __future__ import annotations

import math
from pathlib import Path

HERE = Path(__file__).parent

EMBER = "#FF5A5F"
AMBER = "#FFB547"
INK = "#0B0F2A"
PAPER = "#F5F1EA"
RIVET = "#FFE0A3"

HINGE = (256.0, 404.0)
ANGLES = [-162.0, -126.0, -90.0, -54.0, -18.0]  # screen degrees, -90 = straight up
LENGTHS = [0.84, 0.95, 1.0, 0.95, 0.84]
R0, R1 = 44.0, 262.0
W0, W1 = 9.0, 34.0


def f(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".")


def blade_path(angle_deg: float, length: float) -> str:
    a = math.radians(angle_deg)
    ux, uy = math.cos(a), math.sin(a)  # along the blade
    vx, vy = -uy, ux  # perpendicular (clockwise-rotated)
    r1 = R0 + (R1 - R0) * length

    def P(r, w):
        return (HINGE[0] + ux * r + vx * w, HINGE[1] + uy * r + vy * w)

    p1 = P(R0, -W0)
    p2 = P(r1 - W1, -W1)
    p3 = P(r1 - W1, W1)
    p4 = P(R0, W0)
    return (
        f"M{f(p1[0])} {f(p1[1])}"
        f"L{f(p2[0])} {f(p2[1])}"
        f"A{f(W1)} {f(W1)} 0 0 1 {f(p3[0])} {f(p3[1])}"
        f"L{f(p4[0])} {f(p4[1])}"
        f"A{f(W0)} {f(W0)} 0 0 1 {f(p1[0])} {f(p1[1])}Z"
    )


def symbol_svg() -> str:
    blades = "\n".join(
        f'    <path id="blade-{i + 1}" class="blade" d="{blade_path(a, l)}"/>' for i, (a, l) in enumerate(zip(ANGLES, LENGTHS))
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 92 512 352" width="512" height="352">
  <title>Veyra symbol (fictional sample brand for Luma Studio)</title>
  <defs>
    <linearGradient id="veyra-blade" x1="0" y1="1" x2="1" y2="0">
      <stop offset="0" stop-color="{EMBER}"/>
      <stop offset="1" stop-color="{AMBER}"/>
    </linearGradient>
    <style>.blade {{ fill: url(#veyra-blade); }}</style>
  </defs>
  <g id="fan">
{blades}
    <circle id="rivet" cx="{f(HINGE[0])}" cy="{f(HINGE[1])}" r="17" fill="{RIVET}"/>
  </g>
</svg>
"""


def make_pdf(path: Path) -> None:
    import pymupdf as fitz

    doc = fitz.open()
    page = doc.new_page(width=842, height=595)  # A4 landscape

    def rgb(h):
        h = h.lstrip("#")
        return tuple(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))

    page.draw_rect(page.rect, color=None, fill=rgb(INK))
    inter = None
    for cand in ["/usr/local/share/fonts/inter/Inter-SemiBold.ttf", "/usr/share/fonts/truetype/inter/Inter-SemiBold.ttf"]:
        if Path(cand).exists():
            inter = cand
            break
    fontname = "helv"
    if inter:
        page.insert_font(fontname="InterSemiBold", fontfile=inter)
        fontname = "InterSemiBold"
    page.insert_text((48, 70), "Veyra — Brand Guidelines", fontsize=30, fontname=fontname, color=rgb(PAPER))
    page.insert_text((48, 98), "Fictional sample brand bundled with Luma Studio. Not a real company.", fontsize=11, fontname="helv", color=rgb(PAPER))
    swatches = [("Ember", EMBER, "Primary · gradient start"), ("Amber", AMBER, "Primary · gradient end"),
                ("Ink", INK, "Background"), ("Paper", PAPER, "Wordmark / text"), ("Rivet", RIVET, "Hinge accent")]
    x = 48
    for name, hexv, role in swatches:
        r = fitz.Rect(x, 140, x + 130, 250)
        page.draw_rect(r, color=rgb(PAPER) if hexv == INK else None, fill=rgb(hexv), width=0.8)
        page.insert_text((x, 272), name, fontsize=14, fontname=fontname, color=rgb(PAPER))
        page.insert_text((x, 290), hexv, fontsize=11, fontname="helv", color=rgb(PAPER))
        page.insert_text((x, 306), role, fontsize=9, fontname="helv", color=rgb(PAPER))
        x += 150
    lines = [
        "Typography: Inter SemiBold for the wordmark (tracking +40), Inter Regular for body copy.",
        "Blade gradient: Ember #FF5A5F → Amber #FFB547, bottom-left to top-right, per blade.",
        "Logo construction: five blades open from a single rivet (the hinge). Keep proportions exact.",
        "Lockup: symbol left, wordmark right, gap = 0.35 × symbol height, wordmark cap height = 0.42 × symbol height.",
        "Motion: blades unfold from the hinge with a gentle spring; the brand never flashes pure white for long.",
        "Do not: use pure red #FF0000, recolour the blades, rotate the symbol, or place it on busy imagery.",
    ]
    y = 350
    for ln in lines:
        page.insert_text((48, y), ln, fontsize=11, fontname="helv", color=rgb(PAPER))
        y += 22
    doc.save(str(path))


if __name__ == "__main__":
    (HERE / "veyra-symbol.svg").write_text(symbol_svg())
    make_pdf(HERE / "veyra-brand.pdf")
    print("wrote", HERE / "veyra-symbol.svg", HERE / "veyra-brand.pdf")
