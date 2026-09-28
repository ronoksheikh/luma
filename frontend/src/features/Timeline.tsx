import * as Collapsible from "@radix-ui/react-collapsible";
import {
  AlertTriangle, AudioLines, Brain, Check, ChevronRight, CircleStop, Clapperboard, Eye, FileCode2, FilePen, FolderTree, Gauge, HelpCircle, Image as ImageIcon,
  Info, Loader2, Mic2, Music, PackageCheck, Play, SendHorizonal, ShieldCheck, Sparkles, Square, Terminal, Wand2, X,
} from "lucide-react";
import { memo, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Badge, Button, Dialog, Empty, ProgressBar, Textarea, Tip, cn } from "../components/ui";
import { api } from "../lib/api";
import { ACTIVE, type Item, type Media, type RunState, type ToolItem } from "../lib/run";
import { useStore } from "../lib/store";

const ICONS: Record<string, any> = {
  terminal_run: Terminal, terminal_spawn: Terminal, terminal_poll: Gauge, terminal_kill: CircleStop, terminal_send_keys: Terminal,
  list_files: FolderTree, read_file: FileCode2, write_file: FilePen, edit_file: FilePen, inspect_asset: Eye, render_preview: ImageIcon,
  view_image: Eye, render_final: Clapperboard, audio_analyze: AudioLines, encode: Wand2, qc_report: ShieldCheck, ask_user: HelpCircle,
  finish: PackageCheck, el_tts: Mic2, el_design_voice: Mic2, el_save_designed_voice: Mic2, el_sound_effect: Sparkles, el_music: Music,
  el_speech_to_text: Mic2, el_list_voices: Mic2, el_list_models: Mic2,
};

