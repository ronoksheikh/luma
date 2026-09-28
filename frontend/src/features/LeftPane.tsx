import { Copy, FileAudio, FileText, FolderPlus, ImageIcon, Info, Pencil, Plus, Trash2, UploadCloud, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Badge, Button, Dialog, Input, Label, SectionTitle, Select, Spinner, Switch, Textarea, Tip, cn } from "../components/ui";
import { api, fmtBytes, type Asset, type Project, type ProjectSettings } from "../lib/api";
import { useStore } from "../lib/store";

const RESOLUTIONS = [
  { v: "1920x1080", l: "1920 × 1080 · 16:9" },
  { v: "1080x1920", l: "1080 × 1920 · 9:16" },
  { v: "1080x1080", l: "1080 × 1080 · 1:1" },
  { v: "3840x2160", l: "3840 × 2160 · 4K" },
];

export function LeftPane() {
  const { projects, projectId } = useStore();
  const project = projects.find((p) => p.id === projectId) || null;
  return (
    <aside className="flex h-full min-h-0 flex-col overflow-y-auto border-r border-line bg-panel/40">
      <ProjectList />
      {project && <Assets project={project} />}
      {project && <ProjectSettingsForm project={project} />}
    </aside>
  );
}

function ProjectList() {
  const { projects, projectId, selectProject, refreshProjects, notify } = useStore();
  const [editing, setEditing] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [confirm, setConfirm] = useState<Project | null>(null);

  const create = async () => {
    const p = await api<Project>("/api/projects", { method: "POST", json: { name: `Project ${projects.length + 1}` } });
    await refreshProjects();
    await selectProject(p.id);
    setEditing(p.id);
    setName(p.name);
  };
  const rename = async (id: string) => {
    if (name.trim()) await api(`/api/projects/${id}`, { method: "PATCH", json: { name: name.trim() } });
    setEditing(null);
    refreshProjects();
  };
  return (
    <div>
      <SectionTitle right={<Tip content="New project"><Button size="iconSm" variant="ghost" onClick={create} aria-label="New project"><FolderPlus className="h-4 w-4" /></Button></Tip>}>Projects</SectionTitle>
      <div className="space-y-0.5 px-2">
        {projects.map((p) => (
          <div key={p.id} className={cn("group flex items-center gap-1 rounded-lg px-2 py-1.5", p.id === projectId ? "bg-raised" : "hover:bg-raised/60")}>
            {editing === p.id ? (
              <Input autoFocus value={name} onChange={(e) => setName(e.target.value)} onBlur={() => rename(p.id)}
                onKeyDown={(e) => { if (e.key === "Enter") rename(p.id); if (e.key === "Escape") setEditing(null); }} className="h-7 text-xs" />
            ) : (
              <button className="min-w-0 flex-1 text-left" onClick={() => selectProject(p.id)} onDoubleClick={() => { setEditing(p.id); setName(p.name); }}>
                <div className="flex items-center gap-1.5 truncate text-sm">
                  {p.latest_run && ["running", "waiting_input"].includes(p.latest_run.status) && <span className="h-1.5 w-1.5 shrink-0 animate-pulse-soft rounded-full bg-accent" />}
                  <span className="truncate">{p.name}</span>
                  {p.kind === "demo" && <Badge tone="accent">demo</Badge>}
                </div>
                <div className="text-[11px] text-faint">{p.asset_count} assets · {p.settings.width}×{p.settings.height} · {p.settings.fps} fps · {p.settings.duration}s</div>
              </button>
            )}
            <div className="flex opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
              <Tip content="Rename"><Button size="iconSm" variant="ghost" aria-label="Rename" onClick={() => { setEditing(p.id); setName(p.name); }}><Pencil className="h-3.5 w-3.5" /></Button></Tip>
              <Tip content="Duplicate"><Button size="iconSm" variant="ghost" aria-label="Duplicate" onClick={async () => { const d = await api<Project>(`/api/projects/${p.id}/duplicate`, { method: "POST" }); await refreshProjects(); selectProject(d.id); notify("Project duplicated", "ok"); }}><Copy className="h-3.5 w-3.5" /></Button></Tip>
              <Tip content="Delete"><Button size="iconSm" variant="ghost" aria-label="Delete" onClick={() => setConfirm(p)}><Trash2 className="h-3.5 w-3.5" /></Button></Tip>
            </div>
          </div>
        ))}
        {!projects.length && (
          <button onClick={create} className="flex w-full items-center gap-2 rounded-lg border border-dashed border-line-strong px-3 py-3 text-sm text-muted hover:text-fg">
            <Plus className="h-4 w-4" /> Create your first project
          </button>
        )}
      </div>
      <Dialog open={!!confirm} onOpenChange={(v) => !v && setConfirm(null)} title="Delete project?" description={`“${confirm?.name}” and its whole workspace (assets, renders, outputs) will be removed. Running jobs are stopped.`}>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setConfirm(null)}>Cancel</Button>
          <Button variant="danger" onClick={async () => { await api(`/api/projects/${confirm!.id}`, { method: "DELETE" }); setConfirm(null); localStorage.removeItem("luma.project"); useStore.setState({ projectId: null }); await refreshProjects(); }}>Delete</Button>
        </div>
      </Dialog>
    </div>
  );
}

