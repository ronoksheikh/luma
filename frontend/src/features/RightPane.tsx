import { FitAddon } from "@xterm/addon-fit";
import { Terminal as XTerm } from "@xterm/xterm";
import {
  AudioLines, Check, ChevronLeft, ChevronRight, Download, FileArchive, FileVideo, Film, Hand, Images, Keyboard, Pause, Play, Repeat, ShieldCheck, SkipBack, TerminalSquare, X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Badge, Button, Empty, Switch, Tabs, TabsContent, TabsList, TabsTrigger, Tip, cn } from "../components/ui";
import { api, fmtBytes, type FileItem } from "../lib/api";
import type { Media, RunState } from "../lib/run";
import { useStore } from "../lib/store";
import { AudioRow, Lightbox } from "./Timeline";

export function RightPane({ run }: { run: RunState }) {
  const { rightTab, setRightTab, projectId } = useStore();
  const [files, setFiles] = useState<FileItem[]>([]);
  const loadFiles = useCallback(() => {
    if (!projectId) return;
    api<FileItem[]>(`/api/projects/${projectId}/files?dir=outputs`).then(setFiles).catch(() => setFiles([]));
  }, [projectId]);
  useEffect(() => { loadFiles(); }, [loadFiles, run.artifacts.length, run.status]);
  const qcCount = run.artifacts.filter((a) => a.kind === "qc").length;
  return (
    <aside className="flex h-full min-h-0 flex-col border-l border-line bg-panel/40">
      <Tabs value={rightTab} onValueChange={setRightTab} className="flex h-full min-h-0 flex-col">
        <TabsList>
          <TabsTrigger value="preview"><Film className="h-3.5 w-3.5" />Preview</TabsTrigger>
          <TabsTrigger value="terminal"><TerminalSquare className="h-3.5 w-3.5" />Terminal</TabsTrigger>
          <TabsTrigger value="gallery"><Images className="h-3.5 w-3.5" />Gallery{run.images.length ? <span className="text-faint">{run.images.length}</span> : null}</TabsTrigger>
          <TabsTrigger value="audio"><AudioLines className="h-3.5 w-3.5" />Audio{run.audios.length ? <span className="text-faint">{run.audios.length}</span> : null}</TabsTrigger>
          <TabsTrigger value="outputs"><Download className="h-3.5 w-3.5" />Outputs</TabsTrigger>
          <TabsTrigger value="qc"><ShieldCheck className="h-3.5 w-3.5" />QC{qcCount ? <span className="text-faint">{qcCount}</span> : null}</TabsTrigger>
        </TabsList>
        <TabsContent value="preview" className="overflow-y-auto"><PreviewTab run={run} files={files} /></TabsContent>
        <TabsContent value="terminal" forceMount className="flex flex-col"><TerminalTab visible={rightTab === "terminal"} /></TabsContent>
        <TabsContent value="gallery" className="overflow-y-auto"><GalleryTab images={run.images} /></TabsContent>
        <TabsContent value="audio" className="overflow-y-auto"><AudioTab audios={run.audios} /></TabsContent>
        <TabsContent value="outputs" className="overflow-y-auto"><OutputsTab files={files} reload={loadFiles} /></TabsContent>
        <TabsContent value="qc" className="overflow-y-auto"><QcTab run={run} /></TabsContent>
      </Tabs>
    </aside>
  );
}

