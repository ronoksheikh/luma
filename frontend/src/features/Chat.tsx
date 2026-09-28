import { Alert, Button, Chip, Disclosure, ListBox, ProgressBar, Select, Spinner, TextArea } from "@heroui/react";
import {
  ArrowUp, Brain, CheckCircle, ClockCounterClockwise, Eye, FileCode, FilmSlate, FolderSimple, Gauge, Image as ImageIcon, List,
  MagicWand, Microphone, MusicNotes, NotePencil, Package, Paperclip, Question, ShieldCheck, SlidersHorizontal, Sparkle, Square, StopCircle,
  Terminal, Waveform, XCircle,
} from "@phosphor-icons/react";
import { memo, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "../lib/api";
import { ACTIVE, type Item, type Media, type RunState, type ToolItem } from "../lib/run";
import { useStore } from "../lib/store";
import { Tip, cn, fmtK } from "../ui/kit";
import { AudioRow, Lightbox } from "../ui/media";
import { useUpload } from "./Assets";

type ToolMeta = { icon: any; label: string };
const TOOLS: Record<string, ToolMeta> = {
  terminal_run: { icon: Terminal, label: "Ran a command" },
  terminal_spawn: { icon: Terminal, label: "Started a job" },
  terminal_poll: { icon: Gauge, label: "Checked a job" },
  terminal_kill: { icon: StopCircle, label: "Stopped a job" },
  terminal_send_keys: { icon: Terminal, label: "Typed into the terminal" },
  list_files: { icon: FolderSimple, label: "Listed files" },
  read_file: { icon: FileCode, label: "Read a file" },
  write_file: { icon: NotePencil, label: "Wrote a file" },
  edit_file: { icon: NotePencil, label: "Edited a file" },
  inspect_asset: { icon: Eye, label: "Inspected an asset" },
  render_preview: { icon: ImageIcon, label: "Rendered a preview" },
  view_image: { icon: Eye, label: "Looked at an image" },
  render_final: { icon: FilmSlate, label: "Rendered the film" },
  audio_analyze: { icon: Waveform, label: "Analysed audio" },
  encode: { icon: MagicWand, label: "Encoded deliverables" },
  qc_report: { icon: ShieldCheck, label: "Ran quality checks" },
  ask_user: { icon: Question, label: "Asked you a question" },
  finish: { icon: Package, label: "Delivered" },
  el_tts: { icon: Microphone, label: "Generated voice-over" },
  el_design_voice: { icon: Microphone, label: "Designed a voice" },
  el_save_designed_voice: { icon: Microphone, label: "Saved a voice" },
  el_sound_effect: { icon: Sparkle, label: "Generated a sound effect" },
  el_music: { icon: MusicNotes, label: "Composed music" },
  el_speech_to_text: { icon: Microphone, label: "Transcribed audio" },
  el_list_voices: { icon: Microphone, label: "Listed voices" },
  el_list_models: { icon: Microphone, label: "Listed voice models" },
};

const STATUS: Record<string, { label: string; color: "accent" | "success" | "warning" | "danger" | "default" }> = {
  running: { label: "Working", color: "accent" },
  waiting_input: { label: "Needs input", color: "warning" },
  completed: { label: "Delivered", color: "success" },
  failed: { label: "Failed", color: "danger" },
  cancelled: { label: "Stopped", color: "default" },
  stopped: { label: "Stopped", color: "default" },
  interrupted: { label: "Interrupted", color: "warning" },
  idle: { label: "Idle", color: "default" },
};

export function Chat({ run }: { run: RunState }) {
  const { runId, projectId, refreshRuns, refreshProjects, keys, notify, server, openSettings } = useStore();
  const scroller = useRef<HTMLDivElement>(null);
  const [stick, setStick] = useState(true);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [lightbox, setLightbox] = useState<Media | null>(null);
  const active = ACTIVE.has(run.status);
  const llmReady = !!server?.llm.configured || !!(keys.llmBaseUrl && keys.llmModel);

  useEffect(() => {
    const el = scroller.current;
    if (el && stick) el.scrollTop = el.scrollHeight;
  }, [run.items, run.question, stick]);

  const send = async (msg?: string) => {
    const t = (msg ?? text).trim();
    if (!t || !projectId) return;
    if (!llmReady) {
      notify("Connect a language model first.", "bad");
      openSettings("connections");
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
      notify("Stopped. The model stream, requests, terminal command and jobs were cancelled.", "info");
    } catch (e: any) {
      notify(e.message, "bad");
    }
  };

  return (
    <section className="flex h-full min-h-0 flex-col" aria-label="Director">
      <ChatHeader run={run} />
      <div ref={scroller} aria-live="polite"
        onScroll={() => { const el = scroller.current; if (el) setStick(el.scrollHeight - el.scrollTop - el.clientHeight < 80); }}
        className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-[760px] flex-col gap-4 px-5 pb-8 pt-6">
          {!runId && <Welcome llmReady={llmReady} onPick={setText} />}
          {run.items.map((it) => <TimelineItem key={it.kind + it.id} item={it} onImage={setLightbox} />)}
          {active && !run.question && run.items.length > 0 && run.items[run.items.length - 1].kind !== "tool" && (
            <div className="flex items-center gap-2 text-sm text-muted"><Spinner size="sm" /> Directing…</div>
          )}
          {run.status === "completed" && <Delivered />}
        </div>
      </div>
      {run.question && <AskPanel q={run.question} onAnswer={send} />}
      <Composer text={text} setText={setText} onSend={() => send()} onStop={stop} sending={sending} active={active} hasRun={!!runId} disabled={!projectId} />
      <Lightbox media={lightbox} onClose={() => setLightbox(null)} />
    </section>
  );
}

function ChatHeader({ run }: { run: RunState }) {
  const { runId, runs, setRun, projects, projectId, setProjectSettingsOpen, setNavOpen } = useStore();
  const project = projects.find((p) => p.id === projectId);
  const st = STATUS[run.status] ?? STATUS.idle;
  const u = run.usage;
  const tokens = (u.prompt_tokens || 0) + (u.completion_tokens || 0);
  return (
    <header className="flex h-14 shrink-0 items-center gap-2 border-b border-separator px-3 sm:px-4">
      <Button isIconOnly variant="ghost" size="sm" aria-label="Open projects" className="lg:hidden" onPress={() => setNavOpen(true)}><List size={18} /></Button>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <h1 className="truncate text-[15px]">{project?.name ?? "No project"}</h1>
          {runId && (
            <Chip size="sm" variant="soft" color={st.color}>
              {run.status === "running" && <span className="size-1.5 animate-breathe rounded-full bg-current" />}
              <Chip.Label>{st.label}{!run.connected && ACTIVE.has(run.status) ? " · reconnecting" : ""}</Chip.Label>
            </Chip>
          )}
        </div>
        {runId && (u.steps != null || tokens > 0) && (
          <div className="flex gap-3 text-xs tabular-nums text-muted">
            {u.steps != null && <span>{u.steps}{u.max_steps ? ` / ${u.max_steps}` : ""} steps</span>}
            {tokens > 0 && <span>{fmtK(tokens)} tokens</span>}
            {!!u.el_chars && <span>{fmtK(u.el_chars)} voice chars</span>}
          </div>
        )}
      </div>
      {runs.length > 1 && (
        <Select aria-label="Run history" selectedKey={runId} onSelectionChange={(k) => setRun(String(k))} className="hidden w-[200px] sm:block">
          <Select.Trigger>
            <ClockCounterClockwise size={16} className="shrink-0 text-muted" />
            <Select.Value />
            <Select.Indicator />
          </Select.Trigger>
          <Select.Popover>
            <ListBox>
              {runs.map((r, i) => (
                <ListBox.Item key={r.id} id={r.id} textValue={`Run ${runs.length - i}`}>
                  <span className="flex-1">{r.kind === "demo" ? "Demo" : "Run"} {runs.length - i}</span>
                  <span className="text-xs text-muted">{STATUS[r.status]?.label ?? r.status}</span>
                  <ListBox.ItemIndicator />
                </ListBox.Item>
              ))}
            </ListBox>
          </Select.Popover>
        </Select>
      )}
      {project && (
        <Tip content="Brief & output settings">
          <Button variant="ghost" size="sm" onPress={() => setProjectSettingsOpen(true)} aria-label="Brief and output settings">
            <SlidersHorizontal size={16} /><span className="hidden md:inline">Brief & output</span>
          </Button>
        </Tip>
      )}
    </header>
  );
}

const IDEAS = [
  { title: "Logo outro", text: "Make a 5-second 1080p60 brand outro from my logo: build the mark from its parts with light and springy motion, then hold the exact lockup." },
  { title: "Voiced explainer", text: "Create a 20-second product explainer with a calm, confident voice-over and captions synced to the words." },
  { title: "Vertical teaser", text: "A 9:16 social teaser: the logo draws on, fills with the brand gradient and lands on a sub-bass hit." },
];

function Welcome({ llmReady, onPick }: { llmReady: boolean; onPick: (t: string) => void }) {
  const openSettings = useStore((s) => s.openSettings);
  const { busy, pick, inputEl } = useUpload();
  return (
    <div className="flex flex-col gap-8 pt-[6vh]">
      <div>
        <h2 className="text-[28px] leading-tight">What are we making?</h2>
        <p className="mt-2 max-w-[520px] text-[15px] text-muted">
          Add your logo and brand files, describe the film, and the director plans, animates, renders and checks it while you watch.
        </p>
      </div>
      {!llmReady && (
        <Alert status="accent">
          <Alert.Indicator />
          <Alert.Content>
            <Alert.Title>Connect a language model</Alert.Title>
            <Alert.Description>Any OpenAI-compatible provider works. The demo runs without one.</Alert.Description>
          </Alert.Content>
          <Button size="sm" onPress={() => openSettings("connections")}>Connect</Button>
        </Alert>
      )}
      <div className="grid gap-2 sm:grid-cols-3">
        {IDEAS.map((i) => (
          <button key={i.title} onClick={() => onPick(i.text)}
            className="group rounded-2xl border border-border bg-surface p-4 text-left transition-[border-color,box-shadow] hover:border-accent/40 hover:shadow-[0_2px_12px_-4px_rgba(41,112,236,0.18)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus">
            <div className="text-sm font-medium">{i.title}</div>
            <p className="mt-1 line-clamp-3 text-[13px] leading-snug text-muted">{i.text}</p>
          </button>
        ))}
      </div>
      <button onClick={pick} disabled={busy}
        className="flex items-center gap-3 rounded-2xl border border-dashed border-border px-4 py-3.5 text-left text-sm text-muted transition-colors hover:border-accent/50 hover:text-foreground">
        {busy ? <Spinner size="sm" /> : <Paperclip size={18} />}
        <span>{busy ? "Uploading and analysing…" : "Add brand assets: logo SVG, images, brand PDF, audio"}</span>
      </button>
      {inputEl}
    </div>
  );
}

const TimelineItem = memo(function TimelineItem({ item, onImage }: { item: Item; onImage: (m: Media) => void }) {
  if (item.kind === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-offwhite px-4 py-2.5 text-[14px] leading-relaxed">{item.text}</div>
      </div>
    );
  }
  if (item.kind === "assistant") {
    return (
      <div className="flex flex-col gap-2">
        {item.reasoning && <Reasoning text={item.reasoning} />}
        {item.text && <div className="md"><ReactMarkdown remarkPlugins={[remarkGfm]}>{item.text}</ReactMarkdown></div>}
      </div>
    );
  }
  if (item.kind === "notice") {
    return (
      <Alert status={item.tone === "error" ? "danger" : item.tone === "warn" ? "warning" : "default"}>
        <Alert.Indicator />
        <Alert.Content>
          <Alert.Description className="whitespace-pre-wrap break-words">{item.text}</Alert.Description>
        </Alert.Content>
      </Alert>
    );
  }
  return <ToolRow t={item} onImage={onImage} />;
});

