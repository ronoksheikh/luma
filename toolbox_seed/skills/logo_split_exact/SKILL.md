---
name: logo_split_exact
description: Splitting a logo into animation parts (fan blades, wedges, pieces to assemble) without losing a single pixel of area.
tags: [svg, geometry, logo, split, assembly]
tools_used: [svg_split_exact, inspect_asset]
---

## When to use
Unfold / assemble / explode animations where the mark must be built from parts that recombine into the exact logo.

## Steps
1. `inspect_asset` the SVG: if it already has one shape per part (blades with ids), use those — no cutting needed.
2. Single-shape marks: find the hinge with `svg_split_exact` (pivot = least-squares intersection of principal axes;
   notches = reflex vertices, strongest first). Look at `preview.png`.
3. Choose cuts along the mark's own geometry: radial rays through the notches for fans/petals; straight lines through
   notch pairs for assemblies. Run `svg_split_exact` and require `area_error < 1e-9`.
4. Animate the parts (`part_NN.svg`, centroid + angle around the pivot) — and from the settle time on, draw the
   ORIGINAL SVG paths, not the parts.

## Pitfalls
- Drawing the reassembled parts in the final frame shows hairline **seams** (antialiasing of shared edges): switch to
  the original paths once settled (templates do this with `LogoKit.draw_reference`).
- Cuts that only touch the shape (tangent rays) create slivers or zero-area parts; extend cuts beyond the bounds.
- Flattening tolerance: parts are polylines at 0.01 units — fine for motion, not for the hold.
- A pivot from a single part is unreliable; use all converging parts or give it explicitly.

## Verification
- `area_error < 1e-9` (relative) reported by the tool.
- The final-frame QC matches the reference end card (`final_frame_exactness`, MAD ≤ 2/255).

## Example
`svg_split_exact({"svg": "assets/veyra-symbol.svg", "n_parts": 5})` → pivot [256, 404] (the rivet), 5 wedges,
area error 3e-16; the fan_unfold template swings them open on staggered springs and seats them.
