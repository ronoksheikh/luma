# Luma Studio — build checklist

Legend: `[x]` done **and verified by running it**, `[~]` partially done / caveat noted, `[ ]` not done.

## Phase 1 — engine + demo
- [ ] brand (colour tokens, sRGB↔linear, gradient LUTs, objectBoundingBox gradients)
- [ ] svg (full path grammar, transforms, flatten, skia paths, split w/ area check, union, notch/pivot/symmetry)
- [ ] text (harfbuzz shaping → skia glyph paths, kerning, tracking)
- [ ] layout (lockup builder, least-squares fit to reference, safe areas)
- [ ] motion (easings, closed-form springs, Hermite channels, staggers, timeline/events)
- [ ] camera (perspective camera, projection, plane homographies)
- [ ] layers (HDR buffers, additive light, sRGB solids, motion-blur accumulator, DoF bins)
- [ ] fx (bloom, shafts, roll-off, glow/pen tips, beams, write-on, ignite, shockwave flood, SDF wave, sheen, text rise, captions)
- [ ] audio (synths, reverb, pans, placement, ducking, LUFS, true-peak limiter, 24-bit WAV, spectrogram)
- [ ] encode (TPDF dither w/ exact black, H.264/ProRes pipes, mux, SRT, ffprobe)
- [ ] qc (all qc_report checks)
- [ ] templates: fan_unfold, exploded_assembly, stroke_reveal, voiced_explainer
- [ ] CLI + demo renders 300-frame 5.000 s 60 fps MP4 with audio
- [ ] engine unit tests pass

## Phase 2 — backend + terminal + job manager
- [ ] FastAPI app, SQLite models, settings, projects CRUD, assets (limits, magic bytes, SVG sanitizer, analysis)
- [ ] EventBus + SSE with replay/resume
- [ ] persistent PTY terminal + WebSocket + take over
- [ ] job manager (detached, survives tool call, kill, concurrency)
- [ ] secret handling (headers, encrypted remember, env prefill, redaction)

## Phase 3 — agent loop + tools
- [ ] streaming loop, parallel tool calls, malformed JSON, retries, limits, context mgmt, follow-ups, stop
- [ ] terminal/file/media tools, render_preview w/ vision, QC, ask_user, finish
- [ ] director prompt

## Phase 4 — ElevenLabs tools
- [ ] el_* tools via official SDK, budget, cache, sidecars, capability test

## Phase 5 — frontend
- [ ] setup screen, settings, 3-pane studio, run timeline, terminal, preview/gallery/audio/outputs/QC tabs, demo

## Phase 6 — Docker hardening
- [ ] multi-stage Dockerfile, compose (localhost bind, limits, healthcheck, cap_drop), sandbox user, network toggle

## Phase 7 — tests + docs
- [ ] backend tests, e2e docker test, README, ENGINE_NOTES, LICENSE
