import { AlertDialog, Avatar, Button, Dropdown, Form, Label, Modal, Separator, Spinner } from "@heroui/react";
import { Check, CopySimple, DotsThree, GearSix, PencilSimple, Play, Plus, SignOut, Trash } from "@phosphor-icons/react";
import { useState } from "react";
import { api, type Project } from "../lib/api";
import { useStore } from "../lib/store";
import { useTheme, type ThemeMode } from "../lib/theme";
import { Field, Logo, SectionLabel, THEMES, cn } from "../ui/kit";
import { ToolboxNav } from "./Toolbox";

export function Sidebar() {
  const { projects, projectId, selectProject, refreshProjects, notify, setNavOpen } = useStore();
  const [renaming, setRenaming] = useState<Project | null>(null);
  const [deleting, setDeleting] = useState<Project | null>(null);
  const [creating, setCreating] = useState(false);

  const create = async () => {
    setCreating(true);
    try {
      const p = await api<Project>("/api/projects", { method: "POST", json: { name: "Untitled project" } });
      await refreshProjects();
      await selectProject(p.id);
      setRenaming(p);
      setNavOpen(false);
    } catch (e: any) {
      notify(e.message, "bad");
    } finally {
      setCreating(false);
    }
  };

  const onAction = async (p: Project, key: string) => {
    if (key === "rename") setRenaming(p);
    if (key === "delete") setDeleting(p);
    if (key === "duplicate") {
      const d = await api<Project>(`/api/projects/${p.id}/duplicate`, { method: "POST" });
      await refreshProjects();
      await selectProject(d.id);
      notify("Project duplicated", "ok");
    }
  };

  return (
    <nav aria-label="Projects" className="flex h-full min-h-0 flex-col">
      <div className="flex h-14 shrink-0 items-center px-4">
        <Logo size={26} withName />
      </div>
      <div className="px-3 pb-3">
        <Button variant="secondary" fullWidth onPress={create} isPending={creating} className="justify-start">
          {({ isPending }) => <>{isPending ? <Spinner size="sm" color="current" /> : <Plus size={16} />}New project</>}
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
        <SectionLabel>Projects</SectionLabel>
        <ul className="flex flex-col gap-0.5">
          {projects.map((p) => {
            const active = p.id === projectId;
            const running = p.latest_run && ["running", "waiting_input"].includes(p.latest_run.status);
            return (
              <li key={p.id} className={cn("group relative flex items-center rounded-xl transition-colors",
                active ? "bg-surface shadow-[0_1px_2px_rgba(16,24,40,0.06),0_0_0_1px_var(--border)]" : "hover:bg-surface-tertiary/70")}>
                <button className="min-w-0 flex-1 px-3 py-2 text-left outline-none focus-visible:ring-2 focus-visible:ring-focus rounded-xl"
                  onClick={() => { selectProject(p.id); setNavOpen(false); }}>
                  <div className="flex items-center gap-2">
                    <span className={cn("truncate text-sm", active ? "font-medium text-foreground" : "text-foreground/85")}>{p.name}</span>
                    {running && <span className="size-1.5 shrink-0 animate-breathe rounded-full bg-accent" aria-label="running" />}
                  </div>
                  <div className="truncate text-xs text-muted tabular-nums">
                    {p.kind === "demo" ? "Demo · " : ""}{p.settings.width}×{p.settings.height} · {p.settings.fps} fps · {p.settings.duration}s
                  </div>
                </button>
                <Dropdown>
                  <Button isIconOnly size="sm" variant="ghost" aria-label={`Actions for ${p.name}`}
                    className={cn("mr-1 shrink-0 opacity-0 group-hover:opacity-100 data-[pressed]:opacity-100 focus-visible:opacity-100", active && "opacity-60")}>
                    <DotsThree size={18} weight="bold" />
                  </Button>
                  <Dropdown.Popover placement="bottom end">
                    <Dropdown.Menu onAction={(k) => onAction(p, String(k))}>
                      <Dropdown.Item id="rename" textValue="Rename"><PencilSimple size={16} className="text-muted" /><Label>Rename</Label></Dropdown.Item>
                      <Dropdown.Item id="duplicate" textValue="Duplicate"><CopySimple size={16} className="text-muted" /><Label>Duplicate</Label></Dropdown.Item>
                      <Dropdown.Item id="delete" textValue="Delete" variant="danger"><Trash size={16} className="text-danger-ink" /><Label>Delete</Label></Dropdown.Item>
                    </Dropdown.Menu>
                  </Dropdown.Popover>
                </Dropdown>
              </li>
            );
          })}
        </ul>
        {!projects.length && <p className="px-1 py-2 text-sm text-muted">No projects yet.</p>}
      </div>

      <Separator />
      <ToolboxNav />
      <Separator />
      <div className="flex flex-col gap-1 p-3">
        <DemoButton />
        <UserMenu />
      </div>

      <RenameModal project={renaming} onClose={() => setRenaming(null)} />
      <DeleteDialog project={deleting} onClose={() => setDeleting(null)} />
    </nav>
  );
}

