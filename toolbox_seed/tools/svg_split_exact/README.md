# svg_split_exact

Split a logo shape into animation parts — radially around its hinge/pivot, or along straight cuts — and **prove
nothing was lost**: the parts must sum to the original area within `tol` (default 1e-9, relative).

**Why:** assembling or unfolding animations need parts that recombine seamlessly. Hand-drawn cut lines lose slivers;
this uses shapely's exact polygon operations on the flattened SVG geometry (Wang's-formula flattening at 0.01 units).

**How:** `mode: radial` (default) cuts with rays from the pivot (detected with a least-squares intersection of the
parts' principal axes, or given) at `angles_deg` (or `n_parts` even rays). `mode: lines` cuts along `lines`.
Outputs `part_NN.svg` (same viewBox as the source) and `preview.png` (parts coloured, pivot marked).

```json
{"svg": "assets/logo.svg", "n_parts": 5}
{"svg": "assets/logo.svg", "shape": "blade-3", "mode": "lines", "lines": [[[0, 250], [512, 250]]]}
```

**Limitations:** fills only (strokes are not outlined); curves are flattened, so part SVGs are polylines — draw the
ORIGINAL paths for the final frame (the engine templates do this) to avoid seams.
