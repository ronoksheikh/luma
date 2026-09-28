// Right-pane panels for long jobs: Plan, Memory, Checkpoints, Artifacts.
import { AlertDialog, Button, Chip, Disclosure, Form, Label, ListBox, Modal, ProgressBar, Select, Spinner, TextArea, TextField } from "@heroui/react";
import {
  ArrowDown, ArrowUp, ArrowsLeftRight, CheckCircle, Circle, CircleDashed, ClockCounterClockwise, DownloadSimple, GitCommit, MinusCircle, PencilSimple, Plus,
  Prohibit, Star, Trash, WarningCircle, XCircle,
} from "@phosphor-icons/react";
import { useCallback, useEffect, useMemo, useState, type Key } from "react";
import { api } from "../lib/api";
import type { Artifact, RunState, Todo } from "../lib/run";
import { useStore } from "../lib/store";
import { Field, cn } from "../ui/kit";
import { ComparisonView } from "../ui/player";
import { FavoriteButton, PresentCard } from "./Cards";

function Empty({ icon, title, children }: { icon: React.ReactNode; title: string; children?: React.ReactNode }) {
  return (
    <div className="flex min-h-[300px] flex-col items-center justify-center gap-3 px-8 text-center">
      <span className="flex size-12 items-center justify-center rounded-2xl bg-offwhite text-accent">{icon}</span>
      <div className="text-[15px] font-medium">{title}</div>
      {children && <p className="max-w-[320px] text-sm text-muted">{children}</p>}
    </div>
  );
}