// ------------------------------------------------------------------ Preview
function PreviewTab({ run, files }: { run: RunState; files: FileItem[] }) {
  const { projects, projectId } = useStore();
  const fps = projects.find((p) => p.id === projectId)?.settings.fps ?? 60;
  const videos = useMemo(() => {
    const seen = new Set<string>();
    const out: { url: string; label: string }[] = [];
    for (const f of files.filter((f) => f.ext === ".mp4" || f.ext === ".mov")) {
      if (!seen.has(f.url)) { seen.add(f.url); out.push({ url: f.url, label: f.path }); }
    }
    for (const a of [...run.artifacts].reverse()) {
      if (/\.(mp4|mov)$/.test(a.path) && !seen.has(a.url)) { seen.add(a.url); out.push({ url: a.url, label: a.path }); }
    }
    return out;
  }, [files, run.artifacts]);
  const [sel, setSel] = useState<string | null>(null);
  const src = sel && videos.find((v) => v.url === sel) ? sel : videos[0]?.url;
  const v = useRef<HTMLVideoElement>(null);
  const [loop, setLoop] = useState(true);
  const [playing, setPlaying] = useState(false);
  const [t, setT] = useState(0);
  const [dur, setDur] = useState(0);
  const [bust, setBust] = useState(0);
  useEffect(() => setBust(Date.now()), [run.artifacts.length]);

  const step = (n: number) => {
    const el = v.current;
    if (!el) return;
    el.pause();
    el.currentTime = Math.max(0, Math.min(el.duration || 0, Math.round(el.currentTime * fps + n) / fps + 1e-4));
  };
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest("input,textarea,[contenteditable],.xterm")) return;
      if (useStore.getState().rightTab !== "preview") return;
      if (e.key === ",") { step(-1); e.preventDefault(); }
      if (e.key === ".") { step(1); e.preventDefault(); }
      if (e.key === "k" || (e.key === " " && document.activeElement === document.body)) { const el = v.current; if (el) { if (el.paused) el.play(); else el.pause(); } e.preventDefault(); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  });

  if (!src) {
    const progressing = [...run.items].reverse().find((it) => it.kind === "tool" && it.progress?.total);
    return <Empty icon={<FileVideo className="h-8 w-8" />} title="Nothing rendered yet">{progressing ? "A render is in progress — it will appear here when encoded." : "Final renders and delivered MP4s appear here."}</Empty>;
  }
  const frame = Math.round(t * fps);
  return (
    <div className="space-y-3 p-3">
      <div className="checker overflow-hidden rounded-xl border border-line bg-black">
        <video ref={v} key={src} src={`${src}?v=${bust}`} className="aspect-video w-full bg-black" loop={loop} playsInline controls={false}
          onTimeUpdate={(e) => setT(e.currentTarget.currentTime)} onLoadedMetadata={(e) => setDur(e.currentTarget.duration)}
          onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onClick={(e) => (e.currentTarget.paused ? e.currentTarget.play() : e.currentTarget.pause())} />
      </div>
      <input type="range" min={0} max={dur || 0} step={1 / fps} value={t} aria-label="Seek"
        onChange={(e) => { if (v.current) v.current.currentTime = Number(e.target.value); }} className="w-full accent-[var(--color-accent)]" />
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1">
          <Tip content="Start"><Button size="iconSm" variant="ghost" aria-label="Go to start" onClick={() => { if (v.current) v.current.currentTime = 0; }}><SkipBack className="h-4 w-4" /></Button></Tip>
          <Tip content="Previous frame ( , )"><Button size="iconSm" variant="ghost" aria-label="Previous frame" onClick={() => step(-1)}><ChevronLeft className="h-4 w-4" /></Button></Tip>
          <Button size="icon" variant="primary" aria-label={playing ? "Pause" : "Play"} onClick={() => { const el = v.current; if (el) { if (el.paused) el.play(); else el.pause(); } }}>
            {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
          </Button>
          <Tip content="Next frame ( . )"><Button size="iconSm" variant="ghost" aria-label="Next frame" onClick={() => step(1)}><ChevronRight className="h-4 w-4" /></Button></Tip>
          <Tip content="Loop"><Button size="iconSm" variant="ghost" aria-label="Loop" className={cn(loop && "text-accent")} onClick={() => setLoop(!loop)}><Repeat className="h-4 w-4" /></Button></Tip>
        </div>
        <div className="font-mono text-[11px] tabular-nums text-muted">f {frame} / {Math.round(dur * fps)} · {t.toFixed(3)}s · {fps} fps</div>
      </div>
      {videos.length > 1 && (
        <div className="space-y-1">
          {videos.map((vv) => (
            <button key={vv.url} onClick={() => setSel(vv.url)} className={cn("flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs", vv.url === src ? "bg-raised text-fg" : "text-muted hover:bg-raised/60")}>
              <FileVideo className="h-3.5 w-3.5" /><span className="truncate font-mono">{vv.label}</span>
            </button>
          ))}
        </div>
      )}
      <div className="flex items-center gap-1.5 text-[10px] text-faint"><Keyboard className="h-3 w-3" /> <span className="kbd">,</span> <span className="kbd">.</span> step a frame · <span className="kbd">k</span> play/pause</div>
    </div>
  );
}

