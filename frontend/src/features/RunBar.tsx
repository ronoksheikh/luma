// Sticky header under the chat title: plan progress · current stage · budget meter; plus the
// Resume banner for interrupted runs.
import { Alert, Button, ProgressBar, Spinner } from "@heroui/react";
import { ArrowClockwise, CurrencyDollar, Gauge, ListChecks, Timer } from "@phosphor-icons/react";
import { useState } from "react";
import { api } from "../lib/api";
import { ACTIVE, type RunState } from "../lib/run";
import { useStore } from "../lib/store";
import { Tip, cn, fmtK } from "../ui/kit";

function fmtTime(s: number) {
  const m = Math.floor(s / 60);
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : m ? `${m}m ${Math.round(s % 60)}s` : `${Math.round(s)}s`;
}

export function RunBar({ run }: { run: RunState }) {
  const { setRightTab, setMainView } = useStore();
  const p = run.plan;
  const b = run.budget;
  const st = run.stage;
  if (!p.total && !st && !b) return null;
  const openPlan = () => { setRightTab("plan"); setMainView("inspector"); };
  const costPct = b?.max_cost_usd ? Math.min(100, (100 * b.cost_usd) / b.max_cost_usd) : null;
  return (
    <div className="flex shrink-0 flex-wrap items-center gap-x-5 gap-y-2 border-b border-separator bg-surface/95 px-4 py-2 backdrop-blur" data-testid="run-bar">
      {p.total > 0 && (
        <button onClick={openPlan} className="flex min-w-[180px] flex-1 items-center gap-2.5 text-left" aria-label="Open the plan">
          <ListChecks size={16} className="shrink-0 text-accent" />
          <div className="min-w-0 flex-1">
            <div className="flex items-center justify-between gap-2 text-xs">
              <span className="truncate">{p.current ? p.current.title : p.done === p.total ? "Plan complete" : "Plan"}</span>
              <span className="shrink-0 tabular-nums text-muted" data-testid="plan-progress">{p.done} / {p.total}</span>
            </div>
            <ProgressBar value={(100 * p.done) / p.total} size="sm" aria-label="Plan progress" className="mt-1">
              <ProgressBar.Track><ProgressBar.Fill /></ProgressBar.Track>
            </ProgressBar>
          </div>
        </button>
      )}
      {st && (
        <div className="flex min-w-[140px] items-center gap-2 text-xs">
          {ACTIVE.has(run.status) ? <Spinner size="sm" /> : <Gauge size={16} className="text-muted" />}
          <div>
            <div className="font-medium">{st.stage} · {Math.round(st.percent)}%</div>
            <div className="text-muted">{st.eta_s ? `~${fmtTime(st.eta_s)} left` : st.detail || ""}</div>
          </div>
        </div>
      )}
      {b && (
        <Tip content={<div className="text-xs leading-relaxed">
          <div>{b.tokens.toLocaleString()} tokens{b.max_tokens ? ` of ${b.max_tokens.toLocaleString()}` : ""}</div>
          <div>{b.pricing_known ? `$${b.cost_usd.toFixed(3)}` : "cost unknown (no pricing)"}{b.max_cost_usd ? ` of $${b.max_cost_usd}` : ""}</div>
          <div>{b.steps} / {b.max_steps} steps · {fmtTime(b.elapsed_s)} of {fmtTime(b.wall_clock_s)}</div>
          <div>ElevenLabs {b.el_chars.toLocaleString()} / {b.el_budget.toLocaleString()} chars</div>
        </div>}>
          <div className="flex items-center gap-3 text-xs tabular-nums text-muted" data-testid="budget-meter" tabIndex={0}>
            <span className="flex items-center gap-1"><CurrencyDollar size={14} />{b.pricing_known || b.cost_usd ? b.cost_usd.toFixed(2) : "—"}
              {costPct != null && <span className={cn("ml-1 inline-block h-1.5 w-10 overflow-hidden rounded-full bg-surface-tertiary")}>
                <span className={cn("block h-full", costPct > 85 ? "bg-danger" : "bg-accent")} style={{ width: `${costPct}%` }} /></span>}
            </span>
            <span>{fmtK(b.tokens)} tok</span>
            <span className="flex items-center gap-1"><Timer size={14} />{b.steps}/{b.max_steps}</span>
          </div>
        </Tip>
      )}
    </div>
  );
}

export function ResumeBanner({ run }: { run: RunState }) {
  const { runId, notify, refreshRuns } = useStore();
  const [busy, setBusy] = useState(false);
  if (run.status !== "interrupted") return null;
  const resume = async () => {
    setBusy(true);
    try {
      const r = await api<any>(`/api/runs/${runId}/resume`, { method: "POST" });
      const bits = [r.jobs_requeued?.length && `${r.jobs_requeued.length} render(s) re-queued`, r.jobs_alive?.length && `${r.jobs_alive.length} job(s) re-attached`].filter(Boolean);
      notify(`Resumed${bits.length ? ": " + bits.join(", ") : ""}.`, "ok");
      refreshRuns();
    } catch (e: any) {
      notify(e.message, "bad");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Alert status="warning" data-testid="resume-banner">
      <Alert.Indicator />
      <Alert.Content>
        <Alert.Title>This run was interrupted</Alert.Title>
        <Alert.Description>The server restarted while the director was working. Resume rebuilds its context from the plan, notes and history, and continues unfinished renders.</Alert.Description>
      </Alert.Content>
      <Button size="sm" onPress={resume} isPending={busy}>{({ isPending }) => <>{isPending ? <Spinner size="sm" color="current" /> : <ArrowClockwise size={16} />}Resume</>}</Button>
    </Alert>
  );
}