function Reasoning({ text }: { text: string }) {
  return (
    <Disclosure>
      <Disclosure.Heading>
        <Button slot="trigger" variant="ghost" size="sm" className="-ml-2 h-7 gap-1.5 px-2 text-muted">
          <Brain size={15} /> Thinking <Disclosure.Indicator />
        </Button>
      </Disclosure.Heading>
      <Disclosure.Content>
        <Disclosure.Body>
          <p className="max-h-64 overflow-y-auto whitespace-pre-wrap border-l-2 border-border pl-3 text-[13px] leading-relaxed text-muted">{text}</p>
        </Disclosure.Body>
      </Disclosure.Content>
    </Disclosure>
  );
}

function parseArgs(raw: string): any | null {
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function argSummary(name: string, obj: any): string {
  if (!obj) return "";
  const v = obj.command ?? obj.path ?? obj.scene_path ?? obj.output ?? obj.source ?? obj.prompt ?? obj.text ?? obj.question ?? obj.name ?? obj.filter;
  if (typeof v !== "string") return "";
  const s = v.split("\n")[0];
  return (name.startsWith("terminal") && obj.command ? "$ " : "") + (s.length > 110 ? s.slice(0, 110) + "…" : s);
}

function ToolRow({ t, onImage }: { t: ToolItem; onImage: (m: Media) => void }) {
  const [open, setOpen] = useState(false);
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    if (t.status !== "running") return;
    const i = setInterval(() => setNow(Date.now() / 1000), 500);
    return () => clearInterval(i);
  }, [t.status]);
  useEffect(() => { if (t.status === "error") setOpen(true); }, [t.status]);
  const meta = TOOLS[t.name] ?? { icon: MagicWand, label: t.name };
  const Icon = meta.icon;
  const obj = useMemo(() => parseArgs(t.args), [t.args]);
  const secs = t.seconds ?? Math.max(0, now - t.startedAt);
  const p = t.progress;
  const chars = t.ui?.chars;
  const diff: string | undefined = t.ui?.diff;
  const code = !diff && t.name === "write_file" && obj?.content ? String(obj.content) : null;
  const argText = obj ? JSON.stringify(obj, null, 2) : t.args;
  const outRef = useRef<HTMLPreElement>(null);
  useEffect(() => { if (outRef.current) outRef.current.scrollTop = outRef.current.scrollHeight; }, [t.output, open]);

  return (
    <div className={cn("rounded-2xl border bg-surface", t.status === "error" ? "border-danger/30" : "border-border")}>
      <Disclosure isExpanded={open} onExpandedChange={setOpen}>
        <Disclosure.Heading>
          <Button slot="trigger" variant="ghost" fullWidth className="h-auto justify-start gap-3 rounded-2xl px-3 py-2.5 text-left font-normal">
            <span className={cn("flex size-7 shrink-0 items-center justify-center rounded-lg",
              t.status === "running" ? "bg-accent/10 text-accent" : "bg-surface-secondary text-muted")}>
              <Icon size={16} />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-[13.5px] text-foreground">{meta.label}</span>
              {argSummary(t.name, obj) && <span className="block truncate font-mono text-[11.5px] text-muted">{argSummary(t.name, obj)}</span>}
            </span>
            {chars != null && <Chip size="sm" variant="soft" color={t.ui?.cached ? "success" : "accent"}>{t.ui?.cached ? "cached" : `${chars} chars`}</Chip>}
            {t.job && ["queued", "failed", "killed"].includes(t.job.status) && <Chip size="sm" variant="soft" color={t.job.status === "queued" ? "default" : "danger"}>{t.job.status}</Chip>}
            <span className="shrink-0 text-xs tabular-nums text-muted">{secs.toFixed(1)}s</span>
            <ToolStatus status={t.status} />
            <Disclosure.Indicator className="text-muted" />
          </Button>
        </Disclosure.Heading>
        {p && p.total ? (
          <div className="px-3.5 pb-3">
            <ProgressBar value={(100 * (p.frame || 0)) / p.total} size="sm" aria-label="Render progress">
              <div className="mb-1 flex w-full justify-between text-xs tabular-nums text-muted">
                <span>{p.stage === "render" ? `Frame ${p.frame} of ${p.total}` : `${p.stage ?? "Working"} ${p.frame ?? ""}/${p.total}`}{p.fps ? ` · ${p.fps} fps` : ""}</span>
                <span>{p.eta_s != null ? `${Math.round(p.eta_s)} s left` : p.message ?? ""}</span>
              </div>
              <ProgressBar.Track><ProgressBar.Fill /></ProgressBar.Track>
            </ProgressBar>
          </div>
        ) : null}
        {(t.images.length > 0 || t.audios.length > 0) && (
          <div className="flex flex-col gap-2 px-3 pb-3">
            {t.images.length > 0 && (
              <div className="flex gap-2 overflow-x-auto">
                {t.images.map((m) => (
                  <button key={m.url} onClick={() => onImage(m)} aria-label={`Open ${m.label ?? m.path}`}
                    className="checker shrink-0 overflow-hidden rounded-xl border border-border transition-colors hover:border-accent/50">
                    <img src={m.url} alt={m.label ?? ""} className="h-44 max-w-[560px] object-contain" loading="lazy" />
                  </button>
                ))}
              </div>
            )}
            {t.audios.map((a) => <AudioRow key={a.url} a={a} />)}
          </div>
        )}
        <Disclosure.Content>
          <Disclosure.Body className="flex flex-col gap-3 border-t border-separator px-3.5 py-3">
            <div className="font-mono text-[11px] text-muted">{t.name}</div>
            {diff ? <DiffView diff={diff} /> : code ? <Block title={obj?.path}>{code}</Block> : argText && argText !== "{}" ? <Block title="Arguments">{argText}</Block> : null}
            {t.output && <Block title="Output" preRef={outRef}>{t.output.slice(-20000)}</Block>}
            {t.result && <Block title="Result" danger={t.status === "error"}>{t.result}</Block>}
          </Disclosure.Body>
        </Disclosure.Content>
      </Disclosure>
    </div>
  );
}