// ------------------------------------------------------------------ Terminal
function TerminalTab({ visible }: { visible: boolean }) {
  const { projectId } = useStore();
  const host = useRef<HTMLDivElement>(null);
  const term = useRef<XTerm | null>(null);
  const fit = useRef<FitAddon | null>(null);
  const ws = useRef<WebSocket | null>(null);
  const [takeover, setTakeover] = useState(false);
  const [status, setStatus] = useState<{ alive?: boolean; busy?: boolean }>({});
  const [connected, setConnected] = useState(false);
  const takeoverRef = useRef(false);
  takeoverRef.current = takeover;

  useEffect(() => {
    if (!host.current) return;
    const t = new XTerm({
      fontFamily: '"JetBrains Mono Variable", ui-monospace, monospace', fontSize: 12, lineHeight: 1.25, cursorBlink: true, convertEol: false,
      scrollback: 5000, allowProposedApi: false,
      theme: { background: "#0a0b0f", foreground: "#d8dbe3", cursor: "#ffb547", selectionBackground: "#ffb54744", black: "#1b1f2a", brightBlack: "#5d6478" },
    });
    const f = new FitAddon();
    t.loadAddon(f);
    t.open(host.current);
    term.current = t;
    fit.current = f;
    t.onData((d) => { if (takeoverRef.current && ws.current?.readyState === 1) ws.current.send(JSON.stringify({ type: "input", data: d })); });
    const ro = new ResizeObserver(() => {
      try {
        f.fit();
        if (ws.current?.readyState === 1) ws.current.send(JSON.stringify({ type: "resize", cols: t.cols, rows: t.rows }));
      } catch { /* hidden */ }
    });
    ro.observe(host.current);
    return () => { ro.disconnect(); t.dispose(); };
  }, []);

  useEffect(() => {
    if (!projectId || !term.current) return;
    let closed = false;
    let retry: any;
    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const sock = new WebSocket(`${proto}://${location.host}/ws/terminal/${projectId}`);
      ws.current = sock;
      term.current!.reset();
      sock.onopen = () => {
        setConnected(true);
        try { fit.current?.fit(); } catch { /* */ }
        sock.send(JSON.stringify({ type: "resize", cols: term.current!.cols, rows: term.current!.rows }));
        sock.send(JSON.stringify({ type: "takeover", on: takeoverRef.current }));
      };
      sock.onmessage = (e) => {
        const m = JSON.parse(e.data);
        if (m.type === "output") term.current!.write(m.data);
        else if (m.type === "status") { setStatus(m); setTakeover(!!m.takeover); }
      };
      sock.onclose = () => { setConnected(false); if (!closed) retry = setTimeout(connect, 1500); };
    };
    connect();
    return () => { closed = true; clearTimeout(retry); ws.current?.close(); };
  }, [projectId]);

  useEffect(() => { if (visible) setTimeout(() => { try { fit.current?.fit(); } catch { /* */ } }, 30); }, [visible]);

  const toggle = (on: boolean) => {
    setTakeover(on);
    ws.current?.send(JSON.stringify({ type: "takeover", on }));
    if (on) term.current?.focus();
  };
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between border-b border-line px-3 py-1.5 text-xs">
        <span className="flex items-center gap-2 text-muted">
          <span className={cn("h-1.5 w-1.5 rounded-full", connected ? (status.busy ? "animate-pulse-soft bg-accent" : "bg-ok") : "bg-bad")} />
          {connected ? (status.busy ? "agent command running" : "live mirror of the agent's shell") : "connecting…"}
        </span>
        <label className="flex items-center gap-2 text-muted">
          <Hand className="h-3.5 w-3.5" /> Take over
          <Switch checked={takeover} onCheckedChange={toggle} label="Take over the terminal" />
        </label>
      </div>
      <div ref={host} className={cn("min-h-0 flex-1 bg-bg", takeover && "ring-1 ring-inset ring-accent/40")} onClick={() => takeover && term.current?.focus()} />
      {takeover && <div className="border-t border-line px-3 py-1 text-[10px] text-accent">You are typing into the sandbox shell. The agent's next command still runs here.</div>}
    </div>
  );
}

// ------------------------------------------------------------------ Gallery / Audio
function GalleryTab({ images }: { images: Media[] }) {
  const [lb, setLb] = useState<Media | null>(null);
  if (!images.length) return <Empty icon={<Images className="h-8 w-8" />} title="No images yet">Contact sheets, spectrograms and inspected frames collect here.</Empty>;
  return (
    <div className="grid grid-cols-1 gap-3 p-3 xl:grid-cols-2">
      {[...images].reverse().map((m) => (
        <button key={m.url + m.ts} onClick={() => setLb(m)} className="overflow-hidden rounded-xl border border-line bg-bg text-left hover:border-accent/50">
          <img src={m.url} alt={m.label ?? ""} className="w-full" loading="lazy" />
          <div className="flex items-center justify-between px-2.5 py-1.5 text-[11px] text-muted"><span className="truncate">{m.label ?? m.path}</span>{m.kind && <Badge>{m.kind.replace("_", " ")}</Badge>}</div>
        </button>
      ))}
      <Lightbox media={lb} onClose={() => setLb(null)} />
    </div>
  );
}

