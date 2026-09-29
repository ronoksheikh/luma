"""WCAG contrast check for the theme tokens in src/index.css (AA: 4.5 text, 3.0 UI/focus).  python3 frontend/scripts/contrast.py"""
import re, sys, pathlib
css = pathlib.Path(__file__).resolve().parent.parent.joinpath("src", "index.css").read_text()
def block(sel_re):
    m = re.search(sel_re + r"\s*\{(.*?)\n  \}", css, re.S)
    return dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})\s*;", m.group(1)))
def lum(h):
    c = [int(h[i:i+2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= .03928 else ((x + .055) / 1.055) ** 2.4 for x in c]
    return .2126 * c[0] + .7152 * c[1] + .0722 * c[2]
def cr(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + .05) / (lb + .05)
light = block(r"/\* light-tokens \*/\n  :root,\n  \.light,\n  \[data-theme=\"light\"\]")
dark = block(r"/\* dark-tokens \*/\n  :root\.dark,\n  \.dark,\n  \[data-theme=\"dark\"\]")
bad = 0
for name, t in (("light", light), ("dark", dark)):
    text_on = ["--background", "--surface", "--surface-secondary", "--surface-tertiary", "--sunken", "--overlay"]
    for fg in ["--foreground", "--muted", "--accent-ink", "--success-ink", "--danger-ink", "--warning-ink", "--link"]:
        for bg in text_on:
            r = cr(t[fg], t[bg]); ok = r >= 4.5
            bad += not ok
            if not ok or "-v" in sys.argv: print(f"{name:5} text  {fg:14} on {bg:20} {r:5.2f} {'ok' if ok else 'FAIL'}")
    r = cr(t["--accent-foreground"], t["--accent"]); bad += r < 4.5; print(f"{name:5} button accent-foreground on accent {r:5.2f} {'ok' if r>=4.5 else 'FAIL'}")
    for bg in ["--background", "--surface", "--surface-secondary"]:
        r = cr(t["--focus"], t[bg]); bad += r < 3; print(f"{name:5} focus ring on {bg:20} {r:5.2f} {'ok' if r>=3 else 'FAIL'}")
        r = cr(t["--field-border"], t["--field-background"]); 
print("FAILURES:", bad); sys.exit(1 if bad else 0)
