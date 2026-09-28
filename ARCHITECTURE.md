# Luma Studio — architecture

```
┌──────────────────────────── one container, one port (8080) ────────────────────────────┐
│                                                                                         │
│  React SPA (Vite + HeroUI v3 + Phosphor icons, served statically by FastAPI)            │
│    SSE  /api/runs/{id}/events  (typed, persisted, replayable, Last-Event-ID resume)     │
│    WS   /ws/terminal/{project}  (xterm.js mirror, optional "take over")                 │
│                                                                                         │
│  FastAPI backend  (user: studio — holds API keys in memory only)                        │
│   ├─ auth              (username/password, scrypt, HttpOnly session cookie, per-user    │
│   │                     projects; keys saved per account with Fernet, or per-browser)   │
│   ├─ projects/assets   (magic-byte validation, SVG sanitizer, automatic analysis)       │
│   ├─ EventBus          (SQLite events table, monotonically increasing ids, fan-out)     │
│   ├─ Agent loop        (openai SDK, any OpenAI-compatible base_url, streamed tool calls)│
│   │    tools: terminal_* · files · render/preview · audio · encode · qc · ask · finish  │
│   │           el_* (ElevenLabs via official SDK; budget + content-hash cache)           │
│   ├─ TerminalManager   (persistent PTY bash per project, runs as sandbox user `luma`,   │
│   │                     scrubbed env, OSC-marker command framing, redaction)            │
│   └─ JobManager        (detached setsid subprocesses tracked by PID, log tail,          │
│                         JSON progress lines, cancel, concurrency limit)                 │
│                                                                                         │
│  luma_engine  (pure Python package: skia + numpy + ffmpeg)                              │
│   brand · svg · text · layout · motion · camera · layers · fx · audio · encode · qc     │
│   templates/{fan_unfold, exploded_assembly, stroke_reveal, voiced_explainer}            │
│   CLI: python -m luma_engine {preview,render,audio,encode,qc,demo}                      │
│                                                                                         │
│  /data  (bind mount)  studio.db (users, sessions, projects, events) · secrets/          │
│                       projects/<id>/{assets,work,renders,audio,outputs}                 │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

## Key decisions

* **Rendering model.** Skia rasterises *coverage* (shapes, strokes, glyphs) into float
  surfaces; all light maths (additive beams, glows, bloom, shockwaves) happens in
  float32 numpy in **linear** light, while brand marks are composited in **sRGB** exactly
  like design tools. A highlight roll-off that is the identity on [0,1] guarantees that
  frames without light contributions are bit-exact to the reference lockup.
* **Scenes are pure functions of time.** `Scene.render(t) -> float32 sRGB HxWx3`. That
  makes preview contact sheets, multi-process final renders, resumable chunked renders
  and temporal motion blur (averaging sub-frame samples) trivial and deterministic.
* **One event list** (`Timeline`) drives both picture and sound; QC checks A/V sync
  against it.
* **The container is the sandbox.** The backend runs as `studio`; the agent's shell and
  jobs run as the unprivileged `luma` user with a scrubbed environment. Keys never enter
  the sandbox; ElevenLabs is reached through backend tools. Output is redacted as
  defence in depth.
* **Long work is detached.** Renders are `setsid` subprocesses tracked by PID; tool
  calls wait with a cap and otherwise return a job handle that can be polled/killed.
* **Accounts, not per-browser state.** Projects belong to a user; sign in from any
  browser and everything (projects, runs, renders, saved keys) is there. Sessions are
  random tokens stored hashed in SQLite; passwords are scrypt hashes.
* **Everything is an event.** Every UI update is a persisted, typed event, so reloading
  the page replays the full history and then continues live.

## Toolbox (self-extending tools)

```
agent loop ──(each step)──► registry = built-ins + ≤N toolbox tools (FTS5 relevance, usage, this run's)
   │                                              ▲
   │ tool call                                     │ toolbox_tools / skills / plugins (SQLite index + FTS5)
   ▼                                              │  ◄── sync ── /data/toolbox (git) + work/tools (git outside workspace)
backend/app/toolbox/execute.py ── sudo -u luma [-g lumanonet] env -i … /data/venv/bin/python -m luma_engine.toolkit
   ▲  stdin: {params, workspace, out_dir, limits}      │ stdout JSON lines: log · progress · image · call · result/error
   └──────── call → AgentLoop.invoke (gates, budgets) ◄┘
```

* `backend/app/toolbox/`: `manifest` (tool.yaml), `store` (index, FTS, stats, audit), `lifecycle` (create / test /
  register / update / rollback / promote / deprecate / delete), `scan` (ruff + security + promotion scans),
  `execute` (sandboxed runner + RPC), `venv` (persistent layered venv + lock), `skills`, `plugins` (plugins, templates,
  registry.json), `gitrepo`, `boot` (seeding, background registration).
* `luma_engine/toolkit.py` is the only code that runs inside a tool process (harness, `Ctx`, guards, `make_test_ctx`);
  `luma_engine/plugins` discovers enabled plugins through `registry.json` with a meta-path finder.
* Decisions: files are the source of truth and the DB is an index; a tool is callable only when the hash of its
  files equals the hash its tests passed on; the global toolbox is backend-written only (the agent reaches it through
  `tool_promote`, gated by a scan and the user's approval); network isolation is kernel-level via a firewalled group
  (same UID as the terminal) rather than a separate user.
