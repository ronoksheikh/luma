# Luma Studio

A self-hosted AI motion-graphics and video studio. You upload a logo and brand files,
describe the film, and a director agent plans it, writes the animation, renders
previews, looks at its own frames, mixes the sound, encodes the deliverables and runs
quality checks. You watch every step as it happens: its messages, each tool call, the
terminal, the frames and the audio.

- **One command:** `docker compose up --build`, then open <http://localhost:8080>
- **Any OpenAI-compatible model** (OpenAI, OpenRouter, a local server) through the official `openai` SDK
- **Optional ElevenLabs** for voice-over with word timings, voice design, sound effects and music
- **Accounts:** simple username and password. Projects, renders and saved keys belong to
  your account and are there from any browser you sign in from
- **Bundled engine** (`luma_engine`): skia + numpy + FFmpeg, linear-light compositing,
  closed-form springs, motion blur, a sound-design synth, loudness-normalised mixing and
  a QC report. It renders a 1080p60 film with no API keys at all (the demo)

| Sign in | Director and delivered film |
| --- | --- |
| ![Sign in](docs/screenshots/sign_in.png) | ![Studio with a delivered film](docs/screenshots/studio_delivered.png) |
| **Delivered film and QC** | **Sandbox terminal** |
| ![QC report](docs/screenshots/qc.png) | ![Terminal](docs/screenshots/terminal.png) |

## Quick start

```bash
git clone <this repo> luma && cd luma
docker compose up --build        # first build takes a few minutes
open http://localhost:8080       # or just visit it in a browser
```