function ToolStatus({ status }: { status: string }) {
  if (status === "running") return <Spinner size="sm" />;
  if (status === "success") return <CheckCircle size={18} weight="fill" className="shrink-0 text-success" />;
  if (status === "cancelled") return <StopCircle size={18} weight="fill" className="shrink-0 text-muted" />;
  return <XCircle size={18} weight="fill" className="shrink-0 text-danger" />;
}

function Block({ title, children, danger, preRef }: { title?: string; children: string; danger?: boolean; preRef?: React.Ref<HTMLPreElement> }) {
  return (
    <div>
      {title && <div className="mb-1 text-[11px] font-medium text-muted">{title}</div>}
      <pre ref={preRef} className={cn("max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-xl bg-surface-secondary p-3 font-mono text-[11.5px] leading-relaxed",
        danger ? "text-danger" : "text-foreground/80")}>{children}</pre>
    </div>
  );
}

function DiffView({ diff }: { diff: string }) {
  return (
    <pre className="max-h-72 overflow-auto rounded-xl bg-surface-secondary py-2 font-mono text-[11.5px] leading-relaxed">
      {diff.split("\n").map((l, i) => (
        <div key={i} className={cn("px-3",
          l.startsWith("+") && !l.startsWith("+++") ? "bg-success/10 text-success" :
          l.startsWith("-") && !l.startsWith("---") ? "bg-danger/10 text-danger" :
          l.startsWith("@@") ? "text-accent" : "text-muted")}>{l || " "}</div>
      ))}
    </pre>
  );
}

