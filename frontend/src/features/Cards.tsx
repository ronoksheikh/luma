// Inline cards rendered in the director timeline: presentations, requests (questions / approvals /
// options) and sub-agents.
import { Button, Chip, Spinner, TextArea } from "@heroui/react";
import {
  CheckCircle, Code, DownloadSimple, File, FileArchive, FileText, FilmStrip, Image as ImageIcon, Palette, Question, Robot, SealCheck, Star,
  Table as TableIcon, TextAa, Timer, Waveform, XCircle,
} from "@phosphor-icons/react";
import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import css from "highlight.js/lib/languages/css";
import ini from "highlight.js/lib/languages/ini";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import markdown from "highlight.js/lib/languages/markdown";
import python from "highlight.js/lib/languages/python";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";
import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, fmtBytes } from "../lib/api";
import { ACTIVE, useRunEvents, type Artifact, type Item, type Req } from "../lib/run";
import { useStore } from "../lib/store";
import { cn } from "../ui/kit";
import { ComparisonView, VideoPlayer, WaveformPlayer } from "../ui/player";

for (const [n, l] of Object.entries({ bash, css, ini, javascript, json, markdown, python, typescript, xml, yaml })) hljs.registerLanguage(n, l);

const CARD_ICON: Record<string, any> = {
  video: FilmStrip, image: ImageIcon, audio: Waveform, file: File, comparison: SealCheck, storyboard: FilmStrip, timeline: Timer, code: Code,
  table: TableIcon, palette: Palette, grid: ImageIcon, font: TextAa,
};

export function CardShell({ art, children, icon, subtitle, right }: { art: Artifact; children: React.ReactNode; icon?: any; subtitle?: React.ReactNode; right?: React.ReactNode }) {
  const Icon = icon || CARD_ICON[art.type] || File;
  return (
    <div className="overflow-hidden rounded-2xl border border-border bg-surface" data-testid={`card-${art.type}`}>
      <div className="flex items-center gap-2.5 border-b border-separator px-4 py-2.5">
        <span className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-offwhite text-accent"><Icon size={16} /></span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13.5px] font-medium">{art.title}</div>
          {subtitle && <div className="truncate text-xs text-muted">{subtitle}</div>}
        </div>
        {art.version > 1 && <Chip size="sm" variant="soft" color="accent">v{art.version}</Chip>}
        {right}
      </div>
      <div className="p-3.5">{children}</div>
    </div>
  );
}

export function PresentCard({ card, art }: { card: string; art: Artifact }) {
  const m = art.meta || {};
  switch (card) {
    case "video":
      return (
        <CardShell art={art} subtitle={`${m.width}×${m.height} · ${m.fps} fps · ${Number(m.duration).toFixed(2)} s · ${m.frames} frames${m.caption ? ` · ${m.caption}` : ""}`}>
          <VideoPlayer src={art.url!} poster={m.poster_url} fps={m.fps || 60} chapters={m.chapters || []} loop={m.loop !== false}
            downloadName={art.path?.split("/").pop()} />
        </CardShell>
      );
    case "image":
    case "grid":
      return <ImageCard art={art} />;
    case "audio":
      return (
        <CardShell art={art} subtitle={[m.duration && `${Number(m.duration).toFixed(2)} s`, m.lufs != null && `${m.lufs} LUFS`, m.true_peak_dbtp != null && `${m.true_peak_dbtp} dBTP`].filter(Boolean).join(" · ")}>
          <WaveformPlayer src={art.url!} peaks={m.peaks || []} duration={m.duration} words={m.words} transcript={m.transcript} />
        </CardShell>
      );
    case "file":
    case "font":
      return <FileCard art={art} />;
    case "comparison":
      return (
        <CardShell art={art} subtitle={`${m.labels?.[0]} vs ${m.labels?.[1]}`}>
          <ComparisonView a={m.a} b={m.b} mode={m.mode} labels={m.labels} />
        </CardShell>
      );
    case "storyboard":
      return <StoryboardCard art={art} />;
    case "timeline":
      return <TimelineCard art={art} />;
    case "code":
      return <CodeCard art={art} />;
    case "table":
      return (
        <CardShell art={art} subtitle={`${m.rows?.length ?? 0} rows`}>
          <DataTable columns={m.columns || []} rows={m.rows || []} />
        </CardShell>
      );
    case "palette":
      return (
        <CardShell art={art} subtitle={`${m.colors?.length ?? 0} colours`}>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {(m.colors || []).map((c: any) => (
              <div key={c.hex + c.name} className="overflow-hidden rounded-xl border border-border">
                <div className="h-16" style={{ background: c.hex }} />
                <div className="px-2.5 py-2">
                  <div className="text-sm font-medium">{c.name || c.hex}</div>
                  <div className="font-mono text-xs text-muted">{c.hex}</div>
                  {c.usage && <div className="mt-0.5 text-xs text-muted">{c.usage}</div>}
                  <div className="mt-1 flex gap-2 text-[10.5px] tabular-nums text-muted"><span>on white {c.contrast_white}:1</span><span>on black {c.contrast_black}:1</span></div>
                </div>
              </div>
            ))}
          </div>
        </CardShell>
      );
    default:
      return <CardShell art={art}><p className="text-sm text-muted">{art.path}</p></CardShell>;
  }
}

