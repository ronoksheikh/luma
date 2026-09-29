import { Button, Chip, Dropdown, EmptyState, Label, Spinner, Tabs } from "@heroui/react";
import {
  ArrowClockwise, Brain, CheckCircle, DotsThree, DownloadSimple, FileArchive, FileText, FileVideo, FilmSlate, Files, GitCommit, Image as ImageIcon, ListChecks,
  ShieldCheck, Stack, TerminalWindow, Toolbox as ToolboxIcon, Waveform, WarningCircle, XCircle, type Icon,
} from "@phosphor-icons/react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, fmtBytes, type FileItem } from "../lib/api";
import type { Media, RunState } from "../lib/run";
import { useStore } from "../lib/store";
import { SkeletonRows } from "../ui/ai";
import { cn } from "../ui/kit";
import { AudioRow, Lightbox } from "../ui/media";
import { VideoPlayer } from "../ui/player";
import { AssetsPanel } from "./Assets";
import { ArtifactsPanel, CheckpointsPanel, MemoryPanel, PlanPanel } from "./LongPanels";
import { TerminalPanel } from "./Terminal";
import { ToolboxPanel } from "./Toolbox";

export function Inspector({ run }: { run: RunState }) {
  const { rightTab, setRightTab, projectId, projects } = useStore();
  const [files, setFiles] = useState<FileItem[]>([]);
  const assetCount = projects.find((p) => p.id === projectId)?.asset_count ?? 0;
  const loadFiles = useCallback(() => {
    if (!projectId) return;
    api<FileItem[]>(`/api/projects/${projectId}/files?dir=outputs`).then(setFiles).catch(() => setFiles([]));
  }, [projectId]);
  useEffect(() => { loadFiles(); }, [loadFiles, run.artifacts.length, run.status]);
  const mediaCount = run.images.length + run.audios.length;
  const qc = run.artifacts.filter((a) => a.kind === "qc").length;

  type TabDef = { id: string; label: string; icon: Icon; badge?: string | number };
  const primary: TabDef[] = [
    { id: "preview", label: "Preview", icon: FilmSlate },
    { id: "plan", label: "Plan", icon: ListChecks, badge: run.plan.total ? `${run.plan.done}/${run.plan.total}` : undefined },
    { id: "artifacts", label: "Artifacts", icon: Stack },
    { id: "toolbox", label: "Toolbox", icon: ToolboxIcon },
    { id: "terminal", label: "Terminal", icon: TerminalWindow },
    { id: "assets", label: "Assets", icon: Files, badge: assetCount || undefined },
  ];
  const more: TabDef[] = [
    { id: "memory", label: "Memory", icon: Brain },
    { id: "checkpoints", label: "Checkpoints", icon: GitCommit },
    { id: "media", label: "Media", icon: ImageIcon, badge: mediaCount || undefined },
    { id: "outputs", label: "Files", icon: DownloadSimple, badge: files.length || undefined },
    { id: "qc", label: "QC", icon: ShieldCheck, badge: qc || undefined },
  ];
  const inMore = more.some((m) => m.id === rightTab);
  return (
    <Tabs variant="secondary" selectedKey={rightTab} onSelectionChange={(k) => setRightTab(String(k))} className="flex h-full min-h-0 flex-col">
      {/* Selected tab shows icon + label; the others are icons (with tooltips) until the pane is wide enough for every label. */}
      <div className="@container flex h-12 shrink-0 items-end pl-2 pr-1.5">
      <Tabs.ListContainer className="min-w-0 flex-1 bg-transparent">
        <Tabs.List aria-label="Inspector">
          {[...primary, ...more].map(({ id, label, icon: I, badge }) => (
            <Tabs.Tab key={id} id={id} aria-label={label}
              className={cn("group h-10 w-auto flex-none grow-0 gap-1.5 whitespace-nowrap px-3", more.some((m) => m.id === id) && id !== rightTab && "hidden")}>
              <span title={label} className="inline-flex shrink-0"><I size={16} /></span>
              <span className="hidden group-data-[selected=true]:inline @[640px]:inline">{label}</span>
              {badge ? <span className="text-xs tabular-nums text-muted">{badge}</span> : null}
              <Tabs.Indicator />
            </Tabs.Tab>
          ))}
        </Tabs.List>
      </Tabs.ListContainer>
        <Dropdown>
          <Button size="sm" variant="ghost" className={cn("mb-1.5 shrink-0 text-muted", inMore && "text-foreground")} aria-label="More panels">
            <DotsThree size={18} weight="bold" />
          </Button>
          <Dropdown.Popover placement="bottom end">
            <Dropdown.Menu onAction={(k) => setRightTab(String(k))}>
              {more.map(({ id, label, icon: I, badge }) => (
                <Dropdown.Item key={id} id={id} textValue={label}>
                  <I size={16} className="text-muted" /><Label>{label}</Label>
                  {badge ? <span className="ms-auto text-xs tabular-nums text-muted">{badge}</span> : null}
                </Dropdown.Item>
              ))}
            </Dropdown.Menu>
          </Dropdown.Popover>
        </Dropdown>
      </div>
      <Tabs.Panel id="preview" className="min-h-0 flex-1 overflow-y-auto p-0"><PreviewPanel run={run} files={files} /></Tabs.Panel>
      <Tabs.Panel id="plan" className="min-h-0 flex-1 overflow-y-auto p-0"><PlanPanel run={run} /></Tabs.Panel>
      <Tabs.Panel id="artifacts" className="min-h-0 flex-1 overflow-y-auto p-0"><ArtifactsPanel version={run.versions.artifacts} /></Tabs.Panel>
      <Tabs.Panel id="toolbox" className="min-h-0 flex-1 overflow-hidden p-0"><ToolboxPanel version={run.versions.toolbox} /></Tabs.Panel>
      <Tabs.Panel id="assets" className="min-h-0 flex-1 overflow-y-auto p-0"><AssetsPanel /></Tabs.Panel>
      <Tabs.Panel id="terminal" shouldForceMount className="min-h-0 flex-1 p-0 [&[inert]]:hidden"><TerminalPanel visible={rightTab === "terminal"} /></Tabs.Panel>
      <Tabs.Panel id="memory" className="min-h-0 flex-1 overflow-y-auto p-0"><MemoryPanel version={run.versions.memory} /></Tabs.Panel>
      <Tabs.Panel id="checkpoints" className="min-h-0 flex-1 overflow-y-auto p-0"><CheckpointsPanel version={run.versions.checkpoints} /></Tabs.Panel>
      <Tabs.Panel id="media" className="min-h-0 flex-1 overflow-y-auto p-0"><MediaPanel images={run.images} audios={run.audios} /></Tabs.Panel>
      <Tabs.Panel id="outputs" className="min-h-0 flex-1 overflow-y-auto p-0"><FilesPanel files={files} reload={loadFiles} /></Tabs.Panel>
      <Tabs.Panel id="qc" className="min-h-0 flex-1 overflow-y-auto p-0"><QcPanel run={run} /></Tabs.Panel>
    </Tabs>
  );
}

