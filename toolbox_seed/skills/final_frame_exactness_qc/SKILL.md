---
name: final_frame_exactness_qc
description: Guaranteeing the film ends on the exact approved lockup — pixel-exact hold, no drift, no seams — and proving it before delivery.
tags: [qc, final-frame, lockup, exactness, delivery]
tools_used: [final_frame_exactness, qc_report, export_end_card]
---

## When to use
Every brand outro and any film whose last frame is a logo lockup (i.e. almost always).

## Steps
1. Export the reference end card from the scene's reference drawing (`export_end_card`) at the delivery size.
2. Make the hold exact: springs `at(..., snap_eps)` snap to the target; from the settle time on, draw the reference
   lockup verbatim (original SVG paths); set `Scene.static_after` so hold frames are identical.
3. Render, then `final_frame_exactness(video, reference)`: mean |Δ| ≤ 2/255.
4. Check the hold duration (≥ 0.45 s, templates guarantee it) and that no frame after the settle differs
   (qc_report `still_ending`).

## Pitfalls
- Dither on the hold: static TPDF dither keeps exact 8-bit values exact and the pattern constant, so still frames
  stay still — never use per-frame random dither.
- x264 `ultrafast` silently switches to Constrained Baseline; delivery uses `-profile:v high -preset slow -crf 12`.
- Reassembled split parts show seams in the final frame: draw the original paths.
- A tiny residual spring motion (1e-4 px) creates a 1-code shimmer in the hold: snap.

## Verification
- `final_frame_exactness` pass (typical H.264 4:2:0: mean ≈ 0.1–0.6).
- qc_report `final_frame_matches_reference` and `still_ending` pass.

## Example
Demo render: 300 frames at 60 fps, hold from frame 272 → last-frame MAD 0.10/255 vs end_card.png, max 9 on the
wordmark's antialiased red edges (chroma subsampling) → pass.
