import { Button, Chip, Modal, Spinner } from "@heroui/react";
import { ArrowSquareOut, FileAudio, FilePdf, ImageSquare, UploadSimple, X } from "@phosphor-icons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, fmtBytes, type Asset } from "../lib/api";
import { useStore } from "../lib/store";
import { cn } from "../ui/kit";

const ACCEPT = ".svg,.png,.jpg,.jpeg,.webp,.pdf,.mp3,.wav,image/*,audio/*,application/pdf";

/** Upload files into the current project's assets (shared by the composer, welcome card and Assets tab). */
export function useUpload() {
  const { projectId, server, notify, refreshProjects, bumpAssets, projects } = useStore();
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLInputElement>(null);
  const max = server?.limits.max_files ?? 20;
  const maxMb = server?.limits.max_file_mb ?? 25;

  const upload = async (files: File[]) => {
    if (!files.length || !projectId) return;
    const count = projects.find((p) => p.id === projectId)?.asset_count ?? 0;
    if (count + files.length > max) {
      notify(`A project holds at most ${max} files (${count} uploaded). Remove some first.`, "bad");
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
      await api(`/api/projects/${projectId}/assets`, { method: "POST", body: fd });
      notify(`Added ${files.length} file${files.length > 1 ? "s" : ""}`, "ok");
    } catch (e: any) {
      notify(e.message, "bad");
    } finally {
      setBusy(false);
      bumpAssets();
      refreshProjects();
    }
  };

  const inputEl = (
    <input ref={ref} type="file" multiple hidden accept={ACCEPT} data-testid="asset-input"
      onChange={(e) => { upload(Array.from(e.target.files || [])); e.target.value = ""; }} />
  );
  return { busy, upload, pick: () => ref.current?.click(), inputEl, max, maxMb };
}

export function AssetsPanel() {
  const { projectId, assetsVersion, bumpAssets, refreshProjects } = useStore();
  const { busy, upload, pick, inputEl, max, maxMb } = useUpload();
  const [assets, setAssets] = useState<Asset[]>([]);
  const [drag, setDrag] = useState(false);
  const [view, setView] = useState<Asset | null>(null);

  const load = useCallback(() => {
    if (!projectId) return;
    api<Asset[]>(`/api/projects/${projectId}/assets`).then(setAssets).catch(() => setAssets([]));
  }, [projectId]);
  useEffect(() => { load(); }, [load, assetsVersion]);

  const remove = async (a: Asset) => {
    await api(`/api/projects/${projectId}/assets/${a.id}`, { method: "DELETE" });
    bumpAssets();
    refreshProjects();
  };

  return (
    <div className="flex flex-col gap-4 p-4">
      <div role="button" tabIndex={0} aria-label="Upload assets" onClick={pick}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && pick()}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); upload(Array.from(e.dataTransfer.files)); }}
        className={cn("flex cursor-pointer flex-col items-center justify-center gap-2 rounded-2xl border border-dashed px-4 py-7 text-center outline-none transition-colors focus-visible:ring-2 focus-visible:ring-focus",
          drag ? "border-accent bg-offwhite" : "border-border bg-surface hover:border-accent/50")}>
        <span className="flex size-10 items-center justify-center rounded-full bg-offwhite text-accent">
          {busy ? <Spinner size="sm" /> : <UploadSimple size={20} />}
        </span>
        <div className="text-sm">{busy ? "Uploading and analysing…" : "Drop files or click to upload"}</div>
        <div className="text-xs text-muted">SVG, PNG, JPG, WebP, PDF, MP3, WAV · up to {maxMb} MB each</div>
      </div>
      {inputEl}

      <div className="flex items-center justify-between px-0.5 text-xs text-muted">
        <span>Assets are analysed automatically and shared with the director.</span>
        <span className={cn("tabular-nums", assets.length >= max && "text-danger")}>{assets.length} / {max}</span>
      </div>

      {assets.length > 0 && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          {assets.map((a) => (
            <div key={a.id} className="group relative overflow-hidden rounded-xl border border-border bg-surface transition-colors hover:border-accent/40">
              <button className="block w-full text-left" onClick={() => setView(a)} aria-label={`Inspect ${a.filename}`}>
                <div className="checker flex aspect-[4/3] items-center justify-center">
                  {a.thumb_url ? <img src={a.thumb_url} alt="" className="max-h-full max-w-full object-contain p-3" loading="lazy" /> : <KindIcon kind={a.kind} />}
                </div>
                <div className="flex items-center justify-between gap-2 border-t border-separator px-2.5 py-1.5">
                  <span className="truncate text-xs">{a.filename}</span>
                  <span className="shrink-0 text-[10px] uppercase text-muted">{a.kind}</span>
                </div>
              </button>
              <Button isIconOnly size="sm" variant="secondary" aria-label={`Remove ${a.filename}`} onPress={() => remove(a)}
                className="absolute right-1.5 top-1.5 size-7 min-w-0 opacity-0 group-hover:opacity-100 focus-visible:opacity-100">
                <X size={14} />
              </Button>
            </div>
          ))}
        </div>
      )}
      <AnalysisModal asset={view} onClose={() => setView(null)} />
    </div>
  );
}

