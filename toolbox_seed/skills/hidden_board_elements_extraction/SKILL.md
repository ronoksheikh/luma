---
name: hidden_board_elements_extraction
description: Finding what the eye misses on a brand board — the subtle background gradient and faint decorative lines — by fitting and subtracting the background.
tags: [analysis, board, gradient, residual, lines]
tools_used: [board_residual_extract, palette_from_image]
---

## When to use
The board "looks flat" but the brief says to match it exactly, or the end card must reproduce the board's background.

## Steps
1. Run `board_residual_extract(image, degree=2)`; mask the logo/wordmark with `exclude_mask` for a clean fit.
2. Open `residual_x8.png`: gradients show as smooth ramps if the degree is too low; hidden rules/lines as crisp strokes.
3. Raise `degree` (3 for vignettes) until the residual is noise everywhere except the elements.
4. Rebuild: the gradient from `corners` (or the coefficients) in the scene background; each line from `lines`
   (p0, p1, angle, colour delta) as a thin stroke at that delta over the background.

## Pitfalls
- A 2×2 morphological opening erases 1-px lines — the tool removes small connected components instead.
- Fitting on all pixels lets the logo pull the gradient; the fit trims > 3σ outliers iteratively, but masks are better.
- JPEG block artefacts appear as a grid in the residual: raise `threshold_sigma` to 5–6.
- Colour deltas of +3..+6 codes are typical — render them in sRGB, not linear light, or they vanish.

## Verification
- Residual sigma ≈ the image noise (< 2 codes) after the fit.
- Re-render the rebuilt background, run the tool on it: the same lines at the same positions.

## Example
A 1920×1080 board: degree 2, corners `#0B0F2A`→`#131A3F`, two lines found: a vertical rule at x=412 (+4 codes) and a
27° diagonal (+5 codes) — both reproduced with 1-px strokes.