function ago(ts: number) {
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

function dur(a: number | null, b: number | null) {
  if (!a) return "";
  const s = (b || Date.now() / 1000) - a;
  return s < 60 ? `${Math.round(s)}s` : s < 3600 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

// =============================================================================================== Plan
export function StatusIcon({ status }: { status: string }) {
  if (status === "done") return <CheckCircle size={18} weight="fill" className="shrink-0 text-success" />;
  if (status === "in_progress") return <Spinner size="sm" className="shrink-0" />;
  if (status === "blocked") return <WarningCircle size={18} weight="fill" className="shrink-0 text-warning" />;
  if (status === "failed") return <XCircle size={18} weight="fill" className="shrink-0 text-danger" />;
  if (status === "skipped") return <MinusCircle size={18} className="shrink-0 text-muted" />;
  return <Circle size={18} className="shrink-0 text-muted" />;
}

type Node = Todo & { children: Node[] };

function buildTree(todos: Todo[]): Node[] {
  const by: Record<string, Node[]> = {};
  todos.forEach((t) => { (by[t.parent_id || ""] ||= []).push({ ...t, children: [] }); });
  const attach = (list: Node[]): Node[] => list.sort((a, b) => a.order - b.order).map((n) => ({ ...n, children: attach(by[n.id] || []) }));
  return attach(by[""] || []);
}

export function PlanPanel({ run }: { run: RunState }) {
  const runId = useStore((s) => s.runId);
  const [open, setOpen] = useState<Todo | null>(null);
  const [adding, setAdding] = useState(false);
  const [revisions, setRevisions] = useState<any[]>([]);
  const [, tick] = useState(0);
  useEffect(() => { const i = setInterval(() => tick((x) => x + 1), 1000); return () => clearInterval(i); }, []);
  useEffect(() => {
    if (!runId) return;
    api<any>(`/api/runs/${runId}/plan`).then((r) => setRevisions(r.revisions || [])).catch(() => {});
  }, [runId, run.todos.length, run.items.length > 0 && run.items[run.items.length - 1]?.id]);
  const tree = useMemo(() => buildTree(run.todos), [run.todos]);
  const cur = open ? run.todos.find((t) => t.id === open.id) || open : null;
  if (!runId || !run.todos.length) {
    return (
      <Empty icon={<ListIcon />} title="No plan yet">
        For anything longer than a few steps the director writes a plan first. It appears here live; you can add, edit, reorder or skip items.
        {runId && <Button size="sm" variant="secondary" className="mx-auto mt-3" onPress={() => setAdding(true)}><Plus size={14} /> Add an item</Button>}
        <AddTodoModal open={adding} onClose={() => setAdding(false)} todos={run.todos} />
      </Empty>
    );
  }
  const p = run.plan;
  return (
    <div className="flex flex-col gap-4 p-4" data-testid="plan-panel">
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex justify-between text-sm"><span className="font-medium">Plan</span><span className="tabular-nums text-muted">{p.done} / {p.total} done</span></div>
          <ProgressBar value={p.total ? (100 * p.done) / p.total : 0} size="sm" aria-label="Plan progress" className="mt-1.5"><ProgressBar.Track><ProgressBar.Fill /></ProgressBar.Track></ProgressBar>
        </div>
        <Button size="sm" variant="secondary" onPress={() => setAdding(true)}><Plus size={14} /> Add</Button>
      </div>
      <ul className="flex flex-col gap-1">
        {tree.map((n) => <TodoRow key={n.id} n={n} depth={0} onOpen={setOpen} />)}
      </ul>
      {revisions.length > 0 && (
        <Disclosure>
          <Disclosure.Heading>
            <Button slot="trigger" variant="ghost" size="sm" className="-ml-2 text-muted"><ClockCounterClockwise size={14} /> {revisions.length} plan revision{revisions.length > 1 ? "s" : ""}<Disclosure.Indicator /></Button>
          </Disclosure.Heading>
          <Disclosure.Content><Disclosure.Body>
            <ul className="flex flex-col gap-2 border-l border-border pl-3">
              {revisions.slice().reverse().map((r) => (
                <li key={r.id} className="text-xs"><span className="font-medium">Revision {r.revision}</span> · {r.author} · {ago(r.created_at)}<br /><span className="text-muted">{r.reason} (replaced {r.snapshot.length} items)</span></li>
              ))}
            </ul>
          </Disclosure.Body></Disclosure.Content>
        </Disclosure>
      )}
      <TodoModal todo={cur} todos={run.todos} onClose={() => setOpen(null)} />
      <AddTodoModal open={adding} onClose={() => setAdding(false)} todos={run.todos} />
    </div>
  );
}

function ListIcon() {
  return <CircleDashed size={24} />;
}

function TodoRow({ n, depth, onOpen }: { n: Node; depth: number; onOpen: (t: Todo) => void }) {
  const phase = n.children.length > 0;
  return (
    <li>
      <button onClick={() => onOpen(n)} data-testid="todo-row" data-status={n.status}
        className={cn("flex w-full items-start gap-2.5 rounded-xl px-2.5 py-2 text-left transition-colors hover:bg-surface-secondary", n.status === "in_progress" && "bg-offwhite")}
        style={{ paddingLeft: 10 + depth * 18 }}>
        <span className="mt-px"><StatusIcon status={n.status} /></span>
        <span className="min-w-0 flex-1">
          <span className={cn("block text-[13.5px]", phase && "font-medium", (n.status === "done" || n.status === "skipped") && "text-muted", n.status === "skipped" && "line-through")}>{n.title}</span>
          {n.acceptance_criteria && n.status !== "done" && !phase && <span className="block truncate text-xs text-muted">{n.acceptance_criteria}</span>}
        </span>
        {n.priority === "high" && n.status !== "done" && <Chip size="sm" variant="soft" color="warning">high</Chip>}
        {n.evidence?.length > 0 && <Chip size="sm" variant="soft">{n.evidence.length} evidence</Chip>}
        <span className="shrink-0 text-[11px] tabular-nums text-muted">{dur(n.started_at, n.completed_at)}</span>
      </button>
      {n.children.length > 0 && <ul className="flex flex-col gap-0.5">{n.children.map((c) => <TodoRow key={c.id} n={c} depth={depth + 1} onOpen={onOpen} />)}</ul>}
    </li>
  );
}

function evidenceLink(e: any, projectId: string | null) {
  if (typeof e === "string" && (e.includes("/") || e.includes(".")) && !e.startsWith("art_")) {
    return <a href={`/api/files/${projectId}/${e}`} target="_blank" rel="noreferrer" className="font-mono text-link">{e}</a>;
  }
  if (typeof e === "string") return <span className="font-mono">{e}</span>;
  return <pre className="max-h-32 overflow-auto rounded-lg bg-surface-secondary p-2 font-mono text-[11px]">{JSON.stringify(e, null, 1)}</pre>;
}

function TodoModal({ todo, todos, onClose }: { todo: Todo | null; todos: Todo[]; onClose: () => void }) {
  const { notify, projectId, runId } = useStore();
  const [draft, setDraft] = useState<Partial<Todo>>({});
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (todo) { setDraft({ title: todo.title, detail: todo.detail, acceptance_criteria: todo.acceptance_criteria, priority: todo.priority }); setNote(""); } }, [todo?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!todo) return <Modal.Backdrop isOpen={false}><Modal.Container><Modal.Dialog /></Modal.Container></Modal.Backdrop>;
  const patch = async (body: any, msg?: string) => {
    setBusy(true);
    try {
      await api(`/api/todos/${todo.id}`, { method: "PATCH", json: body });
      if (msg) notify(msg, "ok");
    } catch (e: any) {
      notify(e.message, "bad");
    } finally {
      setBusy(false);
    }
  };
  const move = async (dir: -1 | 1) => {
    const sibs = todos.filter((t) => t.parent_id === todo.parent_id).sort((a, b) => a.order - b.order);
    const i = sibs.findIndex((t) => t.id === todo.id);
    const j = i + dir;
    if (j < 0 || j >= sibs.length) return;
    const order = todos.slice().sort((a, b) => a.order - b.order).map((t) => t.id);
    const a = order.indexOf(sibs[i].id), b = order.indexOf(sibs[j].id);
    [order[a], order[b]] = [order[b], order[a]];
    await api(`/api/runs/${runId}/todos/reorder`, { method: "POST", json: { ids: order } }).catch((e) => notify(e.message, "bad"));
  };
  return (
    <Modal.Backdrop isOpen={!!todo} onOpenChange={(v) => !v && onClose()}>
      <Modal.Container size="lg" scroll="inside">
        <Modal.Dialog className="sm:max-w-[560px]">
          <Modal.CloseTrigger />
          <Modal.Header>
            <div className="flex items-center gap-2"><StatusIcon status={todo.status} /><span className="text-xs uppercase tracking-wide text-muted">{todo.status.replace("_", " ")}</span>
              {todo.started_at && <span className="text-xs text-muted">· {dur(todo.started_at, todo.completed_at)}</span>}</div>
            <Modal.Heading className="mt-1">{todo.title}</Modal.Heading>
          </Modal.Header>
          <Modal.Body>
            <div className="flex flex-col gap-4 text-foreground">
              <Field label="Title" value={draft.title || ""} onChange={(v) => setDraft({ ...draft, title: v })} />
              <TextField value={draft.acceptance_criteria || ""} onChange={(v) => setDraft({ ...draft, acceptance_criteria: v })} className="w-full">
                <Label>Acceptance criteria</Label><TextArea rows={2} className="w-full resize-none" />
              </TextField>
              <TextField value={draft.detail || ""} onChange={(v) => setDraft({ ...draft, detail: v })} className="w-full">
                <Label>Detail</Label><TextArea rows={2} className="w-full resize-none" />
              </TextField>
              <Select selectedKey={draft.priority || "medium"} onSelectionChange={(k) => setDraft({ ...draft, priority: String(k) })} className="w-40">
                <Label>Priority</Label>
                <Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
                <Select.Popover><ListBox>{["high", "medium", "low"].map((p) => <ListBox.Item key={p} id={p} textValue={p}>{p}<ListBox.ItemIndicator /></ListBox.Item>)}</ListBox></Select.Popover>
              </Select>
              {todo.note && <div className="rounded-xl bg-surface-secondary px-3 py-2 text-sm"><span className="text-muted">Note: </span>{todo.note}</div>}
              <div>
                <div className="mb-1 text-sm font-medium">Evidence</div>
                {todo.evidence?.length ? <ul className="flex flex-col gap-1 text-sm">{todo.evidence.map((e, i) => <li key={i}>{evidenceLink(e, projectId)}</li>)}</ul>
                  : <p className="text-sm text-muted">None yet — the director must attach evidence to mark this done.</p>}
              </div>
              <TextField value={note} onChange={setNote} className="w-full"><Label>Reason / note (for skip, block or done)</Label><TextArea rows={2} className="w-full resize-none" /></TextField>
            </div>
          </Modal.Body>
          <Modal.Footer className="flex-wrap justify-between">
            <div className="flex gap-1">
              <Button isIconOnly size="sm" variant="ghost" aria-label="Move up" onPress={() => move(-1)}><ArrowUp size={16} /></Button>
              <Button isIconOnly size="sm" variant="ghost" aria-label="Move down" onPress={() => move(1)}><ArrowDown size={16} /></Button>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="ghost" isDisabled={busy} onPress={() => patch({ status: "skipped", note: note || undefined }, "Skipped").then(onClose)}><Prohibit size={14} /> Skip</Button>
              <Button size="sm" variant="ghost" isDisabled={busy} onPress={() => patch({ status: "blocked", note: note || undefined }, "Marked blocked")}><WarningCircle size={14} /> Block</Button>
              <Button size="sm" variant="secondary" isDisabled={busy} onPress={() => patch({ status: "done", note: note || undefined }, "Marked done")}><CheckCircle size={14} /> Done</Button>
              <Button size="sm" isPending={busy} onPress={() => patch({ ...draft }, "Saved").then(onClose)}>Save</Button>
            </div>
          </Modal.Footer>
        </Modal.Dialog>
      </Modal.Container>
    </Modal.Backdrop>
  );
}

function AddTodoModal({ open, onClose, todos }: { open: boolean; onClose: () => void; todos: Todo[] }) {
  const { runId, notify } = useStore();
  const [title, setTitle] = useState("");
  const [ac, setAc] = useState("");
  const [parent, setParent] = useState<string>("none");
  const phases = todos.filter((t) => !t.parent_id);
  const save = async (e?: React.FormEvent) => {
    e?.preventDefault();
    try {
      await api(`/api/runs/${runId}/todos`, { method: "POST", json: { title, acceptance_criteria: ac, parent_id: parent === "none" ? null : parent } });
      setTitle(""); setAc(""); onClose();
    } catch (err: any) {
      notify(err.message, "bad");
    }
  };
  return (
    <Modal.Backdrop isOpen={open} onOpenChange={(v) => !v && onClose()}>
      <Modal.Container size="sm">
        <Modal.Dialog>
          <Modal.CloseTrigger />
          <Modal.Header><Modal.Heading>Add to the plan</Modal.Heading><p className="text-sm text-muted">The director sees it at its next step.</p></Modal.Header>
          <Form onSubmit={save}>
            <Modal.Body className="flex flex-col gap-4 pt-2">
              <Field label="Title" value={title} onChange={setTitle} autoFocus />
              <Field label="Acceptance criteria" value={ac} onChange={setAc} placeholder="How we'll know it's done" />
              {phases.length > 0 && (
                <Select selectedKey={parent} onSelectionChange={(k) => setParent(String(k))} className="w-full">
                  <Label>Under</Label>
                  <Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
                  <Select.Popover><ListBox>
                    <ListBox.Item id="none" textValue="Top level">Top level<ListBox.ItemIndicator /></ListBox.Item>
                    {phases.map((p) => <ListBox.Item key={p.id} id={p.id} textValue={p.title}>{p.title}<ListBox.ItemIndicator /></ListBox.Item>)}
                  </ListBox></Select.Popover>
                </Select>
              )}
            </Modal.Body>
            <Modal.Footer><Button slot="close" variant="tertiary">Cancel</Button><Button type="submit" isDisabled={!title.trim()}>Add</Button></Modal.Footer>
          </Form>
        </Modal.Dialog>
      </Modal.Container>
    </Modal.Backdrop>
  );
}

// =============================================================================================== Memory
type Mem = { id: string; key: string; value: string; source: string; updated_at: number };

export function MemoryPanel({ version }: { version: number }) {
  const { projectId, notify } = useStore();
  const [items, setItems] = useState<Mem[]>([]);
  const [edit, setEdit] = useState<{ key: string; value: string; isNew: boolean } | null>(null);
  const load = useCallback(() => { if (projectId) api<Mem[]>(`/api/projects/${projectId}/memory`).then(setItems).catch(() => setItems([])); }, [projectId]);
  useEffect(() => { load(); }, [load, version]);
  const save = async () => {
    if (!edit) return;
    try {
      await api(`/api/projects/${projectId}/memory`, { method: "PUT", json: { key: edit.key, value: edit.value } });
      setEdit(null);
      load();
    } catch (e: any) {
      notify(e.message, "bad");
    }
  };
  return (
    <div className="flex flex-col gap-3 p-4" data-testid="memory-panel">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted">Durable facts and decisions, given to every run of this project.</p>
        <Button size="sm" variant="secondary" onPress={() => setEdit({ key: "", value: "", isNew: true })}><Plus size={14} /> Add</Button>
      </div>
      {!items.length ? <Empty icon={<Star size={24} />} title="Nothing remembered yet">The director stores brand colours, chosen voices, approved styles and your preferences here.</Empty> : (
        <ul className="divide-y divide-separator overflow-hidden rounded-2xl border border-border bg-surface">
          {items.map((m) => (
            <li key={m.id} className="group flex items-start gap-3 px-3.5 py-2.5">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2"><span className="font-mono text-[12.5px] font-medium">{m.key}</span>{m.source === "user" && <Chip size="sm" variant="soft" color="accent">you</Chip>}</div>
                <p className="whitespace-pre-wrap break-words text-sm text-foreground/85">{m.value}</p>
              </div>
              <div className="flex opacity-60 group-hover:opacity-100">
                <Button isIconOnly size="sm" variant="ghost" aria-label={`Edit ${m.key}`} onPress={() => setEdit({ key: m.key, value: m.value, isNew: false })}><PencilSimple size={15} /></Button>
                <Button isIconOnly size="sm" variant="ghost" aria-label={`Delete ${m.key}`} onPress={async () => { await api(`/api/projects/${projectId}/memory/${encodeURIComponent(m.key)}`, { method: "DELETE" }); load(); }}><Trash size={15} /></Button>
              </div>
            </li>
          ))}
        </ul>
      )}
      <Modal.Backdrop isOpen={!!edit} onOpenChange={(v) => !v && setEdit(null)}>
        <Modal.Container size="sm"><Modal.Dialog>
          <Modal.CloseTrigger />
          <Modal.Header><Modal.Heading>{edit?.isNew ? "Remember something" : `Edit “${edit?.key}”`}</Modal.Heading></Modal.Header>
          <Modal.Body className="flex flex-col gap-4 pt-2 text-foreground">
            {edit?.isNew && <Field label="Key" value={edit.key} onChange={(v) => setEdit({ ...edit, key: v })} placeholder="e.g. palette, voice_id, never" autoFocus />}
            <TextField value={edit?.value || ""} onChange={(v) => edit && setEdit({ ...edit, value: v })} className="w-full"><Label>Value</Label><TextArea rows={4} className="w-full resize-none" /></TextField>
          </Modal.Body>
          <Modal.Footer><Button slot="close" variant="tertiary">Cancel</Button><Button onPress={save} isDisabled={!edit?.key.trim() || !edit?.value.trim()}>Save</Button></Modal.Footer>
        </Modal.Dialog></Modal.Container>
      </Modal.Backdrop>
    </div>
  );
}

// =============================================================================================== Checkpoints
type Cp = { id: string; label: string; auto: number; created_at: number; meta: any; run_id: string | null };

export function CheckpointsPanel({ version }: { version: number }) {
  const { projectId, notify } = useStore();
  const [items, setItems] = useState<Cp[]>([]);
  const [label, setLabel] = useState("");
  const [confirm, setConfirm] = useState<Cp | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => { if (projectId) api<Cp[]>(`/api/projects/${projectId}/checkpoints`).then(setItems).catch(() => setItems([])); }, [projectId]);
  useEffect(() => { load(); }, [load, version]);
  const create = async () => {
    setBusy(true);
    try {
      await api(`/api/projects/${projectId}/checkpoints`, { method: "POST", json: { label: label.trim() || "Manual checkpoint" } });
      setLabel("");
      load();
    } catch (e: any) {
      notify(e.message, "bad");
    } finally {
      setBusy(false);
    }
  };
  const restore = async () => {
    if (!confirm) return;
    setBusy(true);
    try {
      const r = await api<any>(`/api/projects/${projectId}/checkpoints/${confirm.id}/restore`, { method: "POST" });
      notify(`Restored “${r.restored.label}”. Your previous state was saved as a checkpoint.`, "ok");
      setConfirm(null);
      load();
      useStore.getState().refreshProjects();
    } catch (e: any) {
      notify(e.message, "bad");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex flex-col gap-4 p-4" data-testid="checkpoints-panel">
      <div className="flex items-end gap-2">
        <div className="flex-1"><Field label="New checkpoint" value={label} onChange={setLabel} placeholder="e.g. before trying a darker palette" /></div>
        <Button variant="secondary" isPending={busy} onPress={create}><GitCommit size={16} /> Save</Button>
      </div>
      {!items.length ? <Empty icon={<GitCommit size={24} />} title="No checkpoints yet">Snapshots of the scene code, audio, settings and plan. Renders are checkpointed automatically.</Empty> : (
        <ol className="relative flex flex-col gap-1 border-l border-border pl-4">
          {items.map((c) => (
            <li key={c.id} className="relative flex items-start gap-3 rounded-xl px-2 py-2 hover:bg-surface-secondary">
              <span className={cn("absolute -left-[21px] top-3.5 size-2.5 rounded-full ring-4 ring-surface", c.auto ? "bg-brand-sky" : "bg-accent")} />
              <div className="min-w-0 flex-1">
                <div className="text-sm">{c.label}</div>
                <div className="text-xs text-muted">{ago(c.created_at)}{c.auto ? " · automatic" : ""}{c.meta?.stats ? ` · ${c.meta.stats}` : ""}</div>
              </div>
              <Button size="sm" variant="ghost" onPress={() => setConfirm(c)}>Restore</Button>
            </li>
          ))}
        </ol>
      )}
      <AlertDialog.Backdrop isOpen={!!confirm} onOpenChange={(v) => !v && setConfirm(null)}>
        <AlertDialog.Container><AlertDialog.Dialog className="sm:max-w-[420px]">
          <AlertDialog.CloseTrigger />
          <AlertDialog.Header><AlertDialog.Icon status="warning" /><AlertDialog.Heading>Restore “{confirm?.label}”?</AlertDialog.Heading></AlertDialog.Header>
          <AlertDialog.Body><p>Scene code, audio, output settings and the plan go back to that point. The current state is saved as a checkpoint first, so you can undo this.</p></AlertDialog.Body>
          <AlertDialog.Footer><Button slot="close" variant="tertiary">Cancel</Button><Button onPress={restore} isPending={busy}>Restore</Button></AlertDialog.Footer>
        </AlertDialog.Dialog></AlertDialog.Container>
      </AlertDialog.Backdrop>
    </div>
  );
}

// =============================================================================================== Artifacts
export function ArtifactsPanel({ version }: { version: number }) {
  const { projectId, notify } = useStore();
  const [arts, setArts] = useState<Artifact[]>([]);
  const [favOnly, setFavOnly] = useState(false);
  const [open, setOpen] = useState<Artifact | null>(null);
  const [compare, setCompare] = useState<[Artifact, Artifact] | null>(null);
  const [rename, setRename] = useState<Artifact | null>(null);
  const [del, setDel] = useState<Artifact | null>(null);
  const [sel, setSel] = useState<Record<string, number>>({});
  const load = useCallback(() => { if (projectId) api<Artifact[]>(`/api/projects/${projectId}/artifacts`).then(setArts).catch(() => setArts([])); }, [projectId]);
  useEffect(() => { load(); }, [load, version]);
  const groups = useMemo(() => {
    const g: Record<string, Artifact[]> = {};
    arts.forEach((a) => { (g[a.version_group] ||= []).push(a); });
    return Object.values(g).map((vs) => vs.sort((a, b) => a.version - b.version)).sort((a, b) => b[b.length - 1].created_at - a[a.length - 1].created_at);
  }, [arts]);
  const shown = groups.filter((vs) => !favOnly || vs.some((a) => a.favorite));
  const update = (a: Artifact) => setArts((xs) => xs.map((x) => (x.id === a.id ? a : x)));
  if (!arts.length) return <Empty icon={<Star size={24} />} title="No artifacts yet">Everything the director presents — drafts, stills, audio, files, comparisons — lands on this shelf, versioned.</Empty>;
  return (
    <div className="flex flex-col gap-3 p-4" data-testid="artifacts-panel">
      <div className="flex items-center justify-between gap-2">
        <Button size="sm" variant={favOnly ? "primary" : "ghost"} onPress={() => setFavOnly(!favOnly)}><Star size={14} weight={favOnly ? "fill" : "regular"} /> Favourites</Button>
        <div className="flex gap-2">
          <a href={`/api/projects/${projectId}/artifacts.zip${favOnly ? "?favorites=true" : ""}`} download
            className="inline-flex h-8 items-center gap-1.5 rounded-full border border-border px-3 text-xs font-medium hover:bg-surface-secondary"><DownloadSimple size={14} /> Download all</a>
        </div>
      </div>
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        {shown.map((vs) => {
          const pick = sel[vs[0].version_group] ?? vs[vs.length - 1].version;
          const a = vs.find((x) => x.version === pick) || vs[vs.length - 1];
          const thumb = a.meta?.poster_url || a.meta?.images?.[0]?.url || a.meta?.shots?.[0]?.url || a.meta?.cells?.find((c: any) => c.kind === "image")?.url
            || (a.type === "image" ? a.url : null) || a.meta?.preview?.url;
          return (
            <div key={a.version_group} className="overflow-hidden rounded-2xl border border-border bg-surface" data-testid="artifact-tile">
              <button className="block w-full" onClick={() => setOpen(a)} aria-label={`Open ${a.title}`}>
                <div className="checker flex aspect-video items-center justify-center overflow-hidden bg-surface-secondary">
                  {thumb ? <img src={thumb} alt="" className="h-full w-full object-contain" loading="lazy" />
                    : a.type === "palette" ? <div className="flex h-full w-full">{(a.meta?.colors || []).map((c: any) => <span key={c.hex} className="flex-1" style={{ background: c.hex }} />)}</div>
                    : <span className="text-xs uppercase tracking-wide text-muted">{a.type}</span>}
                </div>
              </button>
              <div className="px-3 pt-2">
                <div className="truncate text-[13px] font-medium">{a.title}</div>
                <div className="text-[11px] text-muted">{a.type} · v{a.version}{vs.length > 1 ? ` of ${vs.length}` : ""} · {ago(a.created_at)}</div>
              </div>
              <div className="flex items-center gap-0.5 px-1.5 pb-1.5">
                {vs.length > 1 && (
                  <Select aria-label="Version" selectedKey={String(pick)} onSelectionChange={(k) => setSel({ ...sel, [a.version_group]: Number(k) })} className="mr-auto w-[74px]">
                    <Select.Trigger className="h-7 min-h-0 px-2 text-xs"><Select.Value /><Select.Indicator /></Select.Trigger>
                    <Select.Popover><ListBox>{vs.map((v) => <ListBox.Item key={v.id} id={String(v.version)} textValue={`v${v.version}`}>v{v.version}<ListBox.ItemIndicator /></ListBox.Item>)}</ListBox></Select.Popover>
                  </Select>
                )}
                {vs.length <= 1 && <span className="mr-auto" />}
                <FavoriteButton art={a} onChange={update} />
                {vs.length > 1 && ["video", "image"].includes(a.type) && (
                  <Button isIconOnly size="sm" variant="ghost" aria-label="Compare versions" onPress={() => {
                    const other = vs.find((v) => v.version !== a.version && v.version === (a.version > 1 ? a.version - 1 : a.version + 1)) || vs.find((v) => v.id !== a.id)!;
                    setCompare(other.version < a.version ? [other, a] : [a, other]);
                  }}><ArrowsLeftRight size={16} /></Button>
                )}
                <Button isIconOnly size="sm" variant="ghost" aria-label="Rename" onPress={() => setRename(a)}><PencilSimple size={15} /></Button>
                <Button isIconOnly size="sm" variant="ghost" aria-label="Delete" onPress={() => setDel(a)}><Trash size={15} /></Button>
              </div>
            </div>
          );
        })}
      </div>
      <Modal.Backdrop isOpen={!!open} onOpenChange={(v) => !v && setOpen(null)}>
        <Modal.Container size="lg" scroll="inside"><Modal.Dialog className="sm:max-w-[860px]">
          <Modal.CloseTrigger />
          <Modal.Body className="pt-8 text-foreground">{open && <PresentCard card={open.type} art={open} />}</Modal.Body>
        </Modal.Dialog></Modal.Container>
      </Modal.Backdrop>
      <CompareModal pair={compare} group={compare ? groups.find((g) => g[0].version_group === compare[0].version_group) || [] : []} onClose={() => setCompare(null)} />
      <RenameArtifact art={rename} onClose={() => setRename(null)} onSaved={(a) => { update(a); setRename(null); }} />
      <AlertDialog.Backdrop isOpen={!!del} onOpenChange={(v) => !v && setDel(null)}>
        <AlertDialog.Container><AlertDialog.Dialog className="sm:max-w-[400px]">
          <AlertDialog.CloseTrigger />
          <AlertDialog.Header><AlertDialog.Icon status="danger" /><AlertDialog.Heading>Delete “{del?.title}” v{del?.version}?</AlertDialog.Heading></AlertDialog.Header>
          <AlertDialog.Body><p>It is removed from the shelf. The file stays in the workspace.</p></AlertDialog.Body>
          <AlertDialog.Footer><Button slot="close" variant="tertiary">Cancel</Button>
            <Button variant="danger" onPress={async () => { try { await api(`/api/artifacts/${del!.id}`, { method: "DELETE" }); load(); } catch (e: any) { notify(e.message, "bad"); } setDel(null); }}>Delete</Button>
          </AlertDialog.Footer>
        </AlertDialog.Dialog></AlertDialog.Container>
      </AlertDialog.Backdrop>
    </div>
  );
}

function CompareModal({ pair, group, onClose }: { pair: [Artifact, Artifact] | null; group: Artifact[]; onClose: () => void }) {
  const [a, setA] = useState<string>("");
  const [b, setB] = useState<string>("");
  useEffect(() => { if (pair) { setA(pair[0].id); setB(pair[1].id); } }, [pair]);
  const A = group.find((x) => x.id === a), B = group.find((x) => x.id === b);
  const side = (x: Artifact) => ({ url: x.url!, kind: x.type, title: `v${x.version}`, poster: x.meta?.poster_url });
  const pickVersion = (value: string, set: (v: string) => void, label: string) => (
    <Select aria-label={label} selectedKey={value} onSelectionChange={(k) => set(String(k as Key))} className="w-24">
      <Select.Trigger className="h-8 min-h-0"><Select.Value /><Select.Indicator /></Select.Trigger>
      <Select.Popover><ListBox>{group.map((v) => <ListBox.Item key={v.id} id={v.id} textValue={`v${v.version}`}>v{v.version}<ListBox.ItemIndicator /></ListBox.Item>)}</ListBox></Select.Popover>
    </Select>
  );
  return (
    <Modal.Backdrop isOpen={!!pair} onOpenChange={(v) => !v && onClose()}>
      <Modal.Container size="lg"><Modal.Dialog className="sm:max-w-[900px]">
        <Modal.CloseTrigger />
        <Modal.Header>
          <Modal.Heading>Compare versions</Modal.Heading>
          <div className="mt-2 flex items-center gap-2 text-sm">{pickVersion(a, setA, "Left version")}<span className="text-muted">vs</span>{pickVersion(b, setB, "Right version")}</div>
        </Modal.Header>
        <Modal.Body className="text-foreground">{A && B && <ComparisonView key={a + b} a={side(A)} b={side(B)} labels={[`v${A.version}`, `v${B.version}`]} />}</Modal.Body>
      </Modal.Dialog></Modal.Container>
    </Modal.Backdrop>
  );
}

function RenameArtifact({ art, onClose, onSaved }: { art: Artifact | null; onClose: () => void; onSaved: (a: Artifact) => void }) {
  const [title, setTitle] = useState("");
  useEffect(() => { if (art) setTitle(art.title); }, [art]);
  return (
    <Modal.Backdrop isOpen={!!art} onOpenChange={(v) => !v && onClose()}>
      <Modal.Container size="sm"><Modal.Dialog>
        <Modal.CloseTrigger />
        <Modal.Header><Modal.Heading>Rename artifact</Modal.Heading></Modal.Header>
        <Form onSubmit={async (e) => { e.preventDefault(); onSaved(await api<Artifact>(`/api/artifacts/${art!.id}`, { method: "PATCH", json: { title } })); }}>
          <Modal.Body className="pt-2"><Field label="Title" value={title} onChange={setTitle} autoFocus /></Modal.Body>
          <Modal.Footer><Button slot="close" variant="tertiary">Cancel</Button><Button type="submit" isDisabled={!title.trim()}>Save</Button></Modal.Footer>
        </Form>
      </Modal.Dialog></Modal.Container>
    </Modal.Backdrop>
  );
}
