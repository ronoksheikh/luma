# lockup_fit_reference

Measure a lockup from a brand board: where exactly does the designer place the mark, and at what size?

**How:** the reference's coverage (alpha, or distance from the background colour for opaque boards) is compared with
the SVG rendered under a similarity transform `(s, tx, ty)`. Initialisation from image moments (area → scale,
centroid → translation), then `scipy.optimize.least_squares` on the pixel residual
(`luma_engine.layout.fit_to_reference`). Works at `max_side` px and reports in the reference's pixels.

Returns `s, tx, ty` (SVG user units → reference pixels: `x_px = (x - viewBox.x) * s + tx`), `rms` of the coverage
residual, `placement` as fractions of the frame (x, y, width, height, center) and `overlay.png`
(reference grey, fit in red).

```json
{"reference": "assets/brand-board.png", "svg": "assets/symbol.svg"}
```

**Limitations:** only the mark should differ from the background in the reference — crop the board to the lockup
first (or mask other elements); rotation is not fitted; rms > 0.1 means the fit is not trustworthy.
