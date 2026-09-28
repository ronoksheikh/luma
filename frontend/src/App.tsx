import { LayoutPanelLeft, MessagesSquare, MonitorPlay, Play, Settings2, ShieldAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { Button, Spinner, Tip, TooltipProvider, cn } from "./components/ui";
import { LeftPane } from "./features/LeftPane";
import { RightPane } from "./features/RightPane";
import { SettingsDialog } from "./features/SettingsDialog";
import { Logo, SetupScreen } from "./features/Setup";
import { Timeline } from "./features/Timeline";
import { api } from "./lib/api";
import { ACTIVE, useRunEvents } from "./lib/run";
import { useStore } from "./lib/store";

export default function App() {
  const { server, loadServer, setupDone, refreshProjects, keys } = useStore();
  const [boot, setBoot] = useState<"loading" | "ready" | "error">("loading");
  const [err, setErr] = useState("");

  useEffect(() => {
    Promise.all([loadServer(), refreshProjects()])
      .then(() => setBoot("ready"))
      .catch((e) => { setErr(e.message); setBoot("error"); });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (boot === "loading") return <div className="flex h-full items-center justify-center"><Spinner className="h-6 w-6" /></div>;
  if (boot === "error") return <div className="flex h-full items-center justify-center p-6 text-sm text-bad">Cannot reach the Luma Studio backend: {err}</div>;
  const llmConfigured = !!server?.llm.configured || !!(keys.llmBaseUrl && keys.llmModel);
  return (
    <TooltipProvider>
      {!setupDone && !llmConfigured ? <SetupScreen /> : <Studio />}
      <SettingsDialog />
      <Toast />
    </TooltipProvider>
  );
}

function Studio() {
  const { runId, projectId, setSettingsOpen, refreshProjects, selectProject, setRun, setRightTab, notify, refreshRuns, runs } = useStore();
  const run = useRunEvents(runId);
  const [view, setView] = useState<"left" | "center" | "right">("center");
  const [demoBusy, setDemoBusy] = useState(false);

  // keep project list / runs fresh when a run changes state
  useEffect(() => { refreshProjects(); refreshRuns(); }, [run.status]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === ",") { e.preventDefault(); setSettingsOpen(true); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [setSettingsOpen]);

  const demo = async () => {
    setDemoBusy(true);
    try {
      const r = await api<any>("/api/demo", { method: "POST" });
      await refreshProjects();
      await selectProject(r.project.id);
      setRun(r.run.id);
      setRightTab("preview");
      notify("Demo started — rendering fan_unfold on the sample logo (no keys).", "info");
    } catch (e: any) {
      notify(e.message, "bad");
    } finally {
      setDemoBusy(false);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-12 shrink-0 items-center justify-between gap-3 border-b border-line bg-panel/60 px-3 backdrop-blur">
        <div className="flex items-center gap-2.5">
          <Logo size={26} />
          <span className="text-sm font-semibold tracking-tight">Luma <span className="glow-text">Studio</span></span>
          {runs.length > 1 && (
            <select aria-label="Run" value={runId ?? ""} onChange={(e) => setRun(e.target.value)} className="ml-2 h-7 rounded-md border border-line bg-bg px-1.5 text-xs text-muted">
              {runs.map((r) => <option key={r.id} value={r.id}>{r.kind === "demo" ? "demo" : "run"} · {r.status} · {r.id.slice(-6)}</option>)}
            </select>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <Tip content="Anyone who can reach this port gets a shell and your API credits. Keep it on localhost or behind an authenticating proxy.">
            <span className="hidden items-center gap-1 rounded-md border border-warn/25 bg-warn/5 px-2 py-1 text-[11px] text-warn/90 md:flex"><ShieldAlert className="h-3.5 w-3.5" /> no login</span>
          </Tip>
          <Button size="sm" onClick={demo} loading={demoBusy}><Play className="h-3.5 w-3.5" /> Try the demo</Button>
          <Tip content="Settings (Ctrl/⌘ ,)"><Button size="icon" variant="ghost" aria-label="Settings" onClick={() => setSettingsOpen(true)}><Settings2 className="h-4 w-4" /></Button></Tip>
        </div>
      </header>
      <main className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[296px_minmax(0,1fr)_minmax(400px,42%)]">
        <div className={cn("min-h-0", view === "left" ? "block" : "hidden lg:block")}><LeftPane /></div>
        <div className={cn("min-h-0", view === "center" ? "block" : "hidden lg:block")}>
          {projectId ? <Timeline run={run} /> : <NoProject />}
        </div>
        <div className={cn("min-h-0", view === "right" ? "block" : "hidden lg:block")}><RightPane run={run} /></div>
      </main>
      <nav className="grid grid-cols-3 border-t border-line bg-panel lg:hidden" aria-label="Panes">
        {([["left", LayoutPanelLeft, "Project"], ["center", MessagesSquare, "Director"], ["right", MonitorPlay, "Preview"]] as const).map(([k, Icon, label]) => (
          <button key={k} onClick={() => setView(k)} className={cn("flex flex-col items-center gap-0.5 py-2 text-[11px]", view === k ? "text-accent" : "text-muted")}>
            <Icon className="h-4 w-4" />{label}{k === "center" && ACTIVE.has(run.status) && <span className="sr-only">(running)</span>}
          </button>
        ))}
      </nav>
    </div>
  );
}

function NoProject() {
  const { refreshProjects, selectProject } = useStore();
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
      <div className="text-lg font-semibold">Start a project</div>
      <p className="max-w-sm text-sm text-muted">A project holds your brand assets (up to 20 files), output settings and the director's workspace.</p>
      <Button variant="primary" onClick={async () => { const p = await api<any>("/api/projects", { method: "POST", json: { name: "My first film" } }); await refreshProjects(); selectProject(p.id); }}>New project</Button>
    </div>
  );
}

function Toast() {
  const toast = useStore((s) => s.toast);
  if (!toast) return null;
  return (
    <div role="status" className={cn("fixed bottom-5 left-1/2 z-50 max-w-lg -translate-x-1/2 rounded-xl border px-4 py-2.5 text-sm shadow-2xl backdrop-blur",
      toast.tone === "ok" ? "border-ok/30 bg-ok/15 text-ok" : toast.tone === "bad" ? "border-bad/30 bg-bad/15 text-bad" : "border-line-strong bg-panel-2/95 text-fg")}>
      {toast.text}
    </div>
  );
}
