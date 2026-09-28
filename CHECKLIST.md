# Luma Studio — build checklist

Legend: `[x]` done **and verified by running it**, `[~]` done with a caveat noted, `[ ]` not done.

## Phase 1 — engine + demo
- [x] brand (colour tokens, sRGB↔linear, gradient LUTs, objectBoundingBox gradients)
- [x] svg (full path grammar, transforms, flatten, skia paths, split with area check, union, notch/pivot via exact area moments, symmetry)
- [x] text (HarfBuzz shaping → skia glyph paths, kerning, tracking)
- [x] layout (lockup builder, least-squares fit to reference, safe areas)
- [x] motion (easings, closed-form springs, Hermite channels, staggers, timeline/events)
- [x] camera (perspective camera, projection, plane homographies)
- [x] layers (HDR buffers, additive light, sRGB solids, adaptive motion-blur accumulator, DoF bins)
- [x] fx (bloom, shafts, roll-off, glow/pen tips, beams, write-on, ignite, shockwave, SDF wave, sheen, text rise, captions)
- [x] audio (synths, reverb, pans, placement, ducking, LUFS, true-peak limiter, 24-bit WAV, spectrogram)
- [x] encode (TPDF dither that keeps exact values, H.264/ProRes pipes, mux, SRT, ffprobe)
- [x] qc (frame count, fps, duration, black first frame, flicker, still ending, final-frame match, true peak, loudness, audio length, A/V sync)
- [x] templates: fan_unfold, exploded_assembly, stroke_reveal, voiced_explainer (each guarantees a ≥0.45 s exact hold at any duration)
- [x] CLI + demo renders a 300-frame, 5.000 s, 60 fps H.264 High MP4 with AAC audio; QC passes
- [x] engine unit tests pass (59)

## Phase 2 — backend + terminal + job manager
- [x] FastAPI app, SQLite models, settings, projects CRUD, assets (20 files, 25 MB, magic bytes, SVG sanitiser, analysis)
- [x] EventBus + SSE with persisted typed events and Last-Event-ID replay/resume
- [x] persistent PTY terminal per project + WebSocket mirror + take over
- [x] job manager (detached setsid processes, survive the tool call, progress, kill, concurrency)
- [x] secret handling (per-request headers, encrypted per-account storage, env fallback, redaction, never in the sandbox env)
- [x] **accounts**: username/password sign-up and sign-in (scrypt), HttpOnly session cookie, per-user projects/runs/terminal/keys, sign out, change password (signs out everywhere), `LUMA_ALLOW_SIGNUP`

## Phase 3 — agent loop + tools
- [x] streaming loop, parallel tool calls, malformed-JSON repair, retries, step/time limits, context compaction, follow-ups, stop — verified against a scripted OpenAI-compatible mock server
- [x] terminal/file/media tools, render_preview with vision, QC, ask_user, finish
- [x] director prompt (editable in Settings)
- [~] tested against real providers: **not run** (no OpenAI/OpenRouter key was available while building). The client uses the official `openai` SDK and only standard chat-completions streaming.

## Phase 4 — ElevenLabs tools
- [x] el_* tools via the official `elevenlabs` SDK, character budget, content-hash cache, sidecars, capability test — verified against a mock server
- [~] real ElevenLabs API: **not run** (no key available; elevenlabs.io docs were blocked from the build sandbox, so the SDK source and its docstrings were used as the reference)

## Phase 5 — frontend
- [x] rebuilt with **HeroUI v3** components and **Phosphor** icons; minimal light theme from the Lumademy brand guidelines (no navy); Inter / Inter Display and Geist Mono
- [x] sign-in / create-account screen; data follows the account to a new browser (Playwright test)
- [x] sidebar (projects: create, rename, duplicate, delete; demo; account menu), director timeline (streamed markdown, thinking, tool rows with args/output/diff/images/audio/progress, questions, delivered card), composer with upload and stop
- [x] inspector tabs: Preview (frame stepping, loop, end-card poster), Assets (drop zone, analysis), Terminal (Night theme, take over), Media, Files, QC
- [x] settings modal (connections with "save to my account", director prompt, limits, account/password) and brief & output modal
- [x] mobile layout (Director/Studio switcher, projects drawer, no horizontal scroll)
- [~] logo: `frontend/public/brand/logo-mark.svg` and `favicon.svg` are **stand-ins** in the brand icon gradient; only the guidelines text was provided, not the logo files

