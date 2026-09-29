// Media building blocks shared by the inline cards, the Preview tab and the Artifacts shelf.
import { Button, ToggleButton, ToggleButtonGroup } from "@heroui/react";
import { CaretLeft, CaretRight, DownloadSimple, Pause, Play, Repeat, SkipBack } from "@phosphor-icons/react";
import { useCallback, useEffect, useMemo, useRef, useState, type Key } from "react";
import { Tip, cn } from "./kit";

export type Chapter = { t: number; label: string };

/** The player that currently owns the keyboard (Space, , and .). */
let activePlayer: { toggle: () => void; step: (n: number) => void } | null = null;
export function playerKeys(e: KeyboardEvent): boolean {
  if (!activePlayer) return false;
  if (e.key === " " || e.key === "k") { activePlayer.toggle(); return true; }
  if (e.key === ",") { activePlayer.step(-1); return true; }
  if (e.key === ".") { activePlayer.step(1); return true; }
  return false;
}

export function VideoPlayer({ src, poster, fps = 60, chapters = [], loop: loop0 = true, downloadName, className, onTime, videoRef, compact }: {
  src: string; poster?: string | null; fps?: number; chapters?: Chapter[]; loop?: boolean; downloadName?: string; className?: string;
  onTime?: (t: number) => void; videoRef?: (el: HTMLVideoElement | null) => void; compact?: boolean;
}) {
  const v = useRef<HTMLVideoElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [loop, setLoop] = useState(loop0);
  const [rate, setRate] = useState(1);
  const [t, setT] = useState(0);
  const [dur, setDur] = useState(0);
  const toggle = useCallback(() => { const el = v.current; if (el) { if (el.paused) el.play().catch(() => {}); else el.pause(); } }, []);
  const step = useCallback((n: number) => {
    const el = v.current;
    if (!el) return;
    el.pause();
    el.currentTime = Math.max(0, Math.min(el.duration || 0, Math.round(el.currentTime * fps + n) / fps + 1e-4));
  }, [fps]);
  const api = useMemo(() => ({ toggle, step }), [toggle, step]);
  useEffect(() => () => { if (activePlayer === api) activePlayer = null; }, [api]);
  useEffect(() => { if (v.current) v.current.playbackRate = rate; }, [rate]);
  const frame = Math.round(t * fps);
  const total = Math.round(dur * fps);
  const seek = (x: number) => { if (v.current) v.current.currentTime = x; };
  return (
    <div className={cn("flex flex-col gap-2", className)} onPointerDown={() => { activePlayer = api; }} onFocusCapture={() => { activePlayer = api; }}>
      <div className="overflow-hidden rounded-xl bg-night ring-1 ring-border">
        <video ref={(el) => { v.current = el; videoRef?.(el); }} src={src} poster={poster ?? undefined} className="aspect-video w-full object-contain" loop={loop}
          playsInline preload="metadata" data-testid="player-video"
          onTimeUpdate={(e) => { setT(e.currentTarget.currentTime); onTime?.(e.currentTarget.currentTime); }}
          onLoadedMetadata={(e) => setDur(e.currentTarget.duration)} onPlay={() => { setPlaying(true); activePlayer = api; }} onPause={() => setPlaying(false)}
          onClick={toggle} />
      </div>
      <Scrubber t={t} dur={dur} chapters={chapters} onSeek={seek} />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-0.5">
          {!compact && <Tip content="Start"><Button isIconOnly size="sm" variant="ghost" aria-label="Go to start" onPress={() => seek(0)}><SkipBack size={16} /></Button></Tip>}
          <Tip content="Previous frame  ,"><Button isIconOnly size="sm" variant="ghost" aria-label="Previous frame" onPress={() => step(-1)}><CaretLeft size={16} /></Button></Tip>
          <Button isIconOnly size="sm" aria-label={playing ? "Pause" : "Play"} onPress={toggle} className="rounded-full">
            {playing ? <Pause size={14} weight="fill" /> : <Play size={14} weight="fill" />}
          </Button>
          <Tip content="Next frame  ."><Button isIconOnly size="sm" variant="ghost" aria-label="Next frame" onPress={() => step(1)}><CaretRight size={16} /></Button></Tip>
          <Tip content="Loop"><Button isIconOnly size="sm" variant="ghost" aria-label="Loop" aria-pressed={loop} className={cn(loop ? "text-accent-ink" : "text-muted")} onPress={() => setLoop(!loop)}><Repeat size={16} /></Button></Tip>
          <ToggleButtonGroup size="sm" selectionMode="single" disallowEmptySelection selectedKeys={new Set([String(rate)])} aria-label="Playback speed"
            onSelectionChange={(k) => setRate(Number([...(k as Set<Key>)][0]))} className="ml-1">
            {["0.25", "0.5", "1", "2"].map((r, i) => (
              <ToggleButton key={r} id={r} className="h-7 min-w-0 px-2 text-xs tabular-nums">{i > 0 && <ToggleButtonGroup.Separator />}{r}×</ToggleButton>
            ))}
          </ToggleButtonGroup>
        </div>
        <div className="flex items-center gap-2">
          <span className="whitespace-nowrap font-mono text-xs tabular-nums text-muted">{frame}/{total} · {t.toFixed(2)}s</span>
          <a href={`${src.split("?")[0]}?download=true`} download={downloadName} aria-label="Download"
            className="inline-flex size-8 items-center justify-center rounded-full text-muted transition-colors hover:bg-surface-secondary hover:text-foreground">
            <DownloadSimple size={16} />
          </a>
        </div>
      </div>
    </div>
  );
}

