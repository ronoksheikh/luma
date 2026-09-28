---
name: motion_blur_and_hdr_compositing
description: Custom scene code with light, glow and fast motion — avoid the skia/HDR pitfalls that clamp highlights, harden edges or fake motion blur.
tags: [compositing, hdr, motion-blur, skia, light, engine]
tools_used: [render_preview, extract_frames]
---

## When to use
Writing custom fx or scene code that reads skia surfaces, adds light, or moves things fast.

## Steps
1. Solids (brand shapes, text) go in `frame.draw()` — composited in sRGB like Illustrator/Figma.
2. Light goes in `frame.light_canvas()` / `frame.add_light()` — linear, HDR (values > 1 allowed), bloomed and rolled off.
3. Read surfaces only with `layers.read_surface()` (kRGBA_F32 + kPremul).
4. Let the engine do motion blur: `Scene.render` averages sub-frames over a 180° shutter in linear light
   (adaptive: 2 samples when static, up to `max_blur_samples`).
5. Preview at `scale 0.5`, inspect the fastest frames (`extract_frames` around the peak velocity).

## Pitfalls
- skia-python `Image.toarray()` returns **unpremultiplied BGRA** by default: antialiased edges harden and light
  intensities break. Pass `colorType=kRGBA_F32` and `alphaType=kPremul` (or use `read_surface`).
- skia gradient shaders quantise Color4f stops to 8 bits → HDR stops clamp at 1.0. Use paint gain on light surfaces.
- `kPlus` blending also clamps at 1.0: light surfaces store `light / LIGHT_GAIN` (32).
- Paint colours are unpremultiplied: coverage paint is `Color4f(1,1,1,w)`, not `(w,w,w,w)`.
- Averaging blur samples in sRGB darkens moving highlights — the engine averages in linear light; don't re-blur in sRGB.
- Tone mapping would shift brand colours: `rolloff` is the identity on [0,1].

## Verification
- Static frames are bit-exact with and without light layers switched off (`test_frame_without_light_is_bit_exact`).
- Moving frames show smooth streaks (not stepped copies) — check the contact sheet at the peak velocity.
- Highlights > 1.0 bloom instead of clipping flat.

## Example
A "liquid chrome sweep": sheen band drawn on a light surface with gain, masked by the lockup coverage
(`lockup_mask`), moved 2400 px/s → engine motion blur uses 8 samples on those frames, 2 on the hold.