function ImageCard({ art }: { art: Artifact }) {
  const m = art.meta || {};
  const imgs: any[] = m.images || (m.cells || []).filter((c: any) => c.path).map((c: any) => ({ ...c, label: `${c.label}${c.qc_pass != null ? (c.qc_pass ? " · QC ✓" : " · QC ✗") : ""}` }));
  const [i, setI] = useState(0);
  const layout = art.type === "grid" ? "grid" : m.layout || "single";
  if (layout === "carousel" && imgs.length) {
    const cur = imgs[i % imgs.length];
    return (
      <CardShell art={art} subtitle={m.caption || `${i + 1} / ${imgs.length}`}>
        <div className="checker overflow-hidden rounded-xl"><img src={cur.url} alt="" className="max-h-[420px] w-full object-contain" /></div>
        <div className="mt-2 flex justify-center gap-1.5">
          {imgs.map((_, k) => <button key={k} aria-label={`Image ${k + 1}`} onClick={() => setI(k)} className={cn("size-2 rounded-full", k === i ? "bg-accent" : "bg-surface-tertiary")} />)}
        </div>
      </CardShell>
    );
  }
  return (
    <CardShell art={art} subtitle={m.caption || (art.type === "grid" ? `${imgs.length} variants` : undefined)}>
      <div className={cn("grid gap-2", imgs.length > 1 ? "grid-cols-2" : "grid-cols-1")}>
        {imgs.map((im, k) => (
          <figure key={k} className="overflow-hidden rounded-xl border border-border">
            {im.kind === "video" ? <video src={im.url} controls loop playsInline className="aspect-video w-full bg-night" />
              : <a href={im.url} target="_blank" rel="noreferrer" className="checker block"><img src={im.url} alt="" className="max-h-[420px] w-full object-contain" loading="lazy" /></a>}
            {(im.label || im.set) && <figcaption className="px-2.5 py-1.5 text-xs text-muted">{im.label}{im.set ? ` · ${JSON.stringify(im.set)}` : ""}</figcaption>}
          </figure>
        ))}
      </div>
    </CardShell>
  );
}

function FileIcon({ ext }: { ext: string }) {
  const I = [".zip"].includes(ext) ? FileArchive : [".mp4", ".mov", ".webm"].includes(ext) ? FilmStrip : [".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"].includes(ext) ? ImageIcon
    : [".wav", ".mp3"].includes(ext) ? Waveform : [".py", ".json", ".js", ".ts"].includes(ext) ? Code : [".ttf", ".otf"].includes(ext) ? TextAa : FileText;
  return <I size={20} />;
}

