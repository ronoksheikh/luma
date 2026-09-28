# luma_engine — engineering notes

`luma_engine` is the rendering framework the director agent writes scene code against.
Every pitfall below is **handled in the engine**, **covered by a unit test** (see
`tests/engine/`) and **repeated in the director prompt** (`prompts/director.md`).

## Mental model

```
scene.draw(frame, t)                                  # pure function of time
  ├─ frame.draw()          → skia F16 surface, solid layers composited in sRGB
  ├─ frame.light_canvas()  → skia F16 surface, additive light (gain-scaled, linear)
  └─ frame.add_light(...)  → numpy float32 HDR light (linear)
frame.resolve_linear()  = srgb_to_linear(base) + light + bloom(light) → roll-off
Scene.render(t)         = motion_blur(render_linear, t) → linear_to_srgb
render_frames()         → 16-bit PNGs (resumable) → encode (static TPDF dither) → MP4
```

Coordinates are **design pixels** (the scene's nominal size). Previews render at
`scale < 1`; every fx helper applies `frame.scale` itself.

## Pitfalls (and what the engine does about them)

| Pitfall | Handling | Test |
|---|---|---|
| skia-python `Image.toarray()` returns **unpremultiplied BGRA** by default. Reading layers that way hardens antialiased edges and breaks light intensities. | `layers.read_surface()` always requests `kRGBA_F32` + `kPremul`. Never call `toarray()` without both arguments. | `test_premultiplied_readback` |
| skia gradient shaders convert `Color4f` stops to **8-bit ints** → HDR (>1) clamps. | Gradients are used only for sRGB fills. HDR light is done with paint gain: light surfaces store `light / LIGHT_GAIN` (32) and are multiplied back on read; skia's `kPlus` also clamps at 1.0, which the gain avoids. `Gradient.render()` gives a float LUT when precision matters. | `test_hdr_light_survives_skia_plus_clamp`, `test_skia_shader_matches_float_gradient_obb_with_transform` |
| Paint colours are **unpremultiplied**: a coverage paint is `Color4f(1,1,1,w)`, not `(w,w,w,w)`. | `layers.coverage_paint(w)`. | `test_coverage_paint_is_unpremultiplied` |
| Light-vector sign conventions: an inverted key light blows faces to white. | `camera.to_light_vector(light, surface)` always points **from the surface to the light**; `camera.lambert(n, l)`; camera looks along +z so a camera-facing normal is (0,0,−1). | `test_light_vector_sign_convention` |
| Brand marks must match the designer's files. | Solids are composited in **sRGB** (untagged skia surfaces blend the encoded values like Illustrator/Figma); light maths is **linear**. Engine SVG renders match cairosvg to ≈0.05/255 mean. | `test_frame_without_light_is_bit_exact` |
| Tone-mapping would alter brand colours. | `layers.rolloff()` is the **identity on [0,1]**; only over-range pixels bleed towards white. | `test_rolloff_identity_in_unit_range_and_bounded` |
| Seam lines between reassembled pieces in the final frame. | Split pieces (`Part.is_piece`) are only used while animating; templates draw the **original SVG paths** from the settle time on (`LogoKit.draw_reference`). | `test_template_renders_and_ends_on_reference` |
| Final frame must be pixel-exact to the lockup. | Springs `at(..., snap_eps)` snap exactly to the target once settled; templates render the reference lockup verbatim during the hold and set `Scene.static_after` (hold frames are cached and identical). QC checks mean abs diff vs `end_card.png`. | same + demo QC (`final_frame_matches_reference` ≈ 0.10/255) |
| Motion blur must be real. | `layers.motion_blur()` averages sub-frame renders over a 180° shutter (nested trapezoid, adaptive: 2 samples when static → up to `max_blur_samples`). Averaging happens in linear light. | `test_motion_blur_static_is_single_sample_and_moving_averages` |
| 8-bit banding vs. exact black. | `encode.to_uint8()` applies **static** TPDF dither (same pattern every frame → still frames stay still) and skips values that are already exact 8-bit codes, so pure black stays 0, white 255 and flat brand colours stay exact. | `test_dither_keeps_exact_values_and_black` |
| x264 presets silently change the profile (`ultrafast` → Constrained Baseline). | Delivery uses `-profile:v high -preset slow -crf 12`; QC reports the profile. | `test_encode_probe_and_qc_pass` |
| Long renders outlive a tool call. | `python -m luma_engine render/pipeline` is resumable (skips existing frames, atomic `.tmp` rename) and prints JSON progress; Luma Studio runs it as a detached job. | backend job tests |
| Intersample peaks. | `audio.true_peak_db()` uses 4× polyphase oversampling; `true_peak_limit()` is a look-ahead limiter on the oversampled envelope; `master()` iterates LUFS-normalise + limit. | `test_true_peak_exceeds_sample_peak_for_intersample_overs`, `test_master_hits_lufs_and_true_peak` |
| AAC adds priming/padding. | QC measures decoded audio length (edit lists honoured) and allows ≤ 1 frame difference. | `test_encode_probe_and_qc_pass` |

## Module map

* `brand` — `Color`, `BrandKit` (avoid-list with ΔE), sRGB↔linear, `Gradient`
  (linear/radial/focal, `objectBoundingBox`/`userSpaceOnUse`, `gradientTransform`,
  spread modes, sRGB LUT, skia shader), k-means palettes.
* `svg` — full path grammar (M/L/H/V/C/S/Q/T/A/Z, relative, compact numbers, arc
  flags), transforms, CSS classes in `<style>`, `<use>`, gradients with `href`
  inheritance, flattening (Wang's formula), skia conversion, `split_polygon` /
  `split_radial` with area-error check (< 1e-9), skia PathOps union, notch / pivot
  (least-squares intersection of exact principal axes) / symmetry detection,
  symbol/wordmark separation, SVG sanitiser.
* `text` — font discovery (family + OS/2 weight), HarfBuzz shaping (kerning,
  ligatures, features), tracking in 1/1000 em, per-glyph skia paths.
* `layout` — `build_lockup` (cap-height-matched wordmark, gap, optical centring),
  `fit_to_reference` (moments init + least squares), safe areas per aspect ratio.
* `motion` — easings (incl. CSS `cubic-bezier`), closed-form `Spring` with
  first-crossing / peak / settle times, Hermite `Channel`, `stagger`, `Timeline` of
  events and spans (JSON, shared with audio & QC).
* `camera` — perspective camera (z = 0 maps 1:1), projection, plane homographies,
  layer matrices, Lambert helpers.
* `layers` — `Frame`, HDR light, roll-off, `motion_blur`, `dof_composite`.
* `fx` — bloom, light shafts, glow dots / pen tips, beams, `glow_path`, `trim_path`,
  `write_on`, `ignite`, `radial_flood`, `shockwave_ring`, `refract`, `sdf_wave`,
  `sheen`, `text_rise`, `kinetic_captions`, `caption_groups`.
* `audio` — oscillators (polyBLEP), FM bell, glass ping, noise colours, whoosh,
  click/clack/thump, sub boom, riser, shimmer, granular, reverb (convolution with a
  synthetic stereo IR, and a Freeverb-style algorithm), equal-power pans & pan paths,
  `Mix` (event placement with anchors, buses, sidechain ducking), LUFS, true peak,
  limiter, `master`, 24-bit WAV, onsets, spectrogram PNG with event markers.
* `encode` — dithering, `VideoWriter` (H.264 High / ProRes 422 HQ), `encode_frames`,
  `mux`, `burn_subtitles`, SRT writer/parser, `words_to_cues`, ffprobe helpers.
* `qc` — `qc_report` (see module docstring) and `image_stats` for non-vision models.
* `render` — `load_scene`, `contact_sheet`, `render_frames` (multi-process,
  resumable), `render_audio`, `render_pipeline`.
* `templates` — `fan_unfold`, `exploded_assembly`, `stroke_reveal`,
  `voiced_explainer` (all accept any SVG; see each module docstring).

## Performance notes

* 1920×1080: a single light-heavy render is ~0.2–0.8 s; with adaptive motion blur a
  frame costs ~0.5–3 s. `render_frames` uses one process per core (cv2 threads = 1).
* Hold frames are computed once (`static_after`).
* Bloom thresholds at half resolution; sRGB conversion uses OpenCV's SIMD `pow`.
* Use `--scale 0.5` previews and contact sheets while iterating.

## Fallbacks

* **skia-python wheels**: `skia-python==144.0.post2` publishes manylinux wheels for
  x86_64 and aarch64 (CPython 3.11). If a platform lacks a wheel, build from source
  (needs `ninja`, `clang`, ~1 h) or run the amd64 image under emulation. The engine
  needs libEGL/libGL/fontconfig at runtime even for raster-only use.
* **Fonts**: `text.find_font` falls back to `fc-match`, then any installed font. Put
  brand fonts in `/data/projects/<id>/assets` and pass the path as `font=`.
* **Known limitation**: onset detection (A/V sync QC) can report spurious onsets on
  noisy beds; the check only needs the *nearest* onset per event, so false positives
  can at worst mask a sync error that happens to land within ±2 frames of one.