export function Timeline({ run }: { run: RunState }) {
  const { runId, projectId, projects, refreshRuns, refreshProjects, keys, notify } = useStore();
  const project = projects.find((p) => p.id === projectId);
  const scroller = useRef<HTMLDivElement>(null);
  const [stick, setStick] = useState(true);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [lightbox, setLightbox] = useState<Media | null>(null);
  const active = ACTIVE.has(run.status);

  useEffect(() => {
    const el = scroller.current;
    if (el && stick) el.scrollTop = el.scrollHeight;
  }, [run.items, stick]);

  const onScroll = () => {
    const el = scroller.current;
    if (!el) return;
    setStick(el.scrollHeight - el.scrollTop - el.clientHeight < 80);
  };

  const send = async (msg?: string) => {
    const t = (msg ?? text).trim();
    if (!t || !projectId) return;
    if (!keys.llmModel && !useStore.getState().server?.llm.configured) {
      notify("Configure an LLM in Settings first (or try the keyless demo).", "bad");
      useStore.getState().setSettingsOpen(true);
      return;
    }
    setSending(true);
    try {
      const agentRun = runId && useStore.getState().runs.find((r) => r.id === runId && r.kind === "agent");
      if (agentRun) {
        await api(`/api/runs/${runId}/message`, { method: "POST", json: { text: t } });
      } else {
        const r = await api<any>(`/api/projects/${projectId}/runs`, { method: "POST", json: { text: t } });
        await refreshRuns();
        useStore.getState().setRun(r.id);
      }
      setText("");
      setStick(true);
      refreshProjects();
    } catch (e: any) {
      notify(e.message, "bad");
    } finally {
      setSending(false);
    }
  };

  const stop = async () => {
    if (!runId) return;
    try {
      await api(`/api/runs/${runId}/cancel`, { method: "POST" });
      notify("Stopped: model stream, ElevenLabs requests, terminal command and jobs cancelled.", "info");
    } catch (e: any) {
      notify(e.message, "bad");
    }
  };

  const u = run.usage;
  return (
    <section className="flex h-full min-h-0 flex-col">
      <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-2.5">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">{project?.name ?? "No project"}</div>
          <div className="flex items-center gap-2 text-[11px] text-faint">
            <StatusPill status={run.status} connected={run.connected} hasRun={!!runId} />
            {u.steps != null && <span className="tabular-nums">steps {u.steps}{u.max_steps ? `/${u.max_steps}` : ""}</span>}
            {(u.prompt_tokens || u.completion_tokens) ? <span className="tabular-nums">tokens {fmtK((u.prompt_tokens || 0) + (u.completion_tokens || 0))}</span> : null}
            {u.el_chars != null && <span className="tabular-nums">EL chars {u.el_chars}{u.el_budget ? `/${u.el_budget}` : ""}</span>}
          </div>
        </div>
        {active && <Button variant="danger" size="sm" onClick={stop}><Square className="h-3 w-3 fill-current" /> Stop</Button>}
      </header>

      <div ref={scroller} onScroll={onScroll} className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-4" aria-live="polite">
        {!runId && <Welcome onPick={(t) => setText(t)} />}
        {run.items.map((it) => <TimelineItem key={it.id + it.kind} item={it} onImage={setLightbox} />)}
        {active && run.items.length > 0 && run.items[run.items.length - 1].kind !== "tool" && (
          <div className="flex items-center gap-2 pl-1 text-xs text-faint"><Loader2 className="h-3.5 w-3.5 animate-spin" /> directing…</div>
        )}
        {run.status === "completed" && run.summary && <DeliveredCard />}
      </div>

      {run.question && <AskPanel q={run.question} onAnswer={send} />}

      <div className="border-t border-line p-3">
        <div className="relative">
          <Textarea
            rows={2}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey || !e.shiftKey) && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }}
            placeholder={runId ? (active ? "Add guidance — it's injected before the next step…" : "Ask for changes: “make it a fan unfold, beams first”, “add a calm female voice-over saying …”") : "Describe the film you want…"}
            className="pr-12"
            aria-label="Message the director"
          />
          <Button variant="primary" size="icon" className="absolute bottom-2 right-2" onClick={() => send()} loading={sending} disabled={!text.trim() || !projectId} aria-label="Send">
            {!sending && <SendHorizonal className="h-4 w-4" />}
          </Button>
        </div>
        <div className="mt-1.5 flex justify-between text-[10px] text-faint"><span><span className="kbd">Enter</span> send · <span className="kbd">Shift+Enter</span> newline</span>{runId && <span className="font-mono">{runId}</span>}</div>
      </div>
      <Lightbox media={lightbox} onClose={() => setLightbox(null)} />
    </section>
  );
}

