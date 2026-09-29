// One quiet status strip under the chat title: orb + stage · plan progress · budget; plus the Resume banner for
// interrupted runs.
import { Alert, Button, ProgressBar, Spinner } from "@heroui/react";
import { ArrowClockwise, CurrencyDollar, ListChecks, Timer } from "@phosphor-icons/react";
import { useState } from "react";
import { api } from "../lib/api";
import { ACTIVE, type RunState } from "../lib/run";
import { useStore } from "../lib/store";
import { Orb } from "../ui/ai";
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
  const active = ACTIVE.has(run.status);
  const openPlan = () => { setRightTab("plan"); setMainView("inspector"); };
  const costPct = b?.max_cost_usd ? Math.min(100, (100 * b.cost_usd) / b.max_cost_usd) : null;
  return (
    <div className="flex h-11 shrink-0 items-center gap-3 border-b border-separator bg-surface px-4 text-xs" data-testid="run-bar">
      <Orb active={active} size={16} />
      <div className="min-w-0 shrink-0 basis-auto sm:max-w-[38%]">
        {st ? (
          <span className="truncate font-medium">{st.stage} · {Math.round(st.percent)}%{st.eta_s ? <span className="hidden font-normal text-muted sm:inline"> · ~{fmtTime(st.eta_s)} left</span> : null}</span>
        ) : <span className="text-muted">{active ? "Working" : "Idle"}</span>}
      </div>
      {p.total > 0 && (
        <button onClick={openPlan} aria-label="Open the plan"
          className="flex min-w-0 flex-1 items-center gap-2 rounded-lg px-1.5 py-1 text-left outline-none transition-colors hover:bg-surface-secondary focus-visible:ring-2 focus-visible:ring-focus">
          <ListChecks size={14} className="shrink-0 text-muted" />
          <span className="hidden min-w-0 truncate text-muted md:inline">{p.current ? p.current.title : p.done === p.total ? "Plan complete" : "Plan"}</span>
          <span className="ml-auto shrink-0 tabular-nums text-muted" data-testid="plan-progress">{p.done} / {p.total}</span>
          <ProgressBar value={(100 * p.done) / p.total} size="sm" aria-label="Plan progress" className="w-14 shrink-0 sm:w-20">
            <ProgressBar.Track><ProgressBar.Fill /></ProgressBar.Track>
          </ProgressBar>
        </button>
      )}
      {!p.total && <span className="flex-1" />}
      {b && (
        <Tip content={<div className="text-xs leading-relaxed">
          <div>{b.tokens.toLocaleString()} tokens{b.max_tokens ? ` of ${b.max_tokens.toLocaleString()}` : ""}</div>
          <div>{b.pricing_known ? `$${b.cost_usd.toFixed(3)}` : "cost unknown (no pricing)"}{b.max_cost_usd ? ` of $${b.max_cost_usd}` : ""}</div>
          <div>{b.steps} / {b.max_steps} steps · {fmtTime(b.elapsed_s)} of {fmtTime(b.wall_clock_s)}</div>
          <div>ElevenLabs {b.el_chars.toLocaleString()} / {b.el_budget.toLocaleString()} chars</div>
        </div>}>
          <div className="flex shrink-0 items-center gap-2.5 tabular-nums text-muted" data-testid="budget-meter" tabIndex={0}>
            <span className="flex items-center gap-1"><CurrencyDollar size={14} />{b.pricing_known || b.cost_usd ? b.cost_usd.toFixed(2) : "—"}
              {costPct != null && <span className="ml-1 hidden h-1.5 w-8 overflow-hidden rounded-full bg-surface-tertiary sm:inline-block">
                <span className={cn("block h-full", costPct > 85 ? "bg-danger" : "bg-accent")} style={{ width: `${costPct}%` }} /></span>}
            </span>
            <span className="hidden sm:inline">{fmtK(b.tokens)} tok</span>
            <span className="hidden items-center gap-1 sm:flex"><Timer size={14} />{b.steps}/{b.max_steps}</span>
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
