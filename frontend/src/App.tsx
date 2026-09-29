import { AlertDialog, Button, Drawer, Spinner, Toast, ToggleButton, ToggleButtonGroup } from "@heroui/react";
import { ChatCircleText, FilmSlate, FolderSimplePlus, List } from "@phosphor-icons/react";
import { useEffect, useRef, useState, type Key } from "react";
import { AuthScreen } from "./features/Auth";
import { Chat } from "./features/Chat";
import { CommandPalette } from "./features/CommandPalette";
import { TemplatesModal } from "./features/Toolbox";
import { useLiveNotifications } from "./features/Notifications";
import { Inspector } from "./features/Inspector";
import { ProjectSettingsModal } from "./features/ProjectSettings";
import { SettingsModal } from "./features/SettingsModal";
import { Sidebar } from "./features/Sidebar";
import { api, setUnauthorizedHandler, type Project } from "./lib/api";
import { ACTIVE, useRunEvents } from "./lib/run";
import { playerKeys } from "./ui/player";
import { useStore } from "./lib/store";
import { ShellSkeleton } from "./ui/ai";
import { cn } from "./ui/kit";

export default function App() {
  const { user, authChecked, checkAuth } = useStore();
  const narrow = useNarrow();
  const [error, setError] = useState("");

  useEffect(() => {
    setUnauthorizedHandler(() => useStore.setState({ user: null }));
    checkAuth().catch((e) => setError(e.message));
  }, [checkAuth]);

  let body;
  if (error) body = <div className="flex h-full items-center justify-center p-6 text-sm text-danger-ink">Can't reach the Luma Studio server: {error}</div>;
  else if (!authChecked) body = <Splash />;
  else if (!user) body = <AuthScreen />;
  else body = <Studio key={user.id} />;
  return (
    <>
      {body}
      <Toast.Provider placement={narrow ? "top" : "bottom end"} />
    </>
  );
}

function Splash() {
  return <ShellSkeleton />;
}

/** True below the `lg` breakpoint, where the bottom bar appears: toasts move to the top so they never cover it or the composer. */
function useNarrow() {
  const [narrow, setNarrow] = useState(() => window.matchMedia("(max-width: 1023px)").matches);
  useEffect(() => {
    const m = window.matchMedia("(max-width: 1023px)");
    const h = () => setNarrow(m.matches);
    m.addEventListener("change", h);
    return () => m.removeEventListener("change", h);
  }, []);
  return narrow;
}