function fmtK(n: number) {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

function StatusPill({ status, connected, hasRun }: { status: string; connected: boolean; hasRun: boolean }) {
  if (!hasRun) return <span>no run yet</span>;
  const tone: Record<string, string> = { running: "text-accent", waiting_input: "text-info", completed: "text-ok", idle: "text-muted", failed: "text-bad", cancelled: "text-warn", stopped: "text-warn", interrupted: "text-warn" };
  return (
    <span className={cn("flex items-center gap-1", tone[status] || "text-muted")}>
      <span className={cn("h-1.5 w-1.5 rounded-full bg-current", status === "running" && "animate-pulse-soft")} />
      {status.replace("_", " ")}
      {!connected && <span className="text-faint">· reconnecting</span>}
    </span>
  );
}

function Welcome({ onPick }: { onPick: (t: string) => void }) {
  const ideas = [
    "Make a 5-second 1080p60 brand outro from my logo: construct the mark from its parts with light, springs and a sheen, then hold the exact lockup.",
    "Create a 20-second product explainer with a calm, confident voice-over and captions synced to the words.",
    "A 9:16 social teaser: the logo writes on with a pen tip, fills with the brand gradient and punches in on a sub-bass hit.",
  ];
  return (
    <div className="mx-auto max-w-lg py-10 text-center">
      <div className="text-lg font-semibold">What are we making?</div>
      <p className="mt-1 text-sm text-muted">Upload your assets on the left, set the output on the left, then brief the director. You'll see every step, the terminal and the frames as it works.</p>
      <div className="mt-5 space-y-2 text-left">
        {ideas.map((t) => (
          <button key={t} onClick={() => onPick(t)} className="w-full rounded-xl border border-line bg-panel px-3.5 py-2.5 text-sm text-muted hover:border-accent/40 hover:text-fg">{t}</button>
        ))}
      </div>
    </div>
  );
}

const TimelineItem = memo(function TimelineItem({ item, onImage }: { item: Item; onImage: (m: Media) => void }) {
  if (item.kind === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md border border-accent/20 bg-accent/10 px-3.5 py-2 text-sm">{item.text}</div>
      </div>
    );
  }
  if (item.kind === "assistant") {
    return (
      <div className="space-y-1.5">
        {item.reasoning && <Reasoning text={item.reasoning} />}
        {item.text && <div className={cn("md text-sm leading-relaxed", item.final && "rounded-xl border border-ok/25 bg-ok/5 px-3.5 py-2")}><ReactMarkdown remarkPlugins={[remarkGfm]}>{item.text}</ReactMarkdown></div>}
      </div>
    );
  }
  if (item.kind === "notice") {
    const tone = item.tone === "error" ? "border-bad/30 bg-bad/10 text-bad" : item.tone === "warn" ? "border-warn/30 bg-warn/10 text-warn" : "border-info/30 bg-info/10 text-info";
    return (
      <div className={cn("flex gap-2 rounded-xl border px-3 py-2 text-xs", tone)}>
        {item.tone === "error" ? <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> : <Info className="mt-0.5 h-4 w-4 shrink-0" />}
        <span className="whitespace-pre-wrap break-words">{item.text}</span>
      </div>
    );
  }
  return <ToolCard t={item} onImage={onImage} />;
});

function Reasoning({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <Collapsible.Root open={open} onOpenChange={setOpen}>
      <Collapsible.Trigger className="flex items-center gap-1 text-[11px] text-faint hover:text-muted">
        <Brain className="h-3.5 w-3.5" /> reasoning <ChevronRight className={cn("h-3 w-3 transition-transform", open && "rotate-90")} />
      </Collapsible.Trigger>
      <Collapsible.Content>
        <div className="mt-1 max-h-60 overflow-y-auto whitespace-pre-wrap border-l-2 border-line pl-3 text-xs italic text-faint">{text}</div>
      </Collapsible.Content>
    </Collapsible.Root>
  );
}

function prettyArgs(raw: string): { obj: any | null; text: string } {
  try {
    const obj = JSON.parse(raw);
    return { obj, text: JSON.stringify(obj, null, 2) };
  } catch {
    return { obj: null, text: raw };
  }
}

function argSummary(name: string, obj: any): string {
  if (!obj) return "";
  const v = obj.command ?? obj.path ?? obj.scene_path ?? obj.output ?? obj.source ?? obj.prompt ?? obj.text ?? obj.question ?? obj.name ?? obj.filter;
  if (typeof v !== "string") return "";
  const s = v.split("\n")[0];
  return (name.startsWith("terminal") && obj.command ? "$ " : "") + (s.length > 90 ? s.slice(0, 90) + "…" : s);
}

