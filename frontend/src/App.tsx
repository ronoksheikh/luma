import { Button, Drawer, Spinner, Toast, ToggleButton, ToggleButtonGroup } from "@heroui/react";
import { ChatCircleText, FilmSlate, FolderSimplePlus, List } from "@phosphor-icons/react";
import { useEffect, useState, type Key } from "react";
import { AuthScreen } from "./features/Auth";
import { Chat } from "./features/Chat";
import { Inspector } from "./features/Inspector";
import { ProjectSettingsModal } from "./features/ProjectSettings";
import { SettingsModal } from "./features/SettingsModal";
import { Sidebar } from "./features/Sidebar";
import { api, setUnauthorizedHandler, type Project } from "./lib/api";
import { useRunEvents } from "./lib/run";
import { useStore } from "./lib/store";
import { Logo, cn } from "./ui/kit";

export default function App() {
  const { user, authChecked, checkAuth } = useStore();
  const [error, setError] = useState("");

  useEffect(() => {
    setUnauthorizedHandler(() => useStore.setState({ user: null }));
    checkAuth().catch((e) => setError(e.message));
  }, [checkAuth]);

  let body;
  if (error) body = <div className="flex h-full items-center justify-center p-6 text-sm text-danger">Can't reach the Luma Studio server: {error}</div>;
  else if (!authChecked) body = <Splash />;
  else if (!user) body = <AuthScreen />;
  else body = <Studio key={user.id} />;
  return (
    <>
      {body}
      <Toast.Provider placement="bottom" />
    </>
  );
}

function Splash() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-5">
      <Logo size={40} />
      <Spinner size="sm" />
    </div>
  );
}

function Studio() {
  const { loadServer, refreshProjects, projectId, runId, refreshRuns, openSettings, navOpen, setNavOpen, mainView, setMainView } = useStore();
  const [ready, setReady] = useState(false);
  const run = useRunEvents(runId);

  useEffect(() => {
    Promise.all([loadServer(), refreshProjects()]).finally(() => setReady(true));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (ready) { refreshProjects(); refreshRuns(); } }, [run.status]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === ",") { e.preventDefault(); openSettings(); }
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
        <div className="flex min-h-0 flex-1 overflow-hidden bg-surface sm:rounded-2xl sm:border sm:border-border sm:shadow-[0_1px_3px_rgba(16,24,40,0.04)]">
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
    </div>
  );
}

function NoProject() {
  const { refreshProjects, selectProject, setNavOpen } = useStore();
  const [busy, setBusy] = useState(false);
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-4 p-8 text-center">
      <span className="flex size-14 items-center justify-center rounded-2xl bg-offwhite text-accent"><FolderSimplePlus size={28} /></span>
      <div>
        <h2 className="text-[22px]">Start a project</h2>
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
