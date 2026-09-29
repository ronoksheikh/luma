import { Alert, Button, Chip, Disclosure, ListBox, ProgressBar, Select, Spinner, TextArea } from "@heroui/react";
import {
  ArrowUp, Brain, CheckCircle, ClockCounterClockwise, Eye, FileCode, FilmSlate, FolderSimple, Gauge, Image as ImageIcon, List,
  MagicWand, Microphone, MusicNotes, NotePencil, Package, Paperclip, Question, ShieldCheck, SlidersHorizontal, Sparkle, Square, StopCircle,
  Terminal, Waveform, XCircle, ListChecks, GitCommit, Globe, Robot, Stack, CaretRight,
} from "@phosphor-icons/react";
import { memo, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "../lib/api";
import { ACTIVE, type Item, type Media, type RunState, type ToolItem } from "../lib/run";
import { PresentCard, RequestCard, SubagentCard } from "./Cards";
import { ToolboxEventCard } from "./Toolbox";
import { NotificationBell, askNotificationPermission } from "./Notifications";
import { RunBar, ResumeBanner } from "./RunBar";
import { useStore } from "../lib/store";
import { Tip, cn } from "../ui/kit";
import { Orb, Shimmer, ThinkingBlock } from "../ui/ai";
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
  todo_write: { icon: ListChecks, label: "Planned the work" },
  todo_update: { icon: ListChecks, label: "Updated the plan" },
  todo_add: { icon: ListChecks, label: "Added to the plan" },
  todo_list: { icon: ListChecks, label: "Read the plan" },
  memory_write: { icon: Brain, label: "Remembered a decision" },
  memory_read: { icon: Brain, label: "Read project memory" },
  memory_search: { icon: Brain, label: "Searched project memory" },
  notes_append: { icon: NotePencil, label: "Took notes" },
  notes_read: { icon: NotePencil, label: "Read notes" },
  checkpoint_create: { icon: GitCommit, label: "Saved a checkpoint" },
  checkpoint_list: { icon: GitCommit, label: "Listed checkpoints" },
  checkpoint_restore: { icon: GitCommit, label: "Restored a checkpoint" },
  context_compact: { icon: Brain, label: "Compacted context" },
  media_probe: { icon: Eye, label: "Probed media" },
  extract_frames: { icon: ImageIcon, label: "Extracted frames" },
  make_gif_or_webp: { icon: ImageIcon, label: "Made a preview loop" },
  make_thumbnail: { icon: ImageIcon, label: "Made a thumbnail" },
  export_end_card: { icon: ImageIcon, label: "Exported end cards" },
  reframe_export: { icon: FilmSlate, label: "Reframed for other aspects" },
  batch_render: { icon: FilmSlate, label: "Rendered variants" },
  vectorize_raster: { icon: MagicWand, label: "Traced a logo" },
  extract_palette: { icon: Eye, label: "Extracted a palette" },
  detect_fonts: { icon: Eye, label: "Detected fonts" },
  install_font: { icon: FileCode, label: "Installed a font" },
  audio_mix: { icon: Waveform, label: "Mixed audio" },
  captions_build: { icon: FileCode, label: "Built captions" },
  render_queue_status: { icon: Gauge, label: "Checked the render queue" },
  render_queue_cancel: { icon: StopCircle, label: "Cancelled a job" },
  budget_status: { icon: Gauge, label: "Checked the budget" },
  self_review: { icon: ShieldCheck, label: "Self-review" },
  web_fetch: { icon: Globe, label: "Read a web page" },
  web_search: { icon: Globe, label: "Searched the web" },
  spawn_subagent: { icon: Robot, label: "Delegated to a sub-agent" },
  subagent_report: { icon: Robot, label: "Reported back" },
};