function Assets({ project }: { project: Project }) {
  const { server, notify, refreshProjects } = useStore();
  const [assets, setAssets] = useState<Asset[]>([]);
  const [busy, setBusy] = useState(false);
  const [drag, setDrag] = useState(false);
  const [view, setView] = useState<Asset | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const max = server?.limits.max_files ?? 20;
  const maxMb = server?.limits.max_file_mb ?? 25;

  const load = useCallback(() => api<Asset[]>(`/api/projects/${project.id}/assets`).then(setAssets).catch(() => {}), [project.id]);
  useEffect(() => { load(); }, [load]);

  const upload = async (files: File[]) => {
    if (!files.length) return;
    if (assets.length + files.length > max) {
      notify(`A project can hold at most ${max} files (${assets.length} uploaded). Remove some first.`, "bad");
      return;
    }
    const big = files.find((f) => f.size > maxMb * 1024 * 1024);
    if (big) {
      notify(`${big.name} is larger than ${maxMb} MB.`, "bad");
      return;
    }
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    setBusy(true);
    try {
      await api(`/api/projects/${project.id}/assets`, { method: "POST", body: fd });
      notify(`Uploaded ${files.length} file${files.length > 1 ? "s" : ""} — analysed`, "ok");
    } catch (e: any) {
      notify(e.message, "bad");
    } finally {
      setBusy(false);
      load();
      refreshProjects();
    }
  };

  const remove = async (a: Asset) => {
    await api(`/api/projects/${project.id}/assets/${a.id}`, { method: "DELETE" });
    load();
    refreshProjects();
  };

  return (
    <div>
      <SectionTitle right={<span className={cn("text-[11px] tabular-nums", assets.length >= max ? "text-bad" : "text-faint")}>{assets.length} / {max}</span>}>Assets</SectionTitle>
      <div className="px-3">
        <div
          role="button"
          tabIndex={0}
          aria-label="Upload assets"
          onClick={() => input.current?.click()}
          onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); upload(Array.from(e.dataTransfer.files)); }}
          className={cn("flex cursor-pointer flex-col items-center justify-center gap-1 rounded-xl border border-dashed px-3 py-4 text-center transition-colors",
            drag ? "border-accent bg-accent/5" : "border-line-strong hover:border-accent/50")}
        >
          {busy ? <Spinner /> : <UploadCloud className="h-5 w-5 text-muted" />}
          <div className="text-xs text-muted">{busy ? "Uploading & analysing…" : "Drop logo SVG, images, brand PDF, audio"}</div>
          <div className="text-[10px] text-faint">SVG · PNG · JPG · WebP · PDF · MP3 · WAV — max {maxMb} MB each</div>
          <input ref={input} type="file" multiple hidden accept=".svg,.png,.jpg,.jpeg,.webp,.pdf,.mp3,.wav,image/*,audio/*,application/pdf"
            onChange={(e) => { upload(Array.from(e.target.files || [])); e.target.value = ""; }} />
        </div>
        <div className="mt-2 grid grid-cols-3 gap-2">
          {assets.map((a) => (
            <div key={a.id} className="group relative overflow-hidden rounded-lg border border-line bg-bg">
              <button className="block w-full" onClick={() => setView(a)} aria-label={`Inspect ${a.filename}`}>
                <div className="checker flex aspect-square items-center justify-center">
                  {a.thumb_url ? <img src={a.thumb_url} alt="" className="max-h-full max-w-full object-contain p-1.5" loading="lazy" />
                    : a.kind === "mp3" || a.kind === "wav" ? <FileAudio className="h-7 w-7 text-muted" /> : <FileText className="h-7 w-7 text-muted" />}
                </div>
                <div className="truncate px-1.5 py-1 text-left text-[10px] text-muted">{a.filename}</div>
              </button>
              <button onClick={() => remove(a)} aria-label={`Remove ${a.filename}`}
                className="absolute right-1 top-1 rounded-md bg-bg/80 p-0.5 text-muted opacity-0 hover:text-bad group-hover:opacity-100 focus:opacity-100">
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>
      </div>
      <AnalysisDialog asset={view} onClose={() => setView(null)} />
    </div>
  );
}

function AnalysisDialog({ asset, onClose }: { asset: Asset | null; onClose: () => void }) {
  if (!asset) return null;
  const a = asset.analysis || {};
  const facts: [string, React.ReactNode][] = [];
  if (asset.kind === "svg") {
    facts.push(["viewBox", a.viewBox?.join(" ")], ["shapes", a.shape_count], ["pivot", a.pivot ? a.pivot.join(", ") : "—"],
      ["symmetry", a.symmetry ? `${a.symmetry.symmetric ? "symmetric" : "asymmetric"} (IoU ${a.symmetry.score})` : "—"],
      ["gradients", Object.keys(a.gradients || {}).length]);
  } else if (asset.kind === "pdf") {
    facts.push(["pages", a.page_count], ["fonts", [...(a.embedded_fonts || []), ...(a.font_mentions || [])].join(", ") || "—"]);
  } else if (["png", "jpeg", "webp"].includes(asset.kind)) {
    facts.push(["size", `${a.width} × ${a.height}`], ["transparency", a.has_transparency ? "yes" : "no"]);
  } else {
    facts.push(["duration", `${a.duration_s}s`], ["loudness", `${a.lufs} LUFS`], ["true peak", `${a.true_peak_dbtp} dBTP`]);
  }
  const colors: string[] = asset.kind === "pdf" ? a.hex_colors_in_text || [] : asset.kind === "svg" ? a.colors || [] : (a.palette || []).map((p: any) => p.hex);
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()} title={asset.filename} description={`${asset.kind.toUpperCase()} · ${fmtBytes(asset.size)} · automatic analysis (also given to the director)`} wide>
      <div className="grid gap-5 md:grid-cols-[240px_1fr]">
        <div className="space-y-3">
          <div className="checker flex aspect-square items-center justify-center rounded-xl border border-line">
            {asset.kind === "mp3" || asset.kind === "wav" ? <audio controls src={asset.url} className="w-full px-2" /> :
              asset.thumb_url ? <img src={asset.thumb_url} alt="" className="max-h-full max-w-full object-contain p-3" /> : <ImageIcon className="h-8 w-8 text-faint" />}
          </div>
          <dl className="space-y-1.5 text-xs">
            {facts.map(([k, v]) => (<div key={k} className="flex justify-between gap-3"><dt className="text-faint">{k}</dt><dd className="text-right">{v as any}</dd></div>))}
          </dl>
          {colors.length > 0 && (
            <div>
              <div className="mb-1.5 text-[11px] text-faint">Colours</div>
              <div className="flex flex-wrap gap-1.5">
                {colors.slice(0, 16).map((c) => (
                  <span key={c} className="flex items-center gap-1 rounded-md border border-line bg-bg px-1.5 py-0.5 font-mono text-[10px]">
                    <span className="h-3 w-3 rounded-sm border border-white/10" style={{ background: c }} />{c}
                  </span>
                ))}
              </div>
            </div>
          )}
          <a href={asset.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-accent"><Info className="h-3.5 w-3.5" /> Open original</a>
        </div>
        <pre className="max-h-[60vh] overflow-auto rounded-xl border border-line bg-bg p-3 font-mono text-[11px] leading-relaxed text-muted">{JSON.stringify(a, null, 2)}</pre>
      </div>
    </Dialog>
  );
}

