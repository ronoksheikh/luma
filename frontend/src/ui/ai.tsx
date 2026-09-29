// AI status components: dependency-free, brand-coloured, reduced-motion safe (see .orb / .shimmer-text in index.css).
// Patterns follow the common "AI elements" set: a shimmering live label, an agent orb, a collapsible thinking trace.
import { Button, Disclosure, Skeleton } from "@heroui/react";
import { Brain, CaretRight } from "@phosphor-icons/react";
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { cn } from "./kit";

/** Text with a light sweeping across it while `active` (a live status such as "Thinking…"). */
export function Shimmer({ children, active = true, className }: { children: ReactNode; active?: boolean; className?: string }) {
  return <span className={cn(active && "shimmer-text", className)}>{children}</span>;
}

/** The agent orb: a brand-gradient sphere that spins a highlight and breathes while `active`. */
export function Orb({ active = false, size = 20, className }: { active?: boolean; size?: number; className?: string }) {
  return <span aria-hidden data-active={active} className={cn("orb", className)} style={{ "--s": `${size}px` } as CSSProperties} />;
}

/** Whole seconds since mount while `active`; freezes when it stops. */
export function useElapsedSeconds(active: boolean): number | null {
  const start = useRef<number | null>(null);
  const [secs, setSecs] = useState<number | null>(null);
  useEffect(() => {
    if (!active) return;
    start.current ??= Date.now();
    const tick = () => setSecs(Math.max(1, Math.round((Date.now() - start.current!) / 1000)));
    tick();
    const i = setInterval(tick, 1000);
    return () => clearInterval(i);
  }, [active]);
  return secs;
}

/**
 * The model's reasoning: open with a shimmering "Thinking…" and a live timer while it streams, then collapses to a
 * quiet "Thought for 8 s" row (or "Thought process" when the duration wasn't observed, e.g. after a reload).
 */
export function ThinkingBlock({ text, streaming }: { text: string; streaming: boolean }) {
  const secs = useElapsedSeconds(streaming);
  const [manual, setManual] = useState<boolean | null>(null);
  const open = manual ?? streaming;
  const logRef = useRef<HTMLParagraphElement>(null);
  useEffect(() => { if (streaming && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight; }, [text, streaming]);
  return (
    <Disclosure isExpanded={open} onExpandedChange={setManual} data-testid="thinking">
      <Disclosure.Heading>
        <Button slot="trigger" variant="ghost" size="sm" className="-ml-2 h-7 gap-1.5 px-2 text-[13px] font-normal text-muted">
          <Brain size={15} />
          {streaming ? <Shimmer>Thinking{secs ? ` · ${secs} s` : "…"}</Shimmer> : <span>{secs ? `Thought for ${secs} s` : "Thought process"}</span>}
          <CaretRight size={12} className={cn("transition-transform duration-150", open && "rotate-90")} />
        </Button>
      </Disclosure.Heading>
      <Disclosure.Content>
        <Disclosure.Body>
          <p ref={logRef} className="mt-1 max-h-56 overflow-y-auto whitespace-pre-wrap border-l-2 border-border pl-3 text-[13px] leading-relaxed text-muted">{text}</p>
        </Disclosure.Body>
      </Disclosure.Content>
    </Disclosure>
  );
}

/** Loading placeholders that echo the real layout instead of a bare spinner. */
export function SkeletonRows({ rows = 4, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn("flex flex-col gap-3 p-4", className)} aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex items-center gap-3">
          <Skeleton className="size-9 shrink-0 rounded-xl" />
          <div className="flex flex-1 flex-col gap-2">
            <Skeleton className="h-3.5 rounded-md" style={{ width: `${62 - (i % 3) * 12}%` }} />
            <Skeleton className="h-3 w-4/5 rounded-md" />
          </div>
        </div>
      ))}
    </div>
  );
}

/** App-shell skeleton shown while the session and project list load. */
export function ShellSkeleton() {
  return (
    <div className="flex h-full" aria-busy="true" aria-label="Loading Luma Studio">
      <aside className="hidden w-[264px] shrink-0 flex-col gap-3 p-4 lg:flex">
        <div className="flex items-center gap-2.5"><Orb size={26} /><Skeleton className="h-4 w-28 rounded-md" /></div>
        <Skeleton className="mt-2 h-9 w-full rounded-xl" />
        {[0, 1, 2].map((i) => <Skeleton key={i} className="h-11 w-full rounded-xl" />)}
      </aside>
      <main className="flex min-w-0 flex-1 p-0 sm:p-2 lg:pl-0">
        <div className="flex flex-1 flex-col gap-4 overflow-hidden bg-surface p-6 sm:rounded-2xl sm:border sm:border-border">
          <Skeleton className="h-5 w-40 rounded-md" />
          <Skeleton className="h-4 w-3/5 rounded-md" />
          <Skeleton className="mt-2 aspect-video w-full max-w-[520px] rounded-2xl" />
        </div>
      </main>
    </div>
  );
}