function ToolCard({ t, onImage }: { t: ToolItem; onImage: (m: Media) => void }) {
  const [open, setOpen] = useState(t.status === "running");
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    if (t.status !== "running") return;
    const i = setInterval(() => setNow(Date.now() / 1000), 500);
    return () => clearInterval(i);
  }, [t.status]);
  useEffect(() => { if (t.status === "error") setOpen(true); }, [t.status]);
  const Icon = ICONS[t.name] || Wand2;
  const { obj, text: argText } = useMemo(() => prettyArgs(t.args), [t.args]);
  const secs = t.seconds ?? Math.max(0, now - t.startedAt);
  const outRef = useRef<HTMLPreElement>(null);
  useEffect(() => { if (outRef.current) outRef.current.scrollTop = outRef.current.scrollHeight; }, [t.output, open]);
  const p = t.progress;
  const statusEl = t.status === "running" ? <Loader2 className="h-3.5 w-3.5 animate-spin text-accent" /> : t.status === "success" ? <Check className="h-3.5 w-3.5 text-ok" />
    : t.status === "cancelled" ? <CircleStop className="h-3.5 w-3.5 text-warn" /> : <X className="h-3.5 w-3.5 text-bad" />;
  const chars = t.ui?.chars;
  const diff: string | undefined = t.ui?.diff;
  const code = !diff && t.name === "write_file" && obj?.content ? String(obj.content) : null;

  return (
    <Collapsible.Root open={open} onOpenChange={setOpen} className={cn("overflow-hidden rounded-xl border bg-panel", t.status === "error" ? "border-bad/30" : "border-line")}>
      <Collapsible.Trigger className="flex w-full items-center gap-2.5 px-3 py-2 text-left hover:bg-raised/40">
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-line bg-bg text-muted"><Icon className="h-3.5 w-3.5" /></span>
        <span className="font-mono text-xs font-medium">{t.name}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-faint">{argSummary(t.name, obj)}</span>
        {chars != null && <Badge tone={t.ui?.cached ? "ok" : "accent"}>{t.ui?.cached ? "cached" : `${chars} chars`}</Badge>}
        {t.job && <Badge tone="info">{t.job.status}</Badge>}
        <span className="shrink-0 text-[11px] tabular-nums text-faint">{secs.toFixed(1)}s</span>
        {statusEl}
        <ChevronRight className={cn("h-3.5 w-3.5 shrink-0 text-faint transition-transform", open && "rotate-90")} />
      </Collapsible.Trigger>
      {p && p.total ? (
        <div className="px-3 pb-2">
          <div className="mb-1 flex justify-between text-[11px] text-muted">
            <span>{p.stage === "render" ? `frame ${p.frame} / ${p.total}` : `${p.stage ?? "working"} ${p.frame ?? ""}/${p.total}`}{p.fps ? ` · ${p.fps} fps` : ""}</span>
            <span>{p.eta_s != null ? `ETA ${Math.round(p.eta_s)} s` : p.message ?? ""}</span>
          </div>
          <ProgressBar value={(p.frame || 0) / p.total} />
        </div>
      ) : null}
      {(t.images.length > 0 || t.audios.length > 0) && (
        <div className="space-y-2 px-3 pb-2.5">
          {t.images.length > 0 && (
            <div className="flex gap-2 overflow-x-auto">
              {t.images.map((m) => (
                <button key={m.url} onClick={() => onImage(m)} className="shrink-0 overflow-hidden rounded-lg border border-line hover:border-accent/50" aria-label={`Open ${m.label ?? m.path}`}>
                  <img src={m.url} alt={m.label ?? ""} className="h-40 max-w-[520px] object-contain" loading="lazy" />
                </button>
              ))}
            </div>
          )}
          {t.audios.map((a) => <AudioRow key={a.url} a={a} />)}
        </div>
      )}
      <Collapsible.Content>
        <div className="space-y-2 border-t border-line px-3 py-2.5">
          {diff ? <DiffView diff={diff} /> : code ? (
            <div><div className="mb-1 text-[10px] uppercase tracking-wide text-faint">{obj?.path}</div><pre className="max-h-72 overflow-auto rounded-lg bg-bg p-2.5 font-mono text-[11px] leading-relaxed text-muted">{code}</pre></div>
          ) : argText && argText !== "{}" ? (
            <div><div className="mb-1 text-[10px] uppercase tracking-wide text-faint">arguments</div><pre className="max-h-56 overflow-auto rounded-lg bg-bg p-2.5 font-mono text-[11px] text-muted">{argText}</pre></div>
          ) : null}
          {t.output && (
            <div><div className="mb-1 text-[10px] uppercase tracking-wide text-faint">live output</div><pre ref={outRef} className="max-h-64 overflow-auto rounded-lg bg-bg p-2.5 font-mono text-[11px] text-muted">{t.output.slice(-20000)}</pre></div>
          )}
          {t.result && (
            <div><div className="mb-1 text-[10px] uppercase tracking-wide text-faint">result</div><pre className={cn("max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-bg p-2.5 font-mono text-[11px]", t.status === "error" ? "text-bad" : "text-muted")}>{t.result}</pre></div>
          )}
        </div>
      </Collapsible.Content>
    </Collapsible.Root>
  );
}

