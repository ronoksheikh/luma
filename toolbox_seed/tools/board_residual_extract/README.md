# board_residual_extract

Brand boards often hide a subtle background gradient and faint decorative lines (a few /255 above the background)
that you cannot see but must reproduce. This reconstructs the gradient and pulls those elements out.

**How:**
1. Fit a low-order polynomial surface (`degree`, default 2) per colour channel by least squares on a subsampled grid,
   iteratively trimming outliers (anything > 3σ from the fit), so logos and lines do not bias the gradient.
2. Subtract it: the residual is ~noise except where hidden elements are.
3. Threshold at `threshold_sigma` robust sigmas, open the mask, and find line segments with a probabilistic Hough
   transform (duplicates merged by angle and offset). Each line reports its average colour delta (BGR).

Outputs: `background_model.png`, `residual_x8.png` (residual × 8 around mid-grey — look at it!), `elements_mask.png`;
`corners` gives the model colour at the four corners (use them to rebuild the gradient in the scene).

```json
{"image": "assets/brand-board.png", "degree": 2, "exclude_mask": "work/logo_mask.png"}
```

**Limitations:** JPEG boards: blocking artefacts show up in the residual — raise `threshold_sigma`. Radial gradients
need `degree` ≥ 2; vignettes ≥ 3. Mask out the logo/wordmark for the cleanest gradient.