function AskPanel({ q, onAnswer }: { q: { text: string; options: string[] }; onAnswer: (t: string) => void }) {
  return (
    <div className="mx-auto w-full max-w-[760px] px-5 pb-2">
      <div className="rounded-2xl border border-accent/30 bg-offwhite p-4">
        <div className="flex items-center gap-2 text-sm font-medium text-brand-royal"><Question size={18} weight="fill" /> The director has a question</div>
        <div className="md mt-1.5"><ReactMarkdown remarkPlugins={[remarkGfm]}>{q.text}</ReactMarkdown></div>
        {q.options.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {q.options.map((o) => <Button key={o} size="sm" variant="secondary" onPress={() => onAnswer(o)}>{o}</Button>)}
          </div>
        )}
      </div>
    </div>
  );
}

function Delivered() {
  const setRightTab = useStore((s) => s.setRightTab);
  const setMainView = useStore((s) => s.setMainView);
  const go = (tab: string) => { setRightTab(tab); setMainView("inspector"); };
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-success/25 bg-success/5 px-4 py-3">
      <CheckCircle size={20} weight="fill" className="text-success" />
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium">Film delivered</div>
        <p className="text-xs text-muted">Watch it in the preview or download the files.</p>
      </div>
      <div className="flex gap-2">
        <Button size="sm" variant="secondary" onPress={() => go("preview")}>Watch</Button>
        <Button size="sm" variant="secondary" onPress={() => go("outputs")}>Downloads</Button>
      </div>
    </div>
  );
}

