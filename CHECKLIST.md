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

## P2.3 — collaboration gates
- [x] generic persisted user requests (`user_requests`): `ask_user` (options/chips, free text, multi-select, timeout → default), `request_approval` (drafts attached, choices), `present_options` (labels, descriptions, preview artifacts); answered via REST buttons or a typed message; events `ask_user` / `approval_request` / `options_request` / `approval_result`
- [x] gates in code (unless project Autopilot): 4K or long (estimated) final renders, ElevenLabs requests over N characters, continuing past the run's cost / time thresholds — expensive tools are refused until an approval newer than the last user instruction exists
- [x] `notify` (notifications table, bell history REST, mark read) and `report_progress` (`progress_stage` events)
- [x] budget: tokens, cost from provider-reported cost or OpenRouter pricing (captured when models are listed), `budget` event every step, cost/token caps stop runs gracefully (tests)

## P2.4 — power tools
- [x] `media_probe`, `extract_frames` (+ contact sheet), `make_gif_or_webp` (palette GIF / animated WebP), `make_thumbnail`, `export_end_card` (rendered per size)
- [x] `reframe_export`: end-card check per aspect (content inside the safe area) then one render job per aspect; test renders 16:9 + 9:16 and ffprobes the sizes
- [x] `batch_render`: variants via `--set` overrides, queued jobs, presented as a `grid` artifact
- [x] `vectorize_raster` (vtracer; IoU 0.95 on the sample, always flagged AUTO-TRACED), `extract_palette`, `detect_fonts` (exact PDF names; OCR guesses flagged as guesses, tesseract in the image), `install_font` (Google Fonts repo or official URL, licence recorded; project fonts visible to the engine)
- [x] `audio_mix` (side-chain ducking, −14 LUFS, −1 dBTP), `captions_build` (SRT, WebVTT, kinetic-caption spec)
- [x] `render_queue_status` / `render_queue_cancel`, `budget_status`
- [x] `web_fetch` / `web_search` only when the project enables web access; SSRF guard; results labelled UNTRUSTED and shown as source cards
- [~] `install_font` by Google Fonts family and `web_search` against DuckDuckGo were tested against local stand-ins only (no internet in the test environment)

## P2.5 — self-review & quality gates
- [x] `prompts/checklists/{brand_fidelity,motion_quality,audio_quality,delivery}.md`; `self_review` measures the automated items (QC checks, avoided/brand colours in the final frame, springs/motion blur in scene code, clean audio edges, formats, file names, presented) and lists manual items; results presented as a table artifact
- [x] failing `qc_report` checks become `blocked` todos under "Fix QC failures" (acceptance = that check passes) and close themselves when a later report passes
- [x] `finish` gate: open todos, missing/failing QC, final video not presented with `present_video`, deliverables not presented → rejected with every reason
- [x] mocked-LLM end-to-end run: plan → storyboard → approval → render (1080×1080, 72 frames) → QC → present video (chapters) + file → self-review → finish (test)

## P2.6 — sub-agents
- [x] `spawn_subagent(task, tools_allowed, budget)`: child run (`parent_run_id`, `limits`) with its own context, a restricted toolset (never finish/ask/spawn), own step/cost/ElevenLabs budget, `subagent_report` result; `subagent_start` / `subagent_end` events; per-project concurrency cap; child cost added to the parent; cancelled with the parent (tests); off unless the project enables sub-agents

## P2.UI — interface
- [x] centre pane: sticky bar with plan progress (n / m + current item), run stage + percent + ETA, live budget meter (cost/tokens/steps, tooltip with caps and ElevenLabs characters)
- [x] inline cards for every `present_*` (video with chapters/speed/frame-step/download, image single/grid/carousel, waveform audio with word highlighting, file previews, comparison slider/side-by-side/toggle with synced videos, storyboard strip, timeline tracks tied to a player, highlighted code, table, palette, variant grid, font), `ask_user` (chips, multi-select, free text, timeout countdown), `request_approval` (drafts inline, Approve / Request changes, `A`), `present_options` (preview cards), sub-agents (collapsible nested timelines)
- [x] right pane: **Plan** (live tree, status icons, progress, elapsed per item, evidence, add/edit/reorder/block/skip/done, revisions), **Artifacts** (versions, compare any two, ⭐, rename, delete, download, download all/favourites), **Memory** (edit/delete/add), **Checkpoints** (timeline, create, restore with confirm); Preview/Terminal/Assets/Media/Files/QC kept (overflow menu)
- [x] project settings: Autopilot, approval thresholds (render minutes, ElevenLabs characters, cost, run time), web access, sub-agents + limits; global limits: plan-first steps, cost cap, token cap
- [x] notifications: bell with history (mark read), toasts for live `notify`, desktop notifications (permission asked once) for approvals, questions, finished/failed runs
- [x] shortcuts: Space/k play-pause, `,` `.` frame step, `A` approve, `Esc` stop (confirm), Ctrl/⌘K command palette (projects, artifacts, todos, demo, export all, settings), Ctrl/⌘, settings
- [x] Resume banner for interrupted runs

## P2 — tests, docs, definition of done
- [x] backend: 62 tests (plan rules, re-planning, user edits, plan-first rule, memory, compaction keeps pinned items, checkpoints create/restore/undo, resume after a simulated restart mid-render, Alembic upgrade of a 1.0 DB, presentation + versioning + REST, questions/approvals/options/timeouts/typed answers, gates + Autopilot, budget caps, power tools, SSRF guard, web tools, QC → blocked todos, self-review, finish gate, full mocked flow, sub-agents incl. cascade cancel)
- [x] engine: 59 tests still pass after the per-aspect layout change
- [x] Playwright (`pytest -m ui`, in-process server + scripted director): plan panel updates live, approval button and `A` key, presentation cards, artifacts compare slider, memory, checkpoints, bell, command palette, sub-agent cards, collaboration settings — and all README screenshots
- [x] README documents every new tool with screenshots; `prompts/director.md` has "Working on long jobs" + a one-example-per-tool cheat-sheet
- [~] **real OpenRouter run** (definition of done #3): **not run** — no API key is available in this environment. Everything the agent does was exercised with a scripted OpenAI-compatible server; token cost uses OpenRouter's `pricing` fields when the model list provides them.
- [x] e2e against the rebuilt container (9 tests): health, login required, demo 300 frames / 60 fps / AAC / QC, sandbox isolation, UI smoke, **`docker restart` mid-render → interrupted → Resume → render re-queued with finished frames kept → video with chapters presented → QC passes → finished** (definition of done #2)
- [~] the Docker image was verified on the Ubuntu 24.04 fallback base (Debian mirrors are blocked from this build environment); new system dependency: `tesseract-ocr`
