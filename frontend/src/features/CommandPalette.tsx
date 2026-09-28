// ⌘K / Ctrl+K: jump to a project, artifact or todo; start the demo; export all; open settings.
import { Input, Kbd, ListBox, Modal } from "@heroui/react";
import { DownloadSimple, FolderSimple, GearSix, ListChecks, MagnifyingGlass, Play, Plus, Star } from "@phosphor-icons/react";
import { useEffect, useMemo, useState } from "react";
import { api, type Project } from "../lib/api";
import type { Artifact, RunState } from "../lib/run";
import { useStore } from "../lib/store";

type Cmd = { id: string; group: string; label: string; hint?: string; icon: any; run: () => void };

export function CommandPalette({ open, onClose, run }: { open: boolean; onClose: () => void; run: RunState }) {
  const s = useStore();
  const [q, setQ] = useState("");
  const [arts, setArts] = useState<Artifact[]>([]);
  useEffect(() => {
    if (!open) return;
    setQ("");
    if (s.projectId) api<Artifact[]>(`/api/projects/${s.projectId}/artifacts`).then(setArts).catch(() => setArts([]));
  }, [open, s.projectId]);
  const go = (tab: string) => { s.setRightTab(tab); s.setMainView("inspector"); };
  const cmds = useMemo<Cmd[]>(() => {
    const out: Cmd[] = [
      { id: "new", group: "Actions", label: "New project", icon: Plus, run: async () => { const p = await api<Project>("/api/projects", { method: "POST", json: { name: "Untitled project" } }); await s.refreshProjects(); s.selectProject(p.id); } },
      { id: "demo", group: "Actions", label: "Start the demo", hint: "keyless 5 s render", icon: Play, run: async () => {
        const r = await api<any>("/api/demo", { method: "POST" });
        await s.refreshProjects(); await s.selectProject(r.project.id); s.setRun(r.run.id); go("preview");
      } },
      ...(s.projectId ? [{ id: "export", group: "Actions", label: "Export all artifacts", hint: "zip", icon: DownloadSimple, run: () => { window.location.href = `/api/projects/${s.projectId}/artifacts.zip`; } }] : []),
      { id: "settings", group: "Actions", label: "Settings", hint: "⌘ ,", icon: GearSix, run: () => s.openSettings() },
      { id: "plan", group: "Actions", label: "Open the plan", icon: ListChecks, run: () => go("plan") },
      { id: "artifacts", group: "Actions", label: "Open artifacts", icon: Star, run: () => go("artifacts") },
    ];
    for (const p of s.projects) out.push({ id: `p_${p.id}`, group: "Projects", label: p.name, hint: `${p.settings.width}×${p.settings.height}`, icon: FolderSimple, run: () => s.selectProject(p.id) });
    for (const a of arts) out.push({ id: `a_${a.id}`, group: "Artifacts", label: `${a.title} v${a.version}`, hint: a.type, icon: Star, run: () => go("artifacts") });
    for (const t of run.todos) out.push({ id: `t_${t.id}`, group: "Plan", label: t.title, hint: t.status.replace("_", " "), icon: ListChecks, run: () => go("plan") });
    return out;
  }, [s.projects, s.projectId, arts, run.todos]); // eslint-disable-line react-hooks/exhaustive-deps
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  const shown = cmds.filter((c) => words.every((w) => `${c.label} ${c.group} ${c.hint ?? ""}`.toLowerCase().includes(w))).slice(0, 60);
  const exec = (id: string) => {
    const c = cmds.find((x) => x.id === id);
    onClose();
    if (c) Promise.resolve(c.run()).catch((e) => s.notify(e.message, "bad"));
  };
  return (
    <Modal.Backdrop isOpen={open} onOpenChange={(v) => !v && onClose()}>
      <Modal.Container size="md" placement="top">
        <Modal.Dialog className="p-0 sm:max-w-[560px]" aria-label="Command palette">
          <div className="flex items-center gap-2 border-b border-separator px-4 py-3">
            <MagnifyingGlass size={18} className="text-muted" />
            <Input autoFocus aria-label="Search commands" placeholder="Jump to a project, artifact or todo, or run a command…" value={q}
              onChange={(e) => setQ(e.target.value)} className="border-0 bg-transparent shadow-none focus:ring-0"
              onKeyDown={(e) => { if (e.key === "Enter" && shown[0]) { e.preventDefault(); exec(shown[0].id); } }} />
            <Kbd>esc</Kbd>
          </div>
          <ListBox aria-label="Commands" className="max-h-[50vh] overflow-y-auto p-2" onAction={(k) => exec(String(k))}
            renderEmptyState={() => <div className="px-3 py-6 text-center text-sm text-muted">No matches</div>}>
            {shown.map((c) => (
              <ListBox.Item key={c.id} id={c.id} textValue={c.label}>
                <c.icon size={16} className="shrink-0 text-muted" />
                <span className="min-w-0 flex-1 truncate">{c.label}</span>
                <span className="text-xs text-muted">{c.hint ?? c.group}</span>
              </ListBox.Item>
            ))}
          </ListBox>
        </Modal.Dialog>
      </Modal.Container>
    </Modal.Backdrop>
  );
}