function Studio() {
  const { loadServer, refreshProjects, projectId, runId, refreshRuns, openSettings, navOpen, setNavOpen, mainView, setMainView } = useStore();
  const [ready, setReady] = useState(false);
  const [palette, setPalette] = useState(false);
  const [confirmStop, setConfirmStop] = useState(false);
  const run = useRunEvents(runId);
  useLiveNotifications(run);
  const runRef = useRef(run);
  runRef.current = run;

  useEffect(() => {
    Promise.all([loadServer(), refreshProjects()]).finally(() => setReady(true));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (ready) { refreshProjects(); refreshRuns(); } }, [run.status]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === ",") { e.preventDefault(); openSettings(); return; }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette((v) => !v); return; }
      const t = e.target as HTMLElement | null;
      const typing = !!t?.closest("input,textarea,select,[contenteditable=true],.xterm,[role=dialog]");
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
      const r = runRef.current;
      if (e.key === "Escape" && ACTIVE.has(r.status)) { e.preventDefault(); setConfirmStop(true); return; }
      if ((e.key === "a" || e.key === "A") && r.pendingRequest?.reqKind === "approval") {
        e.preventDefault();
        const rid = useStore.getState().runId;
        api(`/api/runs/${rid}/requests/${r.pendingRequest.id}/answer`, { method: "POST", json: { choice: r.pendingRequest.data.choices[0] } })
          .then(() => useStore.getState().notify("Approved", "ok")).catch((err) => useStore.getState().notify(err.message, "bad"));
        return;
      }
      if (playerKeys(e)) e.preventDefault();
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [openSettings]);

  if (!ready) return <Splash />;
  return (
    <div className="flex h-full">
      <aside className="hidden w-[264px] shrink-0 lg:block"><Sidebar /></aside>
      <Drawer.Backdrop isOpen={navOpen} onOpenChange={setNavOpen}>
        <Drawer.Content placement="left">
          <Drawer.Dialog className="w-[296px] max-w-[85vw] p-0" aria-label="Projects">
            <Sidebar />
          </Drawer.Dialog>
        </Drawer.Content>
      </Drawer.Backdrop>

      <main className="flex min-w-0 flex-1 flex-col p-0 sm:p-2 lg:pl-0">
        <div className="flex min-h-0 flex-1 overflow-hidden bg-surface sm:rounded-2xl sm:border sm:border-border sm:shadow-soft">
          {projectId ? (
            <>
              <div className={cn("min-h-0 min-w-0 flex-1", mainView === "chat" ? "flex" : "hidden lg:flex", "flex-col")}><Chat run={run} /></div>
              <div className={cn("min-h-0 w-full flex-col border-separator lg:w-[44%] lg:min-w-[400px] lg:max-w-[680px] lg:border-l", mainView === "inspector" ? "flex" : "hidden lg:flex")}>
                <Inspector run={run} />
              </div>
            </>
          ) : <NoProject />}
        </div>
        {projectId && (
          <div className="relative flex justify-center border-t border-separator bg-surface p-2 lg:hidden">
            <Button isIconOnly variant="ghost" aria-label="Projects" className="absolute left-2 top-2" onPress={() => setNavOpen(true)}><List size={18} /></Button>
            <ToggleButtonGroup selectionMode="single" disallowEmptySelection selectedKeys={new Set([mainView])} aria-label="View"
              onSelectionChange={(k) => setMainView([...(k as Set<Key>)][0] as "chat" | "inspector")}>
              <ToggleButton id="chat"><ChatCircleText size={16} /> Director</ToggleButton>
              <ToggleButton id="inspector"><ToggleButtonGroup.Separator /><FilmSlate size={16} /> Studio</ToggleButton>
            </ToggleButtonGroup>
          </div>
        )}
      </main>
      <SettingsModal />
      <ProjectSettingsModal />
      <CommandPalette open={palette} onClose={() => setPalette(false)} run={run} />
      <TemplatesModal />
      <AlertDialog.Backdrop isOpen={confirmStop} onOpenChange={setConfirmStop}>
        <AlertDialog.Container><AlertDialog.Dialog className="sm:max-w-[380px]">
          <AlertDialog.Header><AlertDialog.Icon status="danger" /><AlertDialog.Heading>Stop the run?</AlertDialog.Heading></AlertDialog.Header>
          <AlertDialog.Body><p>The model stream, pending requests, the running command and render jobs are cancelled. You can continue later with a message.</p></AlertDialog.Body>
          <AlertDialog.Footer>
            <Button slot="close" variant="tertiary">Keep going</Button>
            <Button variant="danger" onPress={async () => { setConfirmStop(false); await api(`/api/runs/${runId}/cancel`, { method: "POST" }).catch(() => {}); }}>Stop</Button>
          </AlertDialog.Footer>
        </AlertDialog.Dialog></AlertDialog.Container>
      </AlertDialog.Backdrop>
    </div>
  );
}

function NoProject() {
  const { refreshProjects, selectProject, setNavOpen } = useStore();
  const [busy, setBusy] = useState(false);
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-4 p-8 text-center">
      <span className="flex size-14 items-center justify-center rounded-2xl bg-sunken text-accent-ink"><FolderSimplePlus size={28} /></span>
      <div>
        <h2 className="text-[20px]">Start a project</h2>
        <p className="mx-auto mt-1 max-w-sm text-sm text-muted">A project holds your brand assets, output settings and the director's workspace.</p>
      </div>
      <div className="flex gap-2">
        <Button isPending={busy} onPress={async () => {
          setBusy(true);
          try {
            const p = await api<Project>("/api/projects", { method: "POST", json: { name: "My first film" } });
            await refreshProjects();
            await selectProject(p.id);
          } finally {
            setBusy(false);
          }
        }}>{({ isPending }) => <>{isPending && <Spinner size="sm" color="current" />}New project</>}</Button>
        <Button variant="secondary" className="lg:hidden" onPress={() => setNavOpen(true)}>Projects</Button>
      </div>
    </div>
  );
}