function ProjectSettingsForm({ project }: { project: Project }) {
  const { refreshProjects, notify } = useStore();
  const [st, setSt] = useState<ProjectSettings>(project.settings);
  const [brief, setBrief] = useState(project.brief);
  const [avoid, setAvoid] = useState("");
  const [dirty, setDirty] = useState(false);
  useEffect(() => { setSt(project.settings); setBrief(project.brief); setDirty(false); }, [project.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const up = (p: Partial<ProjectSettings>) => { setSt({ ...st, ...p }); setDirty(true); };
  const save = async () => {
    try {
      await api(`/api/projects/${project.id}`, { method: "PATCH", json: { brief, settings: st } });
      setDirty(false);
      refreshProjects();
      notify("Project settings saved", "ok");
    } catch (e: any) {
      notify(e.message, "bad");
    }
  };
  return (
    <div className="pb-4">
      <SectionTitle right={dirty ? <Button size="sm" variant="primary" onClick={save}>Save</Button> : null}>Brief & output</SectionTitle>
      <div className="space-y-3 px-3">
        <div>
          <Label htmlFor="brief">Brief</Label>
          <Textarea id="brief" rows={3} value={brief} onChange={(e) => { setBrief(e.target.value); setDirty(true); }} placeholder="What should the film be? Audience, mood, must-haves…" />
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div className="col-span-2">
            <Label htmlFor="res">Resolution</Label>
            <Select id="res" value={`${st.width}x${st.height}`} onChange={(e) => { const [w, h] = e.target.value.split("x").map(Number); up({ width: w, height: h }); }}>
              {RESOLUTIONS.map((r) => <option key={r.v} value={r.v}>{r.l}</option>)}
            </Select>
          </div>
          <div>
            <Label htmlFor="fps">FPS</Label>
            <Select id="fps" value={st.fps} onChange={(e) => up({ fps: Number(e.target.value) })}>{[24, 30, 60].map((f) => <option key={f}>{f}</option>)}</Select>
          </div>
          <div>
            <Label htmlFor="dur">Duration (s)</Label>
            <Input id="dur" type="number" min={3} max={180} step={0.5} value={st.duration} onChange={(e) => up({ duration: Number(e.target.value) })} />
          </div>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-xs text-muted">Also deliver ProRes 422 HQ</span>
          <Switch checked={st.formats.includes("prores")} onCheckedChange={(v) => up({ formats: v ? ["mp4", "prores"] : ["mp4"] })} label="ProRes" />
        </div>
        <div className="flex items-center justify-between">
          <span className="text-xs text-muted">Captions (voiced films)</span>
          <Switch checked={st.captions} onCheckedChange={(v) => up({ captions: v })} label="Captions" />
        </div>
        <div>
          <Label>Colours to avoid</Label>
          <div className="flex flex-wrap gap-1.5">
            {st.avoid_colors.map((c) => (
              <span key={c} className="flex items-center gap-1 rounded-md border border-line bg-bg px-1.5 py-0.5 font-mono text-[10px]">
                <span className="h-3 w-3 rounded-sm" style={{ background: c }} />{c}
                <button aria-label={`Remove ${c}`} onClick={() => up({ avoid_colors: st.avoid_colors.filter((x) => x !== c) })}><X className="h-3 w-3 text-faint hover:text-bad" /></button>
              </span>
            ))}
          </div>
          <div className="mt-1.5 flex gap-1.5">
            <Input value={avoid} onChange={(e) => setAvoid(e.target.value)} placeholder="#FF0000" className="h-8 font-mono text-xs"
              onKeyDown={(e) => { if (e.key === "Enter" && /^#?[0-9a-fA-F]{3,6}$/.test(avoid.trim())) { const h = "#" + avoid.trim().replace("#", "").toUpperCase(); up({ avoid_colors: [...st.avoid_colors, h] }); setAvoid(""); } }} />
            <input type="color" aria-label="Pick colour to avoid" className="h-8 w-9 cursor-pointer rounded-md border border-line-strong bg-bg"
              onChange={(e) => up({ avoid_colors: Array.from(new Set([...st.avoid_colors, e.target.value.toUpperCase()])) })} />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <Label htmlFor="vl">Voice language</Label>
            <Input id="vl" value={st.voice_language} onChange={(e) => up({ voice_language: e.target.value })} />
          </div>
          <div>
            <Label htmlFor="vt">Voice tone</Label>
            <Input id="vt" value={st.voice_tone} onChange={(e) => up({ voice_tone: e.target.value })} placeholder="calm, warm" />
          </div>
        </div>
      </div>
    </div>
  );
}