1. **Create an account.** The first person to open the page creates the first account
   (username 3–32 characters, password at least 8). Anyone else who can reach the port can
   also sign up until you turn sign-up off (see [Security](#security)).
2. **Run the demo** (sidebar). It needs no keys: it renders a 5-second, 300-frame 1080p60
   logo film with sound from the bundled sample mark, then checks it. It takes one to three
   minutes depending on your CPU.
3. **Connect a model** in Settings → Connections (`Ctrl`/`⌘` + `,`), then start a project,
   add your brand files and describe what you want.

Everything lives in `./data` on the host (SQLite database, encrypted saved keys, project
workspaces). Stopping or rebuilding the container keeps it.

## How the director works

Each project has a workspace (`assets/`, `work/`, `renders/`, `audio/`, `outputs/`) and a
persistent bash shell running as an unprivileged sandbox user. When you send a message:

1. The backend streams a chat completion with tools from your model. Text, reasoning,
   tool-call arguments and tool output are streamed to the page as typed events (Server-Sent
   Events, persisted in SQLite, so a reload replays the run and continues live).
2. Tools the model can call:
   - `terminal_run` / `terminal_spawn` / `terminal_poll` / `terminal_kill` / `terminal_send_keys`: the shell you see in the Terminal tab
   - `list_files`, `read_file`, `write_file`, `edit_file` (edits show as a diff)
   - `inspect_asset`, `render_preview` (contact sheet the model can see), `view_image`
   - `render_final` (a detached job with live progress), `audio_analyze`, `encode`, `qc_report`
   - `ask_user` (the run pauses and shows you options), `finish`
   - with ElevenLabs connected: `el_tts`, `el_design_voice`, `el_save_designed_voice`,
     `el_sound_effect`, `el_music`, `el_speech_to_text`, `el_list_voices`, `el_list_models`
3. Scenes are Python files that import `luma_engine`; four templates (`fan_unfold`,
   `exploded_assembly`, `stroke_reveal`, `voiced_explainer`) give the model a fast,
   well-tested starting point. The system prompt (Settings → Director) sets the workflow
   and quality bar and can be edited.
4. You can type guidance while it works (it's read before the next step), take over the
   terminal, or stop the run, which cancels the model stream, pending requests, the running
   command and render jobs.

See [ARCHITECTURE.md](ARCHITECTURE.md) and [ENGINE_NOTES.md](ENGINE_NOTES.md) for the
internals and the rendering pitfalls the engine handles.

## Connecting a model

Settings → Connections → Language model. Pick a preset or enter any OpenAI-compatible base URL:

| Provider | Base URL | Notes |
| --- | --- | --- |
| OpenRouter | `https://openrouter.ai/api/v1` | One key for many models. "Load models" lists them and marks which support tools and vision. |
| OpenAI | `https://api.openai.com/v1` | |
| Local (Ollama, LM Studio, vLLM…) | e.g. `http://host.docker.internal:11434/v1` | Needs a model with reliable streaming tool calls. |

**Test & save** checks authentication, streaming, tool calling and (optionally) vision.
The model must support tool calling; vision lets it look at its own preview frames and is
strongly recommended.

**Where keys go.** With *Save to my account* on (the default) the key is encrypted on the
server (Fernet, key file in `data/secrets/`) and follows your account to any browser. Turn
it off to keep the key only in this browser's localStorage; it is then sent with each
request. Keys are never written to logs or run events, and never enter the agent's
terminal environment: the model reaches ElevenLabs only through the backend tools, and
known key patterns are redacted from terminal output. You can also set `LLM_API_KEY`,
`LLM_BASE_URL`, `LLM_MODEL` and `ELEVENLABS_API_KEY` in the environment as a server-wide
fallback (shared by every account).

## ElevenLabs (optional)

Settings → Connections → ElevenLabs. The test shows your plan's character usage and which
features your key can use (text to speech, voice design, sound effects, music, speech to
text). Generated audio is cached by content hash, so re-running with the same text costs
nothing, and a per-run character budget (Settings → Limits, default 5,000) is checked before
every request.

## Costs

- **Model tokens:** a typical film takes 20–80 tool steps. Token use is shown live under
  the project title. Previews are sent to the model as images, which is the main cost with
  vision models. Pick a cheaper model for iteration and set *Max tool steps* and the time
  limit in Settings → Limits.
- **ElevenLabs:** billed per character for speech and per generation for sound effects and
  music. The run's character count is shown next to the token count.
- **Rendering** is local CPU time. A 5 s 1080p60 film renders in 1–3 minutes on a
  4-core machine; the compose file limits the container to 4 CPUs and 8 GB (`LUMA_CPUS`,
  `LUMA_MEMORY`).

## Security

- **Every account gets a shell** inside the container (as the unprivileged `luma` user,
  with passwordless `sudo` *inside* the container so installs work) and can spend the API
  credits saved to it. Treat accounts like SSH access to the container.
- The port is bound to **127.0.0.1** by default. The first visitor creates the first
  account, so sign up before exposing it anywhere.
- To expose it: put it behind TLS (a reverse proxy such as Caddy or nginx), set
  `LUMA_ALLOWED_HOSTS=studio.example.com`, and set `LUMA_ALLOW_SIGNUP=false` once your
  accounts exist. The session cookie is `HttpOnly`, `SameSite=Lax`, and `Secure` behind HTTPS.
- **The container is the sandbox.** It is not privileged, has no Docker socket, only
  `./data` is mounted, capabilities are dropped to what the sandbox needs, and CPU, memory
  and process counts are limited. The agent's shell cannot read the database or the key
  store, cannot reach Luma's own API port, and has network access only while *Terminal
  network access* is on (Settings → Limits; enforced with iptables inside the container).
- Other hardening: host allow-list (DNS rebinding), a custom header required on every
  state-changing request (CSRF), Origin checks on the terminal WebSocket, upload size and
  magic-byte validation, SVG sanitising, a strict Content-Security-Policy, scrypt password
  hashes, sessions stored hashed, and a delay on failed logins.
- Changing your password (Settings → Account) signs out all your sessions.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `LUMA_ALLOW_SIGNUP` | `true` | Allow new accounts from the sign-in page. |
| `LUMA_ALLOWED_HOSTS` | *(localhost)* | Extra host names the app answers to (comma-separated). |
| `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` | | Server-wide model fallback. |
| `ELEVENLABS_API_KEY` | | Server-wide ElevenLabs fallback. |
| `LUMA_JOB_CONCURRENCY` | `1` | Render jobs that may run at once. |
| `LUMA_SANDBOX_SUDO` | `1` | Passwordless `sudo` for the sandbox user inside the container. |
| `LUMA_CPUS`, `LUMA_MEMORY` | `4`, `8g` | Container limits (compose). |

## Your logo

The UI uses `frontend/public/brand/logo-mark.svg` (sidebar, sign-in) and
`frontend/public/favicon.svg`. The files in the repo are a stand-in drawn in the brand's
icon gradient; replace both with the real symbol-only SVG from your brand kit and rebuild.
Colours follow the Lumademy guidelines (Lumademy Blue `#2970EC`, Sky `#5DAEFF`, Royal
`#1557D1`, Deep `#07358F`, Night `#071738` for dark surfaces such as the terminal and
player, Off White `#EFF5FF`) and are defined once in `frontend/src/index.css`.

## Development

```bash
# engine + backend (Python 3.11)
python -m venv .venv && . .venv/bin/activate
pip install -r docker/requirements.lock -e engine -e backend
pytest                                   # engine + backend tests (e2e excluded)

# frontend
cd frontend && npm ci && npm run build   # typecheck + production build into frontend/dist
npm run dev                              # Vite dev server (set LUMA_DEV_CORS=1 on the backend)

# run the backend against a local data dir
cd backend && LUMA_DATA_DIR=../data LUMA_PORT=8080 python -m app.main

# end-to-end, against a running container
docker compose up -d --build
LUMA_E2E_URL=http://127.0.0.1:8080 pytest -m e2e tests/e2e -v
```

## Troubleshooting

- **The page says it can't reach the server / the container is unhealthy:**
  `docker compose logs -f luma`. `http://localhost:8080/healthz` reports the database and FFmpeg.
- **"Tool calling" fails in Test & save:** the model or provider doesn't support streamed
  tool calls. Try another model; on OpenRouter use one marked *tools*.
- **Installs in the terminal fail:** turn on Settings → Limits → Terminal network access.
- **Renders are slow:** raise `LUMA_CPUS`, keep previews small, or lower the frame rate
  while iterating.
- **Forgot your password:** there is no email reset. Another way in is to delete the user
  row from `data/studio.db` (their projects remain on disk under `data/projects/`).
- **Building behind a corporate proxy or TLS-inspecting firewall:** pass the proxy as build
  args and your CA with `--secret id=extra_ca,src=/path/ca.crt` (see `docker/Dockerfile`).

## License

MIT. See [LICENSE](LICENSE).