function FileCard({ art }: { art: Artifact }) {
  const m = art.meta || {};
  const pv = m.preview || {};
  const ext = m.ext || (art.path ? "." + art.path.split(".").pop() : "");
  return (
    <CardShell art={art} subtitle={art.type === "font" ? `${m.license || "licence unknown"} · ${(m.files || []).length} file(s)` : `${art.path} · ${fmtBytes(m.size_bytes || 0)}`}
      right={art.url && <a href={`${art.url}?download=true`} download className="inline-flex h-8 items-center gap-1.5 rounded-full border border-border px-3 text-xs font-medium hover:bg-surface-secondary"><DownloadSimple size={14} /> Download</a>}>
      <div className="flex gap-3">
        <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-surface-secondary text-muted"><FileIcon ext={ext} /></span>
        <div className="min-w-0 flex-1">
          {m.description && <p className="mb-2 text-sm">{m.description}</p>}
          {m.auto_traced && <Chip size="sm" color="warning" variant="soft">Auto-traced · IoU {m.iou}</Chip>}
          {pv.kind === "text" && <pre className="max-h-64 overflow-auto rounded-xl bg-surface-secondary p-3 font-mono text-[11.5px] leading-relaxed">{pv.text}{pv.truncated ? "\n…" : ""}</pre>}
          {pv.kind === "zip" && (
            <ul className="max-h-48 overflow-auto rounded-xl bg-surface-secondary p-2 font-mono text-[11.5px]">
              {pv.entries.map((e: any) => <li key={e.name} className="flex justify-between gap-2 px-1"><span className="truncate">{e.name}</span><span className="text-muted">{fmtBytes(e.size)}</span></li>)}
            </ul>
          )}
          {pv.kind === "image" && <img src={pv.url} alt="" className="checker max-h-64 rounded-xl border border-border object-contain" />}
          {pv.kind === "video" && <video src={pv.url} controls className="max-h-64 rounded-xl bg-night" />}
          {pv.kind === "audio" && <audio src={pv.url} controls className="w-full" />}
          {art.type === "font" && m.source && <p className="truncate text-xs text-muted">Source: {m.source}</p>}
        </div>
      </div>
    </CardShell>
  );
}

function StoryboardCard({ art }: { art: Artifact }) {
  const shots: any[] = art.meta?.shots || [];
  return (
    <CardShell art={art} subtitle={`${shots.length} shots`}>
      <div className="flex gap-3 overflow-x-auto pb-1">
        {shots.map((s) => (
          <figure key={s.n} className="w-52 shrink-0 overflow-hidden rounded-xl border border-border">
            <img src={s.url} alt="" className="aspect-video w-full bg-night object-cover" loading="lazy" />
            <figcaption className="px-2.5 py-2">
              <div className="flex items-center justify-between text-[11px] tabular-nums text-muted">
                <span>Shot {s.n}</span><span>{s.t.toFixed(2)}s{s.end != null ? `–${s.end.toFixed(2)}s` : "+"}</span>
              </div>
              <p className="mt-0.5 text-[13px] leading-snug">{s.note}</p>
            </figcaption>
          </figure>
        ))}
      </div>
    </CardShell>
  );
}