function KindIcon({ kind }: { kind: string }) {
  const Icon = kind === "mp3" || kind === "wav" ? FileAudio : kind === "pdf" ? FilePdf : ImageSquare;
  return <Icon size={32} className="text-muted" />;
}

function AnalysisModal({ asset, onClose }: { asset: Asset | null; onClose: () => void }) {
  const a = asset?.analysis || {};
  const facts: [string, React.ReactNode][] = [];
  if (asset?.kind === "svg") {
    facts.push(["viewBox", a.viewBox?.join(" ")], ["Shapes", a.shape_count], ["Pivot", a.pivot ? a.pivot.join(", ") : "—"],
      ["Symmetry", a.symmetry ? `${a.symmetry.symmetric ? "Symmetric" : "Asymmetric"} (${a.symmetry.score})` : "—"],
      ["Gradients", Object.keys(a.gradients || {}).length]);
  } else if (asset?.kind === "pdf") {
    facts.push(["Pages", a.page_count], ["Fonts", [...(a.embedded_fonts || []), ...(a.font_mentions || [])].join(", ") || "—"]);
  } else if (asset && ["png", "jpeg", "webp"].includes(asset.kind)) {
    facts.push(["Size", `${a.width} × ${a.height}`], ["Transparency", a.has_transparency ? "Yes" : "No"]);
  } else if (asset) {
    facts.push(["Duration", `${a.duration_s} s`], ["Loudness", `${a.lufs} LUFS`], ["True peak", `${a.true_peak_dbtp} dBTP`]);
  }
  const colors: string[] = asset?.kind === "pdf" ? a.hex_colors_in_text || [] : asset?.kind === "svg" ? a.colors || [] : (a.palette || []).map((p: any) => p.hex);
  return (
    <Modal.Backdrop isOpen={!!asset} onOpenChange={(v) => !v && onClose()}>
      <Modal.Container size="lg" scroll="inside">
        <Modal.Dialog className="sm:max-w-[820px]">
          <Modal.CloseTrigger />
          <Modal.Header>
            <Modal.Heading className="truncate pr-8">{asset?.filename}</Modal.Heading>
            <p className="text-sm text-muted">{asset?.kind.toUpperCase()} · {asset ? fmtBytes(asset.size) : ""} · Automatic analysis</p>
          </Modal.Header>
          <Modal.Body>
            {asset && (
              <div className="grid gap-6 md:grid-cols-[260px_1fr]">
                <div className="flex flex-col gap-4">
                  <div className="checker flex aspect-square items-center justify-center overflow-hidden rounded-xl border border-border">
                    {asset.kind === "mp3" || asset.kind === "wav" ? <audio controls src={asset.url} className="w-full px-3" />
                      : asset.thumb_url ? <img src={asset.thumb_url} alt="" className="max-h-full max-w-full object-contain p-4" /> : <KindIcon kind={asset.kind} />}
                  </div>
                  <dl className="flex flex-col gap-2 text-sm">
                    {facts.map(([k, v]) => (
                      <div key={k} className="flex justify-between gap-3"><dt className="text-muted">{k}</dt><dd className="text-right">{v as any}</dd></div>
                    ))}
                  </dl>
                  {colors.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {colors.slice(0, 16).map((c) => (
                        <Chip key={c} size="sm" variant="secondary" className="font-mono">
                          <span className="size-3 rounded-full ring-1 ring-black/10" style={{ background: c }} />
                          <Chip.Label>{c}</Chip.Label>
                        </Chip>
                      ))}
                    </div>
                  )}
                  <a href={asset.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 text-sm text-link">
                    <ArrowSquareOut size={16} /> Open original
                  </a>
                </div>
                <pre className="max-h-[60vh] overflow-auto rounded-xl bg-surface-secondary p-3 font-mono text-[11.5px] leading-relaxed text-foreground/80">{JSON.stringify(a, null, 2)}</pre>
              </div>
            )}
          </Modal.Body>
        </Modal.Dialog>
      </Modal.Container>
    </Modal.Backdrop>
  );
}
