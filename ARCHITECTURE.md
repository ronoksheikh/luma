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