function DemoButton() {
  const { refreshProjects, selectProject, setRun, setRightTab, notify, setNavOpen } = useStore();
  const [busy, setBusy] = useState(false);
  const demo = async () => {
    setBusy(true);
    try {
      const r = await api<any>("/api/demo", { method: "POST" });
      await refreshProjects();
      await selectProject(r.project.id);
      setRun(r.run.id);
      setRightTab("preview");
      setNavOpen(false);
      notify("Demo started: a 5 s logo film rendered from the sample mark, no keys needed.", "info");
    } catch (e: any) {
      notify(e.message, "bad");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Button variant="ghost" fullWidth className="justify-start text-muted" onPress={demo} isPending={busy}>
      {({ isPending }) => <>{isPending ? <Spinner size="sm" color="current" /> : <Play size={16} />}Run the demo</>}
    </Button>
  );
}

function UserMenu() {
  const { user, logout, openSettings } = useStore();
  const name = user?.username ?? "";
  const [mode, setMode] = useTheme();
  return (
    <Dropdown>
      <Dropdown.Trigger className="flex w-full items-center gap-2.5 rounded-xl px-2 py-1.5 text-left outline-none hover:bg-surface-tertiary/70 focus-visible:ring-2 focus-visible:ring-focus">
        <Avatar size="sm" color="accent" variant="soft">
          <Avatar.Fallback>{name.slice(0, 2).toUpperCase()}</Avatar.Fallback>
        </Avatar>
        <span className="min-w-0 flex-1 truncate text-sm">{name}</span>
        <DotsThree size={18} className="text-muted" />
      </Dropdown.Trigger>
      <Dropdown.Popover placement="top start" className="min-w-[220px]">
        <Dropdown.Menu onAction={(k) => {
          if (k === "settings") openSettings("connections");
          if (k === "account") openSettings("account");
          if (k === "logout") logout();
          if (String(k).startsWith("theme-")) setMode(String(k).slice(6) as ThemeMode);
        }}>
          <Dropdown.Item id="settings" textValue="Settings"><GearSix size={16} className="text-muted" /><Label>Settings</Label></Dropdown.Item>
          <Dropdown.Item id="account" textValue="Account"><PencilSimple size={16} className="text-muted" /><Label>Account & password</Label></Dropdown.Item>
          <Dropdown.Section aria-label="Theme">
            {THEMES.map(({ id, label, icon: Icon }) => (
              <Dropdown.Item key={id} id={`theme-${id}`} textValue={`Theme: ${label}`}>
                <Icon size={16} className="text-muted" /><Label>{label} theme</Label>
                {mode === id && <Check size={14} weight="bold" className="ms-auto text-accent-ink" aria-label="selected" />}
              </Dropdown.Item>
            ))}
          </Dropdown.Section>
          <Dropdown.Item id="logout" textValue="Sign out" variant="danger"><SignOut size={16} className="text-danger-ink" /><Label>Sign out</Label></Dropdown.Item>
        </Dropdown.Menu>
      </Dropdown.Popover>
    </Dropdown>
  );
}

function RenameModal({ project, onClose }: { project: Project | null; onClose: () => void }) {
  const refreshProjects = useStore((s) => s.refreshProjects);
  const [name, setName] = useState("");
  const [lastId, setLastId] = useState<string | null>(null);
  if (project && project.id !== lastId) {
    setLastId(project.id);
    setName(project.name);
  }
  const save = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (project && name.trim()) {
      await api(`/api/projects/${project.id}`, { method: "PATCH", json: { name: name.trim() } });
      await refreshProjects();
    }
    setLastId(null);
    onClose();
  };
  return (
    <Modal.Backdrop isOpen={!!project} onOpenChange={(v) => { if (!v) { setLastId(null); onClose(); } }}>
      <Modal.Container size="sm">
        <Modal.Dialog>
          <Modal.CloseTrigger />
          <Modal.Header><Modal.Heading>Name your project</Modal.Heading></Modal.Header>
          <Form onSubmit={save}>
            <Modal.Body className="pt-2">
              <Field label="Project name" value={name} onChange={setName} autoFocus />
            </Modal.Body>
            <Modal.Footer>
              <Button slot="close" variant="tertiary">Cancel</Button>
              <Button type="submit" isDisabled={!name.trim()}>Save</Button>
            </Modal.Footer>
          </Form>
        </Modal.Dialog>
      </Modal.Container>
    </Modal.Backdrop>
  );
}

function DeleteDialog({ project, onClose }: { project: Project | null; onClose: () => void }) {
  const { refreshProjects, projectId, notify } = useStore();
  const del = async () => {
    if (!project) return;
    try {
      await api(`/api/projects/${project.id}`, { method: "DELETE" });
      if (project.id === projectId) {
        localStorage.removeItem("luma.project");
        useStore.setState({ projectId: null, runId: null, runs: [] });
      }
      await refreshProjects();
    } catch (e: any) {
      notify(e.message, "bad");
    }
    onClose();
  };
  return (
    <AlertDialog.Backdrop isOpen={!!project} onOpenChange={(v) => !v && onClose()}>
      <AlertDialog.Container>
        <AlertDialog.Dialog className="sm:max-w-[400px]">
          <AlertDialog.CloseTrigger />
          <AlertDialog.Header>
            <AlertDialog.Icon status="danger" />
            <AlertDialog.Heading>Delete “{project?.name}”?</AlertDialog.Heading>
          </AlertDialog.Header>
          <AlertDialog.Body>
            <p>Its assets, renders and deliverables are removed and running jobs are stopped. This can't be undone.</p>
          </AlertDialog.Body>
          <AlertDialog.Footer>
            <Button slot="close" variant="tertiary">Cancel</Button>
            <Button variant="danger" onPress={del}>Delete project</Button>
          </AlertDialog.Footer>
        </AlertDialog.Dialog>
      </AlertDialog.Container>
    </AlertDialog.Backdrop>
  );
}