function Composer({ text, setText, onSend, onStop, sending, active, hasRun, disabled }: {
  text: string; setText: (t: string) => void; onSend: () => void; onStop: () => void; sending: boolean; active: boolean; hasRun: boolean; disabled: boolean;
}) {
  const { busy, pick, inputEl } = useUpload();
  return (
    <div className="shrink-0 px-3 pb-3 sm:px-5 sm:pb-4">
      <div className="mx-auto max-w-[760px] rounded-2xl border border-border bg-surface shadow-[0_1px_2px_rgba(16,24,40,0.04),0_8px_24px_-12px_rgba(16,24,40,0.10)] transition-[border-color,box-shadow] focus-within:border-accent/50 focus-within:shadow-[0_0_0_4px_rgba(41,112,236,0.10)]">
        <TextArea
          aria-label="Message the director"
          rows={2}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); onSend(); }
          }}
          placeholder={hasRun ? (active ? "Add guidance. It's read before the next step…" : "Ask for changes…") : "Describe the film you want…"}
          className="max-h-60 min-h-[56px] w-full resize-none border-0 bg-transparent px-4 pt-3.5 text-[14.5px] shadow-none focus:ring-0 data-[focus-visible]:ring-0"
        />
        <div className="flex items-center justify-between gap-2 px-2.5 pb-2.5">
          <div className="flex items-center gap-1">
            <Tip content="Add brand assets">
              <Button isIconOnly size="sm" variant="ghost" aria-label="Add brand assets" isDisabled={disabled || busy} onPress={pick}>
                {busy ? <Spinner size="sm" /> : <Paperclip size={18} />}
              </Button>
            </Tip>
            {inputEl}
            <span className="hidden text-xs text-muted sm:inline">Enter to send · Shift+Enter for a new line</span>
          </div>
          {active && !text.trim() ? (
            <Tip content="Stop the run">
              <Button isIconOnly size="sm" aria-label="Stop" onPress={onStop} className="rounded-full"><Square size={12} weight="fill" /></Button>
            </Tip>
          ) : (
            <Button isIconOnly size="sm" aria-label="Send" onPress={onSend} isPending={sending} isDisabled={!text.trim() || disabled} className="rounded-full">
              {({ isPending }) => (isPending ? <Spinner size="sm" color="current" /> : <ArrowUp size={16} weight="bold" />)}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