function AudioTab({ audios }: { audios: Media[] }) {
  if (!audios.length) return <Empty icon={<AudioLines className="h-8 w-8" />} title="No audio yet">Voice-over, SFX, music and the final mix appear here with their script text and character cost.</Empty>;
  const groups: Record<string, Media[]> = {};
  audios.forEach((a) => { (groups[a.kind || "audio"] ||= []).push(a); });
  const order = ["mix", "voice", "sfx", "music", "voice_preview", "audio"];
  return (
    <div className="space-y-4 p-3">
      {Object.keys(groups).sort((a, b) => order.indexOf(a) - order.indexOf(b)).map((k) => (
        <div key={k}>
          <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-faint">{k === "mix" ? "Final mix" : k.replace("_", " ")}</div>
          <div className="space-y-2">{groups[k].map((a) => <AudioRow key={a.url + a.ts} a={a} />)}</div>
        </div>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ Outputs
function OutputsTab({ files, reload }: { files: FileItem[]; reload: () => void }) {
  if (!files.length) return <Empty icon={<Download className="h-8 w-8" />} title="No deliverables yet">MP4, ProRes, end card, SRT and the scene-source zip appear here.</Empty>;
  const icon = (ext: string) => (ext === ".mp4" || ext === ".mov" ? <FileVideo className="h-4 w-4" /> : ext === ".zip" ? <FileArchive className="h-4 w-4" /> : ext === ".png" ? <Images className="h-4 w-4" /> : <Download className="h-4 w-4" />);
  return (
    <div className="p-3">
      <div className="mb-2 flex justify-end"><Button size="sm" variant="ghost" onClick={reload}>Refresh</Button></div>
      <div className="divide-y divide-line rounded-xl border border-line">
        {files.map((f) => (
          <div key={f.path} className="flex items-center gap-3 px-3 py-2">
            <span className="text-muted">{icon(f.ext)}</span>
            <div className="min-w-0 flex-1">
              <div className="truncate font-mono text-xs">{f.path.replace(/^outputs\//, "")}</div>
              <div className="text-[11px] text-faint">{fmtBytes(f.size)} · {new Date(f.mtime * 1000).toLocaleTimeString()}</div>
            </div>
            {f.ext === ".png" && <a href={f.url} target="_blank" rel="noreferrer"><img src={f.url} alt="" className="h-9 rounded border border-line" /></a>}
            <a href={`${f.url}?download=true`} download className="inline-flex h-7 items-center gap-1 rounded-lg border border-line-strong px-2.5 text-xs hover:bg-raised"><Download className="h-3.5 w-3.5" /> Download</a>
          </div>
        ))}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ QC
function QcTab({ run }: { run: RunState }) {
  const reports = run.artifacts.filter((a) => a.kind === "qc");
  const latest = reports[reports.length - 1];
  const [rep, setRep] = useState<any>(null);
  useEffect(() => {
    if (!latest) { setRep(null); return; }
    fetch(`${latest.url}?v=${latest.ts}`).then((r) => r.json()).then(setRep).catch(() => setRep(null));
  }, [latest?.url, latest?.ts]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!latest) return <Empty icon={<ShieldCheck className="h-8 w-8" />} title="No QC report yet">Frame count, loudness, true peak, final-frame match and A/V sync checks show up here.</Empty>;
  if (!rep) return <Empty title="Loading report…" />;
  return (
    <div className="space-y-3 p-3">
      <div className={cn("flex items-center justify-between rounded-xl border px-3 py-2.5", rep.pass ? "border-ok/30 bg-ok/10 text-ok" : "border-bad/30 bg-bad/10 text-bad")}>
        <span className="flex items-center gap-2 text-sm font-semibold">{rep.pass ? <Check className="h-4 w-4" /> : <X className="h-4 w-4" />} {rep.pass ? "All checks passed" : "QC failed"}</span>
        <span className="truncate font-mono text-[11px] opacity-80">{latest.path}</span>
      </div>
      <div className="divide-y divide-line rounded-xl border border-line">
        {rep.checks.map((c: any) => (
          <div key={c.name} className="flex items-start gap-2.5 px-3 py-2">
            <span className={cn("mt-0.5 flex h-4 w-4 items-center justify-center rounded-full", c.pass ? "bg-ok/20 text-ok" : c.severity === "warning" ? "bg-warn/20 text-warn" : "bg-bad/20 text-bad")}>
              {c.pass ? <Check className="h-3 w-3" /> : <X className="h-3 w-3" />}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex justify-between gap-2 text-xs"><span className="font-medium">{c.name.replaceAll("_", " ")}</span><span className="text-faint">{c.expected != null ? `expected ${typeof c.expected === "object" ? JSON.stringify(c.expected) : c.expected}` : ""}</span></div>
              <div className="break-words font-mono text-[11px] text-muted">{typeof c.value === "object" ? JSON.stringify(c.value).slice(0, 400) : String(c.value)}</div>
              {c.detail && <div className="text-[11px] text-faint">{c.detail}</div>}
            </div>
          </div>
        ))}
      </div>
      {rep.stats?.video && <pre className="overflow-auto rounded-xl border border-line bg-bg p-2.5 font-mono text-[10px] text-faint">{JSON.stringify(rep.stats.video, null, 1)}</pre>}
    </div>
  );
}
