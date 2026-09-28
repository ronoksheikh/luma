---
name: brand_discovery
description: Start of every brand video — extract the real colours, fonts, marks and rules from the uploaded kit before designing anything.
tags: [brand, discovery, color, font, logo, kickoff]
tools_used: [palette_from_image, inspect_asset, detect_fonts, extract_palette, memory_write]
---

## When to use
At the start of any job with brand assets (SVG/PNG logos, PDF guidelines, boards), and whenever a new asset arrives.
Nothing creative happens until the brand facts are in project memory.

## Steps
1. `inspect_asset` every upload: SVG structure (shape count, ids, viewBox), PDFs (pages, embedded fonts), images (size, alpha).
2. Colours: prefer **declared** colours (SVG fills / gradient stops, PDF text) over sampled ones. Run
   `palette_from_image` on boards and screenshots for shares and WCAG contrast; note gradients as start → end stops.
3. Fonts: `detect_fonts` — exact names from PDFs; OCR guesses are *guesses* (say so). Install with `install_font`
   (licence recorded) or fall back to a close open font and tell the user.
4. Marks: symbol vs wordmark (`split_symbol_wordmark`), pivot/symmetry (`inspect_asset` analysis), minimum clear space.
5. Rules: avoided colours (project settings), "do not" pages in the PDF (no rotation, no recolouring, no effects on
   the wordmark…).
6. `memory_write` each fact (palette, fonts, clear space, don'ts) so every later run sees them.

## Pitfalls
- A PNG sampled palette drifts a few codes from the real brand colour (anti-aliasing, JPEG, k-means in Lab):
  never ship a sampled hex when an SVG/PDF declares the exact one.
- Gradients sampled as a single "average" colour lose the brand — record both stops and the direction.
- SVG `<style>` classes and `<use>` references hide fills; read the parsed document, not the raw text.
- Brand guidelines often forbid effects (glow, bevel) on the wordmark — check before planning a "chrome" look.

## Verification
- Every colour used in the scene matches a memory entry exactly (self_review brand_fidelity checks the final frame).
- The avoided colours do not appear in the final frame (ΔE check).
- The user confirmed any guessed font.

## Example
Veyra kit: SVG declares `#FF5A5F → #FFB547` (blades, linear, bottom-left → top-right) and rivet `#FFE0A3`;
background from the board `#0B0F2A`; wordmark font from the PDF: Inter SemiBold, tracking 40. Memory:
`palette = "#FF5A5F → #FFB547 gradient on #0B0F2A; wordmark #FFE0A3"`.
