# Brand fidelity

Automated items are checked by `self_review`; manual items need you to look (contact sheets, end card) and confirm with evidence.

- [auto:qc.final_frame_matches_reference] The final frame matches the reference lockup (mean abs diff within tolerance)
- [auto:brand.reference_used] QC was run with the end card / reference lockup as reference
- [auto:brand.avoid_colors] No colour the project avoids appears in the final frame
- [auto:brand.palette_present] The brand colours from work/brand.json appear in the final frame
- [manual] The logo is never recoloured, stretched, rotated or re-drawn; gradients use the brand's stops and units
- [manual] Lockup proportions and clear space follow the brand guidelines
- [manual] Typography uses the brand fonts (installed, not substituted)