export function AudioRow({ a }: { a: Media }) {
  return (
    <div className="rounded-lg border border-line bg-bg px-2.5 py-2">
      <div className="mb-1 flex items-center justify-between gap-2 text-[11px]">
        <span className="flex min-w-0 items-center gap-1.5 text-muted"><AudioLines className="h-3.5 w-3.5 shrink-0" /><span className="truncate">{a.label ?? a.path}</span></span>
        <span className="flex shrink-0 gap-1">
          {a.duration != null && <Badge>{Number(a.duration).toFixed(2)}s</Badge>}
          {a.lufs != null && <Badge>{a.lufs} LUFS</Badge>}
          {a.true_peak_dbtp != null && <Badge>{a.true_peak_dbtp} dBTP</Badge>}
          {a.chars != null && <Badge tone={a.cached ? "ok" : "accent"}>{a.cached ? "cached" : `${a.chars} chars`}</Badge>}
        </span>
      </div>
      <audio controls preload="none" src={a.url} className="h-8 w-full" />
      {a.text && <div className="mt-1 line-clamp-3 text-[11px] italic text-faint">“{a.text}”</div>}
    </div>
  );
}

function DiffView({ diff }: { diff: string }) {
  return (
    <pre className="max-h-72 overflow-auto rounded-lg bg-bg p-2 font-mono text-[11px] leading-relaxed">
      {diff.split("\n").map((l, i) => (
        <div key={i} className={cn("px-1", l.startsWith("+") && !l.startsWith("+++") ? "bg-ok/10 text-ok" : l.startsWith("-") && !l.startsWith("---") ? "bg-bad/10 text-bad" : l.startsWith("@@") ? "text-info" : "text-faint")}>{l || " "}</div>
      ))}
    </pre>
  );
}

function AskPanel({ q, onAnswer }: { q: { text: string; options: string[] }; onAnswer: (t: string) => void }) {
  return (
    <div className="mx-3 mb-2 rounded-xl border border-info/30 bg-info/10 p-3">
      <div className="flex items-center gap-2 text-sm font-medium text-info"><HelpCircle className="h-4 w-4" /> The director needs your input</div>
      <div className="md mt-1 text-sm"><ReactMarkdown remarkPlugins={[remarkGfm]}>{q.text}</ReactMarkdown></div>
      {q.options.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-2">{q.options.map((o) => <Button key={o} size="sm" onClick={() => onAnswer(o)}>{o}</Button>)}</div>
      )}
      <div className="mt-2 text-[11px] text-faint">…or type an answer below.</div>
    </div>
  );
}

function DeliveredCard() {
  const setRightTab = useStore((s) => s.setRightTab);
  return (
    <div className="flex items-center justify-between rounded-xl border border-ok/25 bg-ok/5 px-3.5 py-2.5 text-sm">
      <span className="flex items-center gap-2 text-ok"><PackageCheck className="h-4 w-4" /> Delivered</span>
      <div className="flex gap-2">
        <Button size="sm" onClick={() => setRightTab("preview")}><Play className="h-3.5 w-3.5" /> Watch</Button>
        <Button size="sm" onClick={() => setRightTab("outputs")}>Downloads</Button>
      </div>
    </div>
  );
}

export function Lightbox({ media, onClose }: { media: Media | null; onClose: () => void }) {
  if (!media) return null;
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()} title={media.label ?? media.path} description={media.path} wide>
      <a href={media.url} target="_blank" rel="noreferrer"><img src={media.url} alt={media.label ?? ""} className="w-full rounded-lg border border-line" /></a>
    </Dialog>
  );
}

export { Empty, Tip };