function TimelineCard({ art }: { art: Artifact }) {
  const m = art.meta || {};
  const evs: any[] = m.events || [];
  const dur = m.duration || Math.max(1, ...evs.map((e) => e.t + (e.duration || 0)));
  const video = useRef<HTMLVideoElement | null>(null);
  const [t, setT] = useState(0);
  const tracks = ["picture", "audio"];
  const seek = (x: number) => { if (video.current) { video.current.currentTime = x; } setT(x); };
  return (
    <CardShell art={art} subtitle={`${evs.length} events · ${Number(dur).toFixed(2)} s · hover a dot for its name`}>
      {m.video && <VideoPlayer src={m.video.url} compact videoRef={(el) => { video.current = el; }} onTime={setT} className="mb-3" />}
      <div className="relative select-none rounded-xl bg-surface-secondary p-2" onPointerDown={(e) => {
        const r = e.currentTarget.getBoundingClientRect();
        seek(Math.max(0, Math.min(1, (e.clientX - r.left - 64) / (r.width - 72))) * dur);
      }}>
        {tracks.map((tr) => (
          <div key={tr} className="flex h-9 items-center">
            <span className="w-14 shrink-0 text-[11px] capitalize text-muted">{tr}</span>
            <div className="relative h-7 flex-1 rounded-md bg-surface">
              {evs.filter((e) => e.track === tr).map((e, i, arr) => {
                const crowded = arr.some((o, k) => k !== i && Math.abs(o.t - e.t) / dur < 0.07);
                return (
                  <button key={i} title={`${e.name} · ${e.t.toFixed(3)}s`} aria-label={`${e.name} at ${e.t.toFixed(2)} s`}
                    onPointerDown={(ev) => { ev.stopPropagation(); seek(e.t); }}
                    className={cn("absolute top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full text-[10px] text-white ring-2 ring-surface",
                      crowded ? "size-2.5" : "flex h-5 items-center px-1.5", tr === "audio" ? "bg-brand-royal" : "bg-accent")}
                    style={{ left: `${(100 * e.t) / dur}%` }}>
                    {crowded ? null : e.name}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
        <div className="pointer-events-none absolute inset-y-1 w-px bg-danger" style={{ left: `calc(64px + (100% - 72px) * ${Math.min(1, t / dur)})` }} />
      </div>
    </CardShell>
  );
}

function CodeCard({ art }: { art: Artifact }) {
  const m = art.meta || {};
  const html: string = useMemo(() => {
    try {
      return hljs.getLanguage(m.language) ? hljs.highlight(m.content || "", { language: m.language }).value : hljs.highlightAuto(m.content || "").value;
    } catch {
      return (m.content || "").replace(/[&<>]/g, (c: string) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c] as string);
    }
  }, [m.content, m.language]);
  const lines = html.split("\n");
  const hl = new Set<number>(m.highlight || []);
  return (
    <CardShell art={art} subtitle={`${m.language} · ${m.lines} lines`} right={art.url && <a href={`${art.url}?download=true`} download className="text-xs text-link">Download</a>}>
      <pre className="hljs max-h-[420px] overflow-auto rounded-xl bg-surface-secondary py-2 font-mono text-[12px] leading-[1.55]">
        {lines.map((l: string, i: number) => (
          <div key={i} className={cn("flex", hl.has(i + 1) && "bg-accent/10")}>
            <span className="w-10 shrink-0 select-none pr-3 text-right text-muted/70">{i + 1}</span>
            <span className="whitespace-pre pr-3" dangerouslySetInnerHTML={{ __html: l || " " }} />
          </div>
        ))}
      </pre>
    </CardShell>
  );
}

export function DataTable({ columns, rows }: { columns: string[]; rows: any[][] }) {
  return (
    <div className="max-h-[420px] overflow-auto rounded-xl border border-border">
      <table className="w-full text-left text-[13px]">
        <thead className="sticky top-0 bg-surface-secondary"><tr>{columns.map((c) => <th key={c} className="px-3 py-2 font-medium">{c}</th>)}</tr></thead>
        <tbody className="divide-y divide-separator">
          {rows.map((r, i) => (
            <tr key={i}>{r.map((c, k) => (
              <td key={k} className={cn("px-3 py-1.5 align-top", c === "PASS" && "text-success", c === "FAIL" && "font-medium text-danger", c === "CHECK" && "text-warning")}>
                {typeof c === "boolean" ? (c ? "yes" : "no") : String(c ?? "")}
              </td>
            ))}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------- requests
export function RequestCard({ r, runId }: { r: Req; runId: string }) {
  const notify = useStore((s) => s.notify);
  const d = r.data;
  const [sel, setSel] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [remaining, setRemaining] = useState<number | null>(null);
  const pending = r.status === "pending";
  useEffect(() => {
    if (!pending || !d.deadline) return;
    const tick = () => setRemaining(Math.max(0, Math.round(d.deadline - Date.now() / 1000)));
    tick();
    const iv = setInterval(tick, 1000);
    return () => clearInterval(iv);
  }, [pending, d.deadline]);
  const send = async (body: any) => {
    setBusy(true);
    try {
      await api(`/api/runs/${runId}/requests/${r.id}/answer`, { method: "POST", json: body });
    } catch (e: any) {
      notify(e.message, "bad");
    } finally {
      setBusy(false);
    }
  };
  const answer = r.answer || {};
  const title = r.reqKind === "ask" ? d.question : d.title;
  const Icon = r.reqKind === "approval" ? SealCheck : Question;
  return (
    <div className={cn("rounded-2xl border p-4", pending ? "border-accent/40 bg-offwhite" : "border-border bg-surface")} data-testid={`request-${r.reqKind}`}>
      <div className="flex items-start gap-2.5">
        <Icon size={20} weight="fill" className={cn("mt-0.5 shrink-0", pending ? "text-accent" : "text-muted")} />
        <div className="min-w-0 flex-1">
          <div className="text-[11px] font-medium uppercase tracking-wide text-muted">
            {r.reqKind === "approval" ? "Approval needed" : r.reqKind === "options" ? "Choose a direction" : "Question"}
            {r.auto && " · approved automatically (Autopilot)"}
            {remaining != null && pending && ` · default in ${remaining}s`}
          </div>
          <div className="md mt-1"><ReactMarkdown remarkPlugins={[remarkGfm]}>{title || ""}</ReactMarkdown></div>
          {d.summary && <div className="md mt-1 text-muted"><ReactMarkdown remarkPlugins={[remarkGfm]}>{d.summary}</ReactMarkdown></div>}
        </div>
      </div>
      {r.reqKind === "approval" && (d.artifacts || []).length > 0 && (
        <div className="mt-3 flex flex-col gap-3">{d.artifacts.map((a: Artifact) => <PresentCard key={a.id} card={a.type} art={a} />)}</div>
      )}
      {r.reqKind === "options" && (
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {d.options.map((o: any) => (
            <button key={o.label} disabled={!pending || busy} onClick={() => send({ choice: o.label, note: text || undefined })}
              className={cn("flex flex-col overflow-hidden rounded-xl border bg-surface text-left transition-colors",
                answer.choice === o.label ? "border-accent ring-2 ring-accent/30" : "border-border", pending && "hover:border-accent/60")}>
              {o.preview?.type === "video" && o.preview.url && <video src={o.preview.url} muted loop playsInline autoPlay className="aspect-video w-full bg-night object-cover" />}
              {(o.preview?.type === "image" || o.preview?.type === "storyboard") && (o.preview.meta?.images?.[0]?.url || o.preview.meta?.shots?.[0]?.url || o.preview.url) &&
                <img src={o.preview.meta?.images?.[0]?.url || o.preview.meta?.shots?.[0]?.url || o.preview.url} alt="" className="aspect-video w-full bg-night object-cover" />}
              {o.preview?.type === "audio" && <audio src={o.preview.url} controls className="w-full px-2 pt-2" onClick={(e) => e.stopPropagation()} />}
              <div className="px-3 py-2.5"><div className="text-sm font-medium">{o.label}</div>{o.description && <p className="text-xs text-muted">{o.description}</p>}</div>
            </button>
          ))}
        </div>
      )}
      {r.reqKind === "ask" && d.options?.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {d.options.map((o: string) => {
            const on = pending ? sel.includes(o) : (answer.selections || []).includes(o);
            return (
              <Button key={o} size="sm" variant={on ? "primary" : "secondary"} isDisabled={!pending || busy}
                onPress={() => (d.multi_select ? setSel(on ? sel.filter((x) => x !== o) : [...sel, o]) : send({ selections: [o], text: text || undefined }))}>
                {o}
              </Button>
            );
          })}
        </div>
      )}
      {pending && (r.reqKind !== "ask" || d.allow_free_text !== false || d.multi_select) && (
        <div className="mt-3 flex flex-col gap-2">
          {(r.reqKind !== "ask" || d.allow_free_text !== false) && (
            <TextArea aria-label="Your answer" rows={2} value={text} onChange={(e) => setText(e.target.value)} className="w-full resize-none bg-surface"
              placeholder={r.reqKind === "approval" ? "Notes or requested changes (optional)" : "Type an answer…"} />
          )}
          <div className="flex flex-wrap justify-end gap-2">
            {r.reqKind === "approval" && (d.choices || []).map((c: string, i: number) => (
              <Button key={c} size="sm" variant={i === 0 ? "primary" : "secondary"} isPending={busy} data-approve={i === 0 ? "1" : undefined}
                onPress={() => send({ choice: c, note: text || undefined })}>{i === 0 && <CheckCircle size={16} />}{c}{i === 0 && <kbd className="ml-1 hidden rounded bg-white/20 px-1 text-[10px] sm:inline">A</kbd>}</Button>
            ))}
            {r.reqKind === "ask" && (d.multi_select || (d.allow_free_text !== false && text)) && (
              <Button size="sm" isPending={busy} isDisabled={!sel.length && !text.trim()} onPress={() => send({ selections: sel.length ? sel : undefined, text: text || undefined })}>Send</Button>
            )}
            {r.reqKind === "options" && text && <Button size="sm" variant="secondary" isPending={busy} onPress={() => send({ text })}>Reply without choosing</Button>}
          </div>
        </div>
      )}
      {!pending && (
        <div className="mt-2 flex items-center gap-1.5 text-xs text-muted">
          {r.status === "cancelled" ? <XCircle size={14} /> : <CheckCircle size={14} className="text-success" />}
          {r.status === "timeout" ? "No answer — used the default: " : r.status === "cancelled" ? "Cancelled" : "Answered: "}
          <span className="text-foreground">{answer.choice || (answer.selections || []).join(", ") || answer.text || ""}{answer.note ? ` — ${answer.note}` : ""}</span>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------------------------- sub-agents
export function SubagentCard({ it, render }: { it: Extract<Item, { kind: "subagent" }>; render: (items: Item[], runId: string) => React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-2xl border border-border bg-surface" data-testid="subagent">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left">
        <span className="flex size-7 items-center justify-center rounded-lg bg-offwhite text-accent"><Robot size={16} /></span>
        <span className="min-w-0 flex-1">
          <span className="block text-[13.5px]">Sub-agent · {it.label}</span>
          <span className="block truncate text-xs text-muted">{it.status === "running" ? it.task : it.result?.summary || it.result?.error || it.status}</span>
        </span>
        {it.status === "running" ? <Spinner size="sm" /> : it.status === "completed" ? <CheckCircle size={18} weight="fill" className="text-success" /> : <XCircle size={18} weight="fill" className="text-danger" />}
        {it.result?.cost_usd != null && <span className="text-xs tabular-nums text-muted">${Number(it.result.cost_usd).toFixed(3)}</span>}
      </button>
      {open && <ChildTimeline runId={it.child} render={render} />}
    </div>
  );
}

function ChildTimeline({ runId, render }: { runId: string; render: (items: Item[], runId: string) => React.ReactNode }) {
  const run = useRunEvents(runId);
  return (
    <div className="flex flex-col gap-3 border-t border-separator px-3.5 py-3">
      {run.items.length ? render(run.items, runId) : <p className="text-sm text-muted">Waiting for the sub-agent…</p>}
      {ACTIVE.has(run.status) && <div className="flex items-center gap-2 text-xs text-muted"><Spinner size="sm" /> working</div>}
    </div>
  );
}

export function FavoriteButton({ art, onChange }: { art: Artifact; onChange?: (a: Artifact) => void }) {
  return (
    <Button isIconOnly size="sm" variant="ghost" aria-label={art.favorite ? "Unfavourite" : "Favourite"}
      onPress={async () => onChange?.(await api<Artifact>(`/api/artifacts/${art.id}`, { method: "PATCH", json: { favorite: !art.favorite } }))}>
      <Star size={16} weight={art.favorite ? "fill" : "regular"} className={art.favorite ? "text-warning" : "text-muted"} />
    </Button>
  );
}