## Phase 6 — Docker hardening
- [x] multi-stage Dockerfile (pinned digests + lock file), compose (127.0.0.1 bind, CPU/memory/pids limits, healthcheck, cap_drop ALL + minimal caps), sandbox user `luma`, backend user `studio`, iptables network toggle
- [~] the image was verified on an Ubuntu 24.04 base (`tests/e2e/ubuntu-base.Dockerfile`) because the Debian mirrors were blocked from the build sandbox; the default `python:3.11-slim-bookworm` base was not built end-to-end here

## Phase 7 — tests + docs
- [x] backend tests (40, incl. auth and per-user isolation), engine tests (59)
- [x] e2e test against the container (health, login required, demo MP4 via ffprobe, QC, sandbox isolation, terminal WebSocket)
- [x] Playwright UI smoke test (sign-up, demo, upload, terminal, settings, new-browser sign-in, mobile)
- [x] README, ARCHITECTURE, ENGINE_NOTES, LICENSE (MIT)

---

# Phase 2 — long-horizon planning, presentation & power tools

## P2.1 — todos, memory, checkpoints, resume, compaction
- [x] Alembic migrations (`backend/app/migrations`: 0001 baseline, 0002 long jobs); pre-Alembic databases are stamped and upgraded in place (test with a 1.0-era DB)
- [x] `todos` + `plan_revisions`; `todo_write` / `todo_update` / `todo_add` / `todo_list`; nesting, priorities, acceptance criteria, evidence
- [x] rules enforced in code: one in-progress item, `done` needs evidence (paths/artifact ids/JSON) or a justification, `skipped` needs a reason, re-planning needs a reason and keeps a revision, phases derive status
- [x] plan-first rule: after `plan_required_after_steps` (default 5) model steps without a plan only planning/read-only tools run
- [x] plan pinned in the system prompt (survives compaction); user edits (add/edit/skip/reorder via REST) reach the agent as a system note
- [x] project memory (`memory_write/read/search`, REST CRUD) pinned into every run of the project
- [x] scratchpad (`notes_append/read`), tail pinned into context
- [x] checkpoints with git outside the workspace (`/data/git/<project>`), state.json (brief, settings, plan); auto after renders; restore makes a safety checkpoint first; REST + tools
- [x] resume: dangling tool calls closed, live jobs re-attached, lost renders re-queued (resume skips finished frames), stale requests cancelled, a system note explains it — tested with a simulated restart mid-render
- [x] `context_compact` + automatic compaction at 75 %; structured digest; pinned plan/memory/settings/brand/events/notes/user requests never dropped (test)

## P2.2 — presentation & artifacts
- [x] `artifacts` table with `version_group` / `version` (re-presenting creates v2, v3…), favourite, rename, delete, zip of all (or favourites) — REST + tests
- [x] `present_video` (probe, chapters validated against the duration, poster frame, loop), `present_image` (single/grid/carousel), `present_audio` (waveform peaks, word timings for highlighting, LUFS/TP), `present_file` (text/JSON/SRT/code/zip/PDF/media previews), `present_comparison` (artifact ids or paths, video/image, slider/side-by-side/toggle), `present_storyboard` (stills rendered from the scene in the sandbox, or given frames), `present_timeline` (events.json → picture/audio tracks, linked video), `present_code` (language + highlighted lines), `present_table`, `present_palette` (contrast ratios) — each emits a typed `present` event + an `artifact` event
- [x] engine `luma_engine.production` + CLI (`probe frames gif thumb stills endcard reframe-check vectorize palette fonts mix captions peaks`), `--set key=value` scene overrides
- [x] per-aspect lockup rules in `layout.build_lockup` (auto horizontal/vertical, fit to title-safe) so templates re-lay out for 9:16 / 1:1 / 4:5 instead of cropping (engine tests still pass)
