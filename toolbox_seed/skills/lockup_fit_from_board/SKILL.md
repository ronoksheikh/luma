---
name: lockup_fit_from_board
description: Reproducing the designer's exact lockup placement (size, position, gap) measured from a brand board or reference PNG.
tags: [layout, lockup, brand, measurement, least-squares]
tools_used: [lockup_fit_reference, board_residual_extract]
---

## When to use
The brief includes a board / end-card image and the video must end on exactly that composition.

## Steps
1. Crop the board to the lockup area (or mask other elements) so only the mark differs from the background.
2. `lockup_fit_reference(reference, svg)` → `s, tx, ty` and `placement` (fractions of the frame). Check `overlay.png`.
3. Repeat for the wordmark (render it to an SVG/alpha) or measure the gap from the fitted symbol box.
4. Convert to scene units: `x = placement.center_x * width` etc.; set the lockup explicitly instead of
   `build_lockup` defaults; re-export the end card and compare.

## Pitfalls
- Moments initialisation assumes ONE object: other elements on the board (taglines, faint lines) bias the scale —
  subtract the background first (`board_residual_extract`) or mask.
- JPEG boards: a soft alpha from colour distance; raise the contrast threshold if the fit "grows".
- The fit has no rotation — a rotated mark needs a manual angle first.
- rms > 0.1 means the fit is wrong; don't trust the numbers.

## Verification
- rms < 0.05 and the overlay shows no red fringe.
- Final frame vs the board (same size): `final_frame_exactness` mean |Δ| ≤ 2.

## Example
Board 1920×1080 → `s=0.842, tx=731.4, ty=402.9`, `placement.center_x=0.500, center_y=0.470, height=0.276` →
the symbol sits slightly above centre (optical centring) at 27.6 % of the frame height.
