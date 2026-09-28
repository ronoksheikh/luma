# palette_from_image

First step of brand discovery: what colours does the brand really use, how much of each, and which combinations are
legible?

**How:** `luma_engine.production.extract_palette` (k-means in CIE Lab over opaque pixels; SVG/PDF sources also report
their exactly declared colours), then WCAG 2.x relative luminance and contrast ratios for every colour against white
and black (`text_on_it` picks the better one) and for every pair (`aa_text` ≥ 4.5, `aa_large` ≥ 3). Writes `swatch.png`.

```json
{"path": "assets/brand-board.png", "k": 6}
```

**Limitations:** gradients spread over several clusters — prefer `declared` colours from the SVG/PDF when available;
anti-aliased edges create tiny in-between clusters (filtered by `min_share`).
