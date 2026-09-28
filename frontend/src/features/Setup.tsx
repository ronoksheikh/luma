import { Clapperboard, Mic2, Play, ShieldAlert, Sparkles, TerminalSquare } from "lucide-react";
import { useState } from "react";
import { Badge, Button } from "../components/ui";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import { ElevenLabsConnection, LlmConnection } from "./Connections";

export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden>
      <defs>
        <linearGradient id="lg" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stopColor="#FF6B5A" /><stop offset="1" stopColor="#FFC56B" /></linearGradient>
      </defs>
      <rect width="64" height="64" rx="14" fill="#141722" />
      <circle cx="32" cy="32" r="12" fill="url(#lg)" />
      <circle cx="32" cy="32" r="20" fill="none" stroke="url(#lg)" strokeOpacity=".45" strokeWidth="2" />
    </svg>
  );
}

export function SetupScreen() {
  const { setSetupDone, server, refreshProjects, selectProject, notify } = useStore();
  const [llmOk, setLlmOk] = useState(!!server?.llm.configured);
  const [demoBusy, setDemoBusy] = useState(false);

  const demo = async () => {
    setDemoBusy(true);
    try {
      const r = await api<any>("/api/demo", { method: "POST" });
      setSetupDone(true);
      await refreshProjects();
      await selectProject(r.project.id);
      useStore.getState().setRun(r.run.id);
      useStore.getState().setRightTab("preview");
    } catch (e: any) {
      notify(e.message, "bad");
    } finally {
      setDemoBusy(false);
    }
  };

  return (
    <div className="min-h-full overflow-y-auto">
      <div className="mx-auto grid max-w-6xl gap-10 px-6 py-10 lg:grid-cols-[1fr_1.15fr]">
        <div className="lg:sticky lg:top-10 lg:self-start">
          <div className="flex items-center gap-3">
            <Logo size={40} />
            <div>
              <div className="text-xl font-semibold tracking-tight">Luma <span className="glow-text">Studio</span></div>
              <div className="text-xs text-muted">Self-hosted AI motion-graphics & video studio</div>
            </div>
          </div>
          <h1 className="mt-10 text-3xl font-semibold leading-tight tracking-tight">
            Cinematic brand films from your own assets, <span className="glow-text">directed by any LLM.</span>
          </h1>
          <p className="mt-4 text-sm leading-relaxed text-muted">
            Upload a logo, brand guidelines or a script. The director plans the film, writes scene code on a bundled rendering engine,
            previews and critiques its own frames, designs the sound, and delivers a QC-checked MP4 — while you watch every step live.
          </p>
          <ul className="mt-6 space-y-3 text-sm">
            <Feature icon={<Clapperboard className="h-4 w-4" />} title="Logo construction, explainers, promos">Real geometry, springs, light, motion blur. Pixel-exact final lockup.</Feature>
            <Feature icon={<TerminalSquare className="h-4 w-4" />} title="A full terminal, in the open">The agent has a sandboxed shell you can watch or take over.</Feature>
            <Feature icon={<Mic2 className="h-4 w-4" />} title="Sound design & voice">Built-in synthesiser; optional ElevenLabs voice, SFX and music.</Feature>
          </ul>
          <div className="mt-8 rounded-xl border border-line bg-panel p-4">
            <div className="flex items-center gap-2 text-sm font-medium"><Sparkles className="h-4 w-4 text-accent" /> Try the demo — no keys needed</div>
            <p className="mt-1 text-xs text-muted">Renders the <code className="font-mono">fan_unfold</code> template on a bundled original sample logo (5 s, 1080p60, with sound design and QC). Takes a few minutes on a laptop.</p>
            <Button className="mt-3" onClick={demo} loading={demoBusy}><Play className="h-4 w-4" /> Render the demo</Button>
          </div>
          <div className="mt-4 flex gap-2 rounded-xl border border-warn/25 bg-warn/5 p-3 text-xs text-warn/90">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
            <span>No login: anyone who can reach this port gets a shell and your API credits. Keep it on 127.0.0.1 or behind an authenticating reverse proxy.</span>
          </div>
        </div>
        <div className="space-y-6">
          <section className="panel p-5">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h2 className="font-semibold">A · Language model <span className="text-bad">*</span></h2>
                <p className="text-xs text-muted">Any OpenAI-compatible endpoint with tool calling: OpenAI, OpenRouter, vLLM, Ollama, LM Studio…</p>
              </div>
              {llmOk && <Badge tone="ok">verified</Badge>}
            </div>
            <LlmConnection onValid={() => setLlmOk(true)} />
          </section>
          <section className="panel p-5">
            <div className="mb-4">
              <h2 className="font-semibold">B · ElevenLabs <span className="text-xs font-normal text-faint">optional</span></h2>
              <p className="text-xs text-muted">Voice-over with word timestamps, voice design, sound effects and music.</p>
            </div>
            <ElevenLabsConnection />
          </section>
          <div className="flex items-center justify-end gap-3">
            {!llmOk && <span className="text-xs text-faint">Verify the LLM connection to continue</span>}
            <Button variant="primary" size="lg" disabled={!llmOk} onClick={() => setSetupDone(true)}>Open the studio</Button>
          </div>
        </div>
      </div>
    </div>
  );
}

function Feature({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <li className="flex gap-3">
      <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-line bg-panel text-accent">{icon}</span>
      <span><span className="font-medium">{title}</span><span className="block text-xs text-muted">{children}</span></span>
    </li>
  );
}