function Empty({ icon, title, children }: { icon: React.ReactNode; title: string; children?: React.ReactNode }) {
  return (
    <EmptyState className="flex h-full min-h-[320px] flex-col items-center justify-center gap-3 px-8 text-center">
      <span className="flex size-12 items-center justify-center rounded-2xl bg-sunken text-accent-ink">{icon}</span>
      <div className="text-[16px] font-medium text-foreground">{title}</div>
      {children && <p className="max-w-[320px] text-sm text-muted">{children}</p>}
    </EmptyState>
  );
}

// ------------------------------------------------------------------ Preview
function PreviewPanel({ run, files }: { run: RunState; files: FileItem[] }) {
  const { projects, projectId } = useStore();
  const fps = projects.find((p) => p.id === projectId)?.settings.fps ?? 60;
  const videos = useMemo(() => {
    const seen = new Set<string>();
    const out: { url: string; label: string }[] = [];
    for (const f of files.filter((f) => f.ext === ".mp4" || f.ext === ".mov")) {
      if (!seen.has(f.url)) { seen.add(f.url); out.push({ url: f.url, label: f.path.replace(/^outputs\//, "") }); }
    }
    for (const a of [...run.artifacts].reverse()) {
      if (/\.(mp4|mov)$/.test(a.path) && !seen.has(a.url)) { seen.add(a.url); out.push({ url: a.url, label: a.path }); }
    }
    return out;
  }, [files, run.artifacts]);
  const [sel, setSel] = useState<string | null>(null);
  const src = sel && videos.find((v) => v.url === sel) ? sel : videos[0]?.url;
  const poster = files.find((f) => f.ext === ".png" && /end_card/.test(f.path))?.url;
  const [bust, setBust] = useState(0);
  useEffect(() => setBust(Date.now()), [run.artifacts.length]);
  const chapters = useMemo(() => {
    const card = [...run.items].reverse().find((it) => it.kind === "card" && it.card === "video" && src && it.artifact.url === src);
    return card && card.kind === "card" ? card.artifact.meta?.chapters || [] : [];
  }, [run.items, src]);

  if (!src) {
    const rendering = [...run.items].reverse().find((it) => it.kind === "tool" && it.status === "running" && it.progress?.total);
    return (
      <Empty icon={rendering ? <Spinner size="sm" /> : <FilmSlate size={24} />} title={rendering ? "Rendering…" : "Nothing rendered yet"}>
        {rendering ? "The film appears here as soon as it's encoded." : "Finished films play here with frame-accurate controls."}
      </Empty>
    );
  }
  return (
    <div className="flex flex-col gap-4 p-4">
      <VideoPlayer key={src} src={`${src}?v=${bust}`} poster={poster ? `${poster}?v=${bust}` : undefined} fps={fps} chapters={chapters} downloadName={src.split("/").pop()} />
      {videos.length > 1 && (
        <div className="flex flex-col gap-1">
          {videos.map((vv) => (
            <button key={vv.url} onClick={() => setSel(vv.url)}
              className={cn("flex items-center gap-2 rounded-xl px-3 py-2 text-left text-sm transition-colors", vv.url === src ? "bg-sunken text-foreground" : "text-muted hover:bg-surface-secondary")}>
              <FileVideo size={16} /><span className="truncate font-mono text-xs">{vv.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ Media
function MediaPanel({ images, audios }: { images: Media[]; audios: Media[] }) {
  const [lb, setLb] = useState<Media | null>(null);
  if (!images.length && !audios.length) {
    return <Empty icon={<ImageIcon size={24} />} title="No media yet">Preview frames, contact sheets, voice-over, sound effects and music collect here.</Empty>;
  }
  const groups: Record<string, Media[]> = {};
  audios.forEach((a) => { (groups[a.kind || "audio"] ||= []).push(a); });
  const order = ["mix", "voice", "sfx", "music", "voice_preview", "audio"];
  const title: Record<string, string> = { mix: "Final mix", voice: "Voice-over", sfx: "Sound effects", music: "Music", voice_preview: "Voice previews", audio: "Audio" };
  return (
    <div className="flex flex-col gap-6 p-4">
      {Object.keys(groups).sort((a, b) => order.indexOf(a) - order.indexOf(b)).map((k) => (
        <section key={k}>
          <h3 className="mb-2 flex items-center gap-2 text-sm"><Waveform size={16} className="text-muted" />{title[k] ?? k}</h3>
          <div className="flex flex-col gap-2">{groups[k].map((a) => <AudioRow key={a.url + a.ts} a={a} />)}</div>
        </section>
      ))}
      {images.length > 0 && (
        <section>
          <h3 className="mb-2 flex items-center gap-2 text-sm"><ImageIcon size={16} className="text-muted" />Frames</h3>
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
            {[...images].reverse().map((m) => (
              <button key={m.url + m.ts} onClick={() => setLb(m)} className="overflow-hidden rounded-xl border border-border bg-surface text-left transition-colors hover:border-accent/40">
                <div className="checker"><img src={m.url} alt={m.label ?? ""} className="w-full" loading="lazy" /></div>
                <div className="flex items-center justify-between gap-2 border-t border-separator px-3 py-2 text-xs">
                  <span className="truncate">{m.label ?? m.path}</span>
                  {m.kind && <span className="shrink-0 text-muted">{m.kind.replace("_", " ")}</span>}
                </div>
              </button>
            ))}
          </div>
        </section>
      )}
      <Lightbox media={lb} onClose={() => setLb(null)} />
    </div>
  );
}

// ------------------------------------------------------------------ Files
function FilesPanel({ files, reload }: { files: FileItem[]; reload: () => void }) {
  if (!files.length) return <Empty icon={<DownloadSimple size={24} />} title="No deliverables yet">MP4, ProRes, end card, captions and the scene source appear here when the film is delivered.</Empty>;
  const icon = (ext: string) => (ext === ".mp4" || ext === ".mov" ? FileVideo : ext === ".zip" ? FileArchive : ext === ".png" ? ImageIcon : FileText);
  return (
    <div className="flex flex-col gap-3 p-4">
      <div className="flex items-center justify-between">
        <span className="text-sm text-muted">{files.length} file{files.length > 1 ? "s" : ""}</span>
        <Button size="sm" variant="ghost" onPress={reload}><ArrowClockwise size={14} /> Refresh</Button>
      </div>
      <ul className="divide-y divide-separator overflow-hidden rounded-2xl border border-border bg-surface">
        {files.map((f) => {
          const Icon = icon(f.ext);
          return (
            <li key={f.path} className="flex items-center gap-3 px-3.5 py-2.5">
              <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-surface-secondary text-muted"><Icon size={18} /></span>
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm">{f.path.replace(/^outputs\//, "")}</div>
                <div className="text-xs tabular-nums text-muted">{fmtBytes(f.size)} · {new Date(f.mtime * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}</div>
              </div>
              <a href={`${f.url}?download=true`} download aria-label={`Download ${f.path}`}
                className="inline-flex h-8 items-center gap-1.5 rounded-full border border-border px-3 text-xs font-medium transition-colors hover:bg-surface-secondary">
                <DownloadSimple size={14} /> Download
              </a>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ------------------------------------------------------------------ QC
const QC_LABEL: Record<string, string> = {
  fps: "Frame rate", frame_count: "Frame count", av_sync_events: "A/V sync on impacts", audio_true_peak: "Audio true peak",
  audio_loudness: "Audio loudness", audio_length_matches_video: "Audio length matches video", first_frame_black: "First frame is black",
};
function qcValue(v: any): string {
  if (Array.isArray(v) && v.every((x) => x && typeof x === "object" && "offset_ms" in x)) {
    return v.map((x) => `${x.name} ${x.offset_ms == null ? "—" : `${x.offset_ms > 0 ? "+" : ""}${x.offset_ms} ms`}`).join(" · ");
  }
  return typeof v === "object" ? JSON.stringify(v).slice(0, 400) : String(v);
}

function QcPanel({ run }: { run: RunState }) {
  const reports = run.artifacts.filter((a) => a.kind === "qc");
  const latest = reports[reports.length - 1];
  const [rep, setRep] = useState<any>(null);
  useEffect(() => {
    if (!latest) { setRep(null); return; }
    fetch(`${latest.url}?v=${latest.ts}`, { credentials: "same-origin" }).then((r) => r.json()).then(setRep).catch(() => setRep(null));
  }, [latest?.url, latest?.ts]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!latest) return <Empty icon={<ShieldCheck size={24} />} title="No quality report yet">Frame count, loudness, true peak, final-frame match and A/V sync are checked before delivery.</Empty>;
  if (!rep) return <SkeletonRows rows={5} />;
  const failed = rep.checks.filter((c: any) => !c.pass).length;
  return (
    <div className="flex flex-col gap-4 p-4">
      <div className={cn("flex items-center gap-3 rounded-2xl px-4 py-3", rep.pass ? "bg-success/8 text-success-ink" : "bg-danger/8 text-danger-ink")}>
        {rep.pass ? <CheckCircle size={22} weight="fill" /> : <XCircle size={22} weight="fill" />}
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium">{rep.pass ? "All checks passed" : `${failed} check${failed > 1 ? "s" : ""} failed`}</div>
          <div className="truncate font-mono text-xs opacity-70">{latest.path}</div>
        </div>
        <Chip size="sm" variant="soft" color={rep.pass ? "success" : "danger"}>{rep.checks.length - failed} / {rep.checks.length}</Chip>
      </div>
      <ul className="divide-y divide-separator overflow-hidden rounded-2xl border border-border bg-surface">
        {rep.checks.map((c: any) => (
          <li key={c.name} className="flex items-start gap-3 px-3.5 py-2.5">
            {c.pass ? <CheckCircle size={18} weight="fill" className="mt-px shrink-0 text-success-ink" />
              : c.severity === "warning" ? <WarningCircle size={18} weight="fill" className="mt-px shrink-0 text-warning-ink" />
              : <XCircle size={18} weight="fill" className="mt-px shrink-0 text-danger-ink" />}
            <div className="min-w-0 flex-1">
              <div className="flex justify-between gap-2 text-sm">
                <span>{QC_LABEL[c.name] ?? c.name.replaceAll("_", " ").replace(/^./, (m: string) => m.toUpperCase())}</span>
                {c.expected != null && <span className="shrink-0 text-xs text-muted">expected {typeof c.expected === "object" ? JSON.stringify(c.expected) : c.expected}</span>}
              </div>
              <div className="break-words font-mono text-xs text-muted">{qcValue(c.value)}</div>
              {c.detail && <div className="text-xs text-muted">{c.detail}</div>}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