// Tools whose result is shown as a card or request instead of a tool row
const CARD_TOOLS = new Set(["present_video", "present_image", "present_audio", "present_file", "present_comparison", "present_storyboard", "present_timeline",
  "present_code", "present_table", "present_palette", "ask_user", "request_approval", "present_options", "notify", "report_progress", "spawn_subagent", "self_review"]);

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
    askNotificationPermission();
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
      {runId && <RunBar run={run} />}
      <div ref={scroller} aria-live="polite"
        onScroll={() => { const el = scroller.current; if (el) setStick(el.scrollHeight - el.scrollTop - el.clientHeight < 80); }}
        className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-[760px] flex-col gap-4 px-5 pb-8 pt-6">
          {!runId && <Welcome llmReady={llmReady} onPick={setText} />}
          {renderItems(run.items, runId!, setLightbox, active)}
          {runId && <ResumeBanner run={run} />}
          {active && !run.question && !run.pendingRequest && run.items.length > 0 && run.items[run.items.length - 1].kind !== "tool" && (
            <div className="flex items-center gap-2.5 text-sm text-muted" role="status"><Orb active size={16} /><Shimmer>Directing…</Shimmer></div>
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
  return (
    <header className="flex h-14 shrink-0 items-center gap-2 border-b border-separator px-3 sm:px-4">
      <Button isIconOnly variant="ghost" size="sm" aria-label="Open projects" className="lg:hidden" onPress={() => setNavOpen(true)}><List size={18} /></Button>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <h1 className="truncate text-[16px]">{project?.name ?? "No project"}</h1>
          {runId && (
            <Chip size="sm" variant="soft" color={st.color}>
              {run.status === "running" && <span className="size-1.5 animate-breathe rounded-full bg-current" />}
              <Chip.Label>{st.label}{!run.connected && ACTIVE.has(run.status) ? " · reconnecting" : ""}</Chip.Label>
            </Chip>
          )}
        </div>
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
      <NotificationBell live={run.notifications.length} />
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
        <Orb size={32} className="mb-4" />
        <h2 className="text-[24px] leading-tight">What are we making?</h2>
        <p className="mt-2 max-w-[520px] text-[16px] text-muted">
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
            className="group rounded-2xl bg-surface-secondary p-4 text-left transition-colors duration-150 hover:bg-surface-tertiary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus">
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

const hiddenTool = (it: Item) => it.kind === "tool" && CARD_TOOLS.has(it.name) && it.status !== "error";

/** Timeline items; consecutive tool calls collapse into one "N steps" group. `live` = the run is streaming right now. */
export function renderItems(items: Item[], runId: string, onImage: (m: Media) => void = () => {}, live = false) {
  const out: ReactNode[] = [];
  let tools: ToolItem[] = [];
  const flush = () => {
    if (tools.length > 1) out.push(<StepGroup key={`g${tools[0].id}`} tools={tools} onImage={onImage} />);
    else tools.forEach((t) => out.push(<ToolRow key={`t${t.id}`} t={t} onImage={onImage} />));
    tools = [];
  };
  items.forEach((it, i) => {
    if (it.kind === "tool") { if (!hiddenTool(it)) tools.push(it); return; }
    flush();
    out.push(<TimelineItem key={it.kind + it.id} item={it} onImage={onImage} runId={runId} streaming={live && i === items.length - 1} />);
  });
  flush();
  return out;
}

const TimelineItem = memo(function TimelineItem({ item, onImage, runId, streaming }: { item: Item; onImage: (m: Media) => void; runId: string; streaming: boolean }) {
  if (item.kind === "card") return <PresentCard card={item.card} art={item.artifact} />;
  if (item.kind === "request") return <RequestCard r={item} runId={runId} />;
  if (item.kind === "toolbox") return <ToolboxEventCard ev={item.event} data={item.data} />;
  if (item.kind === "subagent") return <SubagentCard it={item} render={(its, rid) => renderItems(its, rid, onImage)} />;
  if (item.kind === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-sunken px-4 py-2.5 text-[14px] leading-relaxed">{item.text}</div>
      </div>
    );
  }
  if (item.kind === "assistant") {
    return (
      <div className="flex flex-col gap-2">
        {item.reasoning && <ThinkingBlock text={item.reasoning} streaming={streaming && !item.text} />}
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
  return null;
});

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

function useNow(running: boolean) {
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    if (!running) return;
    const i = setInterval(() => setNow(Date.now() / 1000), 500);
    return () => clearInterval(i);
  }, [running]);
  return now;
}

const stepSeconds = (t: ToolItem, now: number) => t.seconds ?? Math.max(0, now - t.startedAt);

function ToolProgress({ p }: { p: NonNullable<ToolItem["progress"]> }) {
  return (
    <ProgressBar value={(100 * (p.frame || 0)) / (p.total || 1)} size="sm" aria-label="Render progress" className="px-2">
      <div className="mb-1 flex w-full justify-between text-xs tabular-nums text-muted">
        <span>{p.stage === "render" ? `Frame ${p.frame} of ${p.total}` : `${p.stage ?? "Working"} ${p.frame ?? ""}/${p.total}`}{p.fps ? ` · ${p.fps} fps` : ""}</span>
        <span>{p.eta_s != null ? `${Math.round(p.eta_s)} s left` : p.message ?? ""}</span>
      </div>
      <ProgressBar.Track><ProgressBar.Fill /></ProgressBar.Track>
    </ProgressBar>
  );
}

function StepImages({ t, onImage, small }: { t: ToolItem; onImage: (m: Media) => void; small?: boolean }) {
  if (!t.images.length && !t.audios.length) return null;
  return (
    <div className="flex flex-col gap-2 px-2 pb-2">
      {t.images.length > 0 && (
        <div className="flex gap-2 overflow-x-auto">
          {t.images.map((m) => (
            <button key={m.url} onClick={() => onImage(m)} aria-label={`Open ${m.label ?? m.path}`}
              className="checker shrink-0 overflow-hidden rounded-xl transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus">
              <img src={m.url} alt={m.label ?? ""} className={cn("max-w-[560px] object-contain", small ? "h-20" : "h-44")} loading="lazy" />
            </button>
          ))}
        </div>
      )}
      {!small && t.audios.map((a) => <AudioRow key={a.url} a={a} />)}
    </div>
  );
}

/** One quiet line: icon · name · short argument summary · time · status. Collapsed by default; errors open themselves. */
function ToolRow({ t, onImage, nested }: { t: ToolItem; onImage: (m: Media) => void; nested?: boolean }) {
  const [open, setOpen] = useState(false);
  const now = useNow(t.status === "running");
  useEffect(() => { if (t.status === "error") setOpen(true); }, [t.status]);
  const meta = TOOLS[t.name] ?? { icon: MagicWand, label: t.name };
  const Icon = meta.icon;
  const obj = useMemo(() => parseArgs(t.args), [t.args]);
  const secs = stepSeconds(t, now);
  const chars = t.ui?.chars;
  const diff: string | undefined = t.ui?.diff;
  const code = !diff && t.name === "write_file" && obj?.content ? String(obj.content) : null;
  const argText = obj ? JSON.stringify(obj, null, 2) : t.args;
  const summary = argSummary(t.name, obj);
  const outRef = useRef<HTMLPreElement>(null);
  useEffect(() => { if (outRef.current) outRef.current.scrollTop = outRef.current.scrollHeight; }, [t.output, open]);
  const running = t.status === "running";

  return (
    <div className={cn("rounded-xl", t.status === "error" && "bg-danger/[0.06]")} data-testid="tool-step">
      <Disclosure isExpanded={open} onExpandedChange={setOpen}>
        <Disclosure.Heading>
          <Button slot="trigger" variant="ghost" fullWidth className="h-9 justify-start gap-2.5 rounded-xl px-2 text-left font-normal">
            <Icon size={16} className={cn("shrink-0", running ? "text-accent-ink" : "text-muted")} />
            <span className={cn("shrink-0 text-[13px]", nested ? "text-muted" : "text-foreground")}>{running ? <Shimmer>{meta.label}</Shimmer> : meta.label}</span>
            {summary && <span className="min-w-0 flex-1 truncate font-mono text-xs text-muted">{summary}</span>}
            {!summary && <span className="flex-1" />}
            {chars != null && <Chip size="sm" variant="soft" color={t.ui?.cached ? "success" : "default"}>{t.ui?.cached ? "cached" : `${chars} chars`}</Chip>}
            {t.job && ["queued", "failed", "killed"].includes(t.job.status) && <Chip size="sm" variant="soft" color={t.job.status === "queued" ? "default" : "danger"}>{t.job.status}</Chip>}
            <span className="shrink-0 text-xs tabular-nums text-muted">{secs.toFixed(1)}s</span>
            <ToolStatus status={t.status} />
            <Disclosure.Indicator className="size-3.5 text-muted" />
          </Button>
        </Disclosure.Heading>
        {t.progress?.total ? <div className="pb-2"><ToolProgress p={t.progress} /></div> : null}
        <StepImages t={t} onImage={onImage} />
        <Disclosure.Content>
          <Disclosure.Body className="flex flex-col gap-3 px-2 pb-3 pl-9 pt-1">
            <div className="font-mono text-xs text-muted">{t.name}</div>
            {diff ? <DiffView diff={diff} /> : code ? <Block title={obj?.path}>{code}</Block> : argText && argText !== "{}" ? <Block title="Arguments">{argText}</Block> : null}
            {t.output && <Block title="Output" preRef={outRef}>{t.output.slice(-20000)}</Block>}
            {t.result && <Block title="Result" danger={t.status === "error"}>{t.result}</Block>}
          </Disclosure.Body>
        </Disclosure.Content>
      </Disclosure>
    </div>
  );
}

/** "N steps": consecutive tool calls folded into one row; the header names the running step, otherwise lists what ran. */
function StepGroup({ tools, onImage }: { tools: ToolItem[]; onImage: (m: Media) => void }) {
  const [open, setOpen] = useState(false);
  const running = tools.find((t) => t.status === "running");
  const errors = tools.filter((t) => t.status === "error").length;
  const now = useNow(!!running);
  useEffect(() => { if (errors) setOpen(true); }, [errors]);
  const label = (t: ToolItem) => (TOOLS[t.name] ?? { label: t.name }).label;
  const labels = useMemo(() => [...new Set(tools.map(label))], [tools]); // eslint-disable-line react-hooks/exhaustive-deps
  const total = tools.reduce((n, t) => n + stepSeconds(t, now), 0);
  const thumbs = tools.filter((t) => t.images.length);
  return (
    <div className="rounded-xl" data-testid="step-group">
      <button aria-expanded={open} onClick={() => setOpen(!open)}
        className="flex h-9 w-full items-center gap-2.5 rounded-xl px-2 text-left text-[13px] outline-none transition-colors hover:bg-surface-secondary focus-visible:ring-2 focus-visible:ring-focus">
        <Stack size={16} className={cn("shrink-0", running ? "text-accent-ink" : "text-muted")} />
        <span className="shrink-0 font-medium">{tools.length} steps</span>
        <span className="min-w-0 flex-1 truncate text-xs text-muted">{running ? <Shimmer>{label(running)}</Shimmer> : labels.join(" · ")}</span>
        {errors > 0 && <Chip size="sm" variant="soft" color="danger">{errors} failed</Chip>}
        <span className="shrink-0 text-xs tabular-nums text-muted">{total.toFixed(1)}s</span>
        {running ? <Spinner size="sm" /> : errors ? <XCircle size={16} weight="fill" className="shrink-0 text-danger-ink" /> : <CheckCircle size={16} weight="fill" className="shrink-0 text-success-ink" />}
        <CaretRight size={14} className={cn("shrink-0 text-muted transition-transform duration-150", open && "rotate-90")} />
      </button>
      {!open && running?.progress?.total ? <div className="pb-2"><ToolProgress p={running.progress} /></div> : null}
      {!open && thumbs.slice(-2).map((t) => <StepImages key={t.id} t={t} onImage={onImage} small />)}
      {open && (
        <div className="ml-[15px] mt-0.5 flex animate-rise flex-col border-l border-separator pl-1.5">
          {tools.map((t) => <ToolRow key={t.id} t={t} onImage={onImage} nested />)}
        </div>
      )}
    </div>
  );
}

function ToolStatus({ status }: { status: string }) {
  if (status === "running") return <Spinner size="sm" />;
  if (status === "success") return <CheckCircle size={16} weight="fill" className="shrink-0 text-success-ink" />;
  if (status === "cancelled") return <StopCircle size={16} weight="fill" className="shrink-0 text-muted" />;
  return <XCircle size={16} weight="fill" className="shrink-0 text-danger-ink" />;
}

const CLAMP_LINES = 12;

/** Long logs and diffs show their first lines and expand on demand. */
function Clamped({ text, children }: { text: string; children: (visible: string) => ReactNode }) {
  const lines = text.split("\n");
  const [all, setAll] = useState(false);
  const long = lines.length > CLAMP_LINES + 4;
  return (
    <div>
      {children(long && !all ? lines.slice(0, CLAMP_LINES).join("\n") : text)}
      {long && (
        <button onClick={() => setAll(!all)} className="mt-1 text-xs text-accent-ink hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus">
          {all ? "Show less" : `Show ${lines.length - CLAMP_LINES} more lines`}
        </button>
      )}
    </div>
  );
}

function Block({ title, children, danger, preRef }: { title?: string; children: string; danger?: boolean; preRef?: React.Ref<HTMLPreElement> }) {
  return (
    <div>
      {title && <div className="mb-1 text-xs font-medium text-muted">{title}</div>}
      <Clamped text={children}>
        {(visible) => (
          <pre ref={preRef} className={cn("max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-xl bg-surface-secondary p-3 font-mono text-xs leading-relaxed",
            danger ? "text-danger-ink" : "text-foreground/85")}>{visible}</pre>
        )}
      </Clamped>
    </div>
  );
}

function DiffView({ diff }: { diff: string }) {
  return (
    <Clamped text={diff}>
      {(visible) => (
        <pre className="max-h-72 overflow-auto rounded-xl bg-surface-secondary py-2 font-mono text-xs leading-relaxed">
          {visible.split("\n").map((l, i) => (
            <div key={i} className={cn("px-3",
              l.startsWith("+") && !l.startsWith("+++") ? "bg-success/10 text-success-ink" :
              l.startsWith("-") && !l.startsWith("---") ? "bg-danger/10 text-danger-ink" :
              l.startsWith("@@") ? "text-accent-ink" : "text-muted")}>{l || " "}</div>
          ))}
        </pre>
      )}
    </Clamped>
  );
}

function AskPanel({ q, onAnswer }: { q: { text: string; options: string[] }; onAnswer: (t: string) => void }) {
  return (
    <div className="mx-auto w-full max-w-[760px] px-5 pb-2">
      <div className="rounded-2xl border border-accent/30 bg-sunken p-4">
        <div className="flex items-center gap-2 text-sm font-medium text-accent-ink"><Question size={18} weight="fill" /> The director has a question</div>
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
      <CheckCircle size={20} weight="fill" className="text-success-ink" />
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
      <div className="mx-auto max-w-[760px] rounded-2xl border border-border bg-surface shadow-soft transition-[border-color,box-shadow] focus-within:border-accent/50 focus-within:shadow-[0_0_0_4px_color-mix(in_oklab,var(--accent)_16%,transparent)]">
        <TextArea
          aria-label="Message the director"
          rows={2}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); onSend(); }
          }}
          placeholder={hasRun ? (active ? "Add guidance. It's read before the next step…" : "Ask for changes…") : "Describe the film you want…"}
          className="max-h-60 min-h-[56px] w-full resize-none border-0 bg-transparent px-4 pt-3.5 text-[14px] shadow-none focus:ring-0 data-[focus-visible]:ring-0"
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