export function Scrubber({ t, dur, chapters = [], onSeek }: { t: number; dur: number; chapters?: Chapter[]; onSeek: (t: number) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const at = (clientX: number) => {
    const r = ref.current!.getBoundingClientRect();
    onSeek(Math.max(0, Math.min(1, (clientX - r.left) / r.width)) * (dur || 0));
  };
  const pct = dur ? (100 * t) / dur : 0;
  return (
    <div className="flex flex-col gap-1">
      <div ref={ref} role="slider" aria-label="Seek" aria-valuemin={0} aria-valuemax={dur} aria-valuenow={t} tabIndex={0}
        onKeyDown={(e) => { if (e.key === "ArrowLeft") onSeek(Math.max(0, t - 0.1)); if (e.key === "ArrowRight") onSeek(Math.min(dur, t + 0.1)); }}
        onPointerDown={(e) => { (e.target as HTMLElement).setPointerCapture?.(e.pointerId); at(e.clientX); }}
        onPointerMove={(e) => { if (e.buttons) at(e.clientX); }}
        className="group relative h-5 cursor-pointer touch-none outline-none">
        <div className="absolute inset-x-0 top-1/2 h-1 -translate-y-1/2 rounded-full bg-surface-tertiary" />
        <div className="absolute left-0 top-1/2 h-1 -translate-y-1/2 rounded-full bg-accent" style={{ width: `${pct}%` }} />
        {chapters.map((c) => (
          <span key={c.t + c.label} className="absolute top-1/2 h-2.5 w-0.5 -translate-y-1/2 rounded bg-muted/60" style={{ left: `${dur ? (100 * c.t) / dur : 0}%` }} />
        ))}
        <span className="absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full bg-surface shadow ring-2 ring-accent" style={{ left: `${pct}%` }} />
      </div>
      {chapters.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {chapters.map((c) => (
            <button key={c.t + c.label} onClick={() => onSeek(c.t + 1e-3)}
              className={cn("rounded-full border px-2 py-0.5 text-xs tabular-nums transition-colors",
                t >= c.t - 0.02 ? "border-accent/40 bg-sunken text-accent-ink" : "border-border text-muted hover:text-foreground")}>
              {c.label} <span className="opacity-60">{c.t.toFixed(2)}s</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function WaveformPlayer({ src, peaks, duration, words, transcript }: {
  src: string; peaks: number[]; duration?: number | null; words?: { text: string; start: number; end: number }[] | null; transcript?: string | null;
}) {
  const a = useRef<HTMLAudioElement>(null);
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const dur = duration || a.current?.duration || 0;
  const pct = dur ? t / dur : 0;
  const bars = peaks.length ? peaks : new Array(120).fill(0.2);
  const seek = (e: React.PointerEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    if (a.current && dur) a.current.currentTime = ((e.clientX - r.left) / r.width) * dur;
  };
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-3">
        <Button isIconOnly size="sm" className="rounded-full" aria-label={playing ? "Pause" : "Play"}
          onPress={() => { const el = a.current; if (el) { if (el.paused) el.play().catch(() => {}); else el.pause(); } }}>
          {playing ? <Pause size={14} weight="fill" /> : <Play size={14} weight="fill" />}
        </Button>
        <div className="relative flex h-12 flex-1 cursor-pointer items-center gap-px" onPointerDown={seek} role="slider" aria-label="Seek audio"
          aria-valuenow={t} aria-valuemin={0} aria-valuemax={dur}>
          {bars.map((p, i) => (
            <span key={i} className={cn("flex-1 rounded-full", i / bars.length <= pct ? "bg-accent" : "bg-surface-tertiary")}
              style={{ height: `${Math.max(6, p * 100)}%` }} />
          ))}
        </div>
        <span className="w-12 text-right font-mono text-xs tabular-nums text-muted">{t.toFixed(1)}s</span>
      </div>
      <audio ref={a} src={src} preload="metadata" onTimeUpdate={(e) => setT(e.currentTarget.currentTime)} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} />
      {words && words.length > 0 ? (
        <p className="text-sm leading-relaxed">
          {words.map((w, i) => (
            <span key={i} onClick={() => { if (a.current) a.current.currentTime = w.start; }}
              className={cn("cursor-pointer rounded px-0.5 transition-colors", t >= w.start && t < w.end ? "bg-accent text-white" : t >= w.end ? "text-foreground" : "text-muted")}>
              {w.text}{" "}
            </span>
          ))}
        </p>
      ) : transcript ? <p className="text-sm text-muted">{transcript}</p> : null}
    </div>
  );
}

type Side = { url: string; kind: string; title?: string; poster?: string | null };

export function ComparisonView({ a, b, mode: mode0 = "slider", labels = ["A", "B"] }: { a: Side; b: Side; mode?: string; labels?: string[] }) {
  const [mode, setMode] = useState(mode0);
  const [split, setSplit] = useState(50);
  const [show, setShow] = useState<"a" | "b">("a");
  const va = useRef<HTMLVideoElement | null>(null);
  const vb = useRef<HTMLVideoElement | null>(null);
  const isVideo = a.kind === "video";
  useEffect(() => {  // keep B locked to A
    if (!isVideo) return;
    const A = va.current, B = vb.current;
    if (!A || !B) return;
    const sync = () => { if (Math.abs(B.currentTime - A.currentTime) > 0.04) B.currentTime = A.currentTime; };
    const play = () => B.play().catch(() => {});
    const pause = () => B.pause();
    A.addEventListener("timeupdate", sync); A.addEventListener("seeked", sync); A.addEventListener("play", play); A.addEventListener("pause", pause);
    return () => { A.removeEventListener("timeupdate", sync); A.removeEventListener("seeked", sync); A.removeEventListener("play", play); A.removeEventListener("pause", pause); };
  }, [isVideo, mode]);
  const media = (s: Side, ref: React.MutableRefObject<HTMLVideoElement | null>, main: boolean, cls = "") =>
    isVideo ? <video ref={ref} src={s.url} poster={s.poster ?? undefined} className={cn("h-full w-full object-contain", cls)} loop muted={!main} playsInline controls={main && mode !== "slider"} />
      : <img src={s.url} alt={s.title ?? ""} className={cn("h-full w-full object-contain", cls)} draggable={false} />;
  return (
    <div className="flex flex-col gap-2" data-testid="comparison">
      <div className="flex items-center justify-between gap-2">
        <ToggleButtonGroup size="sm" selectionMode="single" disallowEmptySelection selectedKeys={new Set([mode])} aria-label="Compare mode"
          onSelectionChange={(k) => setMode(String([...(k as Set<Key>)][0]))}>
          {[["slider", "Slider"], ["side_by_side", "Side by side"], ["toggle", "Toggle"]].map(([id, l], i) => (
            <ToggleButton key={id} id={id} className="h-7 px-2.5 text-xs">{i > 0 && <ToggleButtonGroup.Separator />}{l}</ToggleButton>
          ))}
        </ToggleButtonGroup>
        {isVideo && <Button size="sm" variant="ghost" onPress={() => { const A = va.current; if (A) { if (A.paused) A.play(); else A.pause(); } }}><Play size={14} /> Play both</Button>}
      </div>
      {mode === "side_by_side" ? (
        <div className="grid grid-cols-2 gap-2">
          {[a, b].map((s, i) => (
            <div key={i} className="flex flex-col gap-1">
              <div className="aspect-video overflow-hidden rounded-xl bg-night">{media(s, i === 0 ? va : vb, i === 0)}</div>
              <span className="text-center text-xs text-muted">{labels[i]}</span>
            </div>
          ))}
        </div>
      ) : mode === "toggle" ? (
        <div className="flex flex-col gap-2">
          <div className="relative aspect-video overflow-hidden rounded-xl bg-night">
            <div className={cn("absolute inset-0", show === "a" ? "visible" : "invisible")}>{media(a, va, true)}</div>
            <div className={cn("absolute inset-0", show === "b" ? "visible" : "invisible")}>{media(b, vb, false)}</div>
            <span className="absolute left-2 top-2 rounded-full bg-black/50 px-2 py-0.5 text-xs text-white">{show === "a" ? labels[0] : labels[1]}</span>
          </div>
          <Button size="sm" variant="secondary" onPress={() => setShow(show === "a" ? "b" : "a")}>Show {show === "a" ? labels[1] : labels[0]}</Button>
        </div>
      ) : (
        <div className="relative aspect-video select-none overflow-hidden rounded-xl bg-night">
          <div className="absolute inset-0">{media(b, vb, false)}</div>
          <div className="absolute inset-0" style={{ clipPath: `inset(0 ${100 - split}% 0 0)` }}>{media(a, va, true)}</div>
          <div className="pointer-events-none absolute inset-y-0 w-0.5 bg-white/90 shadow" style={{ left: `${split}%` }} />
          <span className="absolute left-2 top-2 rounded-full bg-black/50 px-2 py-0.5 text-xs text-white">{labels[0]}</span>
          <span className="absolute right-2 top-2 rounded-full bg-black/50 px-2 py-0.5 text-xs text-white">{labels[1]}</span>
          <input type="range" min={0} max={100} step={0.5} value={split} onChange={(e) => setSplit(Number(e.target.value))} aria-label="Compare position"
            data-testid="compare-slider" className="absolute inset-0 h-full w-full cursor-ew-resize opacity-0" />
        </div>
      )}
    </div>
  );
}
