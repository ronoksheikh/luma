// The self-extending Toolbox: tools (global / project), skills, plugins and templates.
// Tool detail: manifest, README, source, tests (+ run), "Try it" form from the JSON Schema, usage, history with
// diff + revert, enable / promote / delete. Also the timeline cards for toolbox events and the templates gallery.
import { AlertDialog, Button, Chip, Disclosure, Input, Label, ListBox, Modal, Popover, Select, Spinner, Switch, Tabs, TextArea, TextField } from "@heroui/react";
import {
  ArrowCounterClockwise, ArrowFatLinesUp, BookOpen, CaretDown, CheckCircle, Code, FilmStrip, Flask, Funnel, GitDiff, Globe, FolderSimple, MagnifyingGlass, Play,
  Plus, PuzzlePiece, Sparkle, Toolbox as ToolboxIcon, Trash, Warning, XCircle,
} from "@phosphor-icons/react";
import { useCallback, useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type Project } from "../lib/api";
import { useStore } from "../lib/store";
import { SkeletonRows } from "../ui/ai";
import { cn } from "../ui/kit";
import { highlightCode } from "../ui/highlight";

export type ToolRow = {
  id: string; name: string; scope: "global" | "project"; project_id: string | null; version: string; enabled: boolean; status: string;
  test_status: string; author: string; description: string; tags: string[]; stats: any; deprecated: any; created_from_run: string | null;
  overrides_global?: boolean; shadowed?: boolean; updated_at: number;
};
type FileDiff = { file: string; status: string; diff: string };

const STATUS_COLOR: Record<string, "success" | "danger" | "warning" | "default" | "accent"> = {
  enabled: "success", failing: "danger", invalid: "danger", modified: "warning", deprecated: "warning", disabled: "default",
};

// ------------------------------------------------------------------------------------------------ small pieces
export function StatusChip({ status }: { status: string }) {
  return <Chip size="sm" variant="soft" color={STATUS_COLOR[status] ?? "default"} data-testid="tool-status">{status}</Chip>;
}

function ScopeChip({ scope }: { scope: string }) {
  return (
    <Chip size="sm" variant="soft" color={scope === "global" ? "accent" : "default"}>
      {scope === "global" ? <Globe size={12} /> : <FolderSimple size={12} />}{scope}
    </Chip>
  );
}

export function Code_({ code, language, maxH = "420px" }: { code: string; language?: string; maxH?: string }) {
  const html = useMemo(() => highlightCode(code, language), [code, language]);
  const lines = html.split("\n");
  return (
    <pre className="hljs overflow-auto rounded-xl bg-surface-secondary py-2 font-mono text-[12px] leading-[1.55]" style={{ maxHeight: maxH }}>
      {lines.map((l, i) => (
        <div key={i} className="flex">
          <span className="w-10 shrink-0 select-none pr-3 text-right text-muted">{i + 1}</span>
          <span className="whitespace-pre pr-3" dangerouslySetInnerHTML={{ __html: l || " " }} />
        </div>
      ))}
    </pre>
  );
}

/** Unified diff text with coloured lines. */
export function DiffText({ diff, maxH = "380px" }: { diff: string; maxH?: string }) {
  const lines = (diff || "").split("\n");
  return (
    <pre className="overflow-auto rounded-xl bg-surface-secondary py-2 font-mono text-[12px] leading-[1.5]" style={{ maxHeight: maxH }} data-testid="diff">
      {lines.map((l, i) => (
        <div key={i} className={cn("whitespace-pre px-3",
          l.startsWith("+++") || l.startsWith("---") ? "text-muted" : l.startsWith("+") ? "bg-success/10 text-success-ink" :
            l.startsWith("-") ? "bg-danger/10 text-danger-ink" : l.startsWith("@@") ? "text-accent-ink" : l.startsWith("diff ") ? "font-medium text-foreground" : "")}>
          {l || " "}
        </div>
      ))}
    </pre>
  );
}

/** Per-file diffs (collapsible). */
export function FileDiffs({ diffs, open = false }: { diffs: FileDiff[]; open?: boolean }) {
  return (
    <div className="flex flex-col gap-1.5">
      {diffs.map((d) => {
        const add = d.diff.split("\n").filter((l) => l.startsWith("+") && !l.startsWith("+++")).length;
        const del = d.diff.split("\n").filter((l) => l.startsWith("-") && !l.startsWith("---")).length;
        return (
          <Disclosure key={d.file} defaultExpanded={open}>
            <Disclosure.Heading>
              <Button slot="trigger" variant="ghost" size="sm" fullWidth className="justify-start gap-2 font-mono text-[12px]">
                <GitDiff size={14} className="text-muted" />{d.file}
                <span className="text-success-ink">+{add}</span>{del > 0 && <span className="text-danger-ink">−{del}</span>}
                <span className="ms-auto text-xs text-muted">{d.status}</span><Disclosure.Indicator />
              </Button>
            </Disclosure.Heading>
            <Disclosure.Content><Disclosure.Body><DiffText diff={d.diff} /></Disclosure.Body></Disclosure.Content>
          </Disclosure>
        );
      })}
    </div>
  );
}

function TestChip({ t }: { t?: any }) {
  if (!t?.status) return null;
  const ok = t.status === "passed";
  return (
    <Chip size="sm" variant="soft" color={ok ? "success" : "danger"} data-testid="test-chip">
      {ok ? <CheckCircle size={12} weight="fill" /> : <XCircle size={12} weight="fill" />}
      tests {t.status}{t.passed != null ? ` · ${t.passed} passed${t.failed ? `, ${t.failed} failed` : ""}` : ""}
    </Chip>
  );
}

function statsLine(st: any) {
  if (!st?.calls) return "never called";
  return `${st.calls} call${st.calls === 1 ? "" : "s"} · ${Math.round((st.success_rate ?? 0) * 100)}% ok${st.avg_s != null ? ` · avg ${st.avg_s}s` : ""}`;
}

// ------------------------------------------------------------------------------------------------ timeline cards
const EVENT_TITLE: Record<string, (d: any) => string> = {
  tool_created: (d) => `Created tool ${d.name} v${d.version}`,
  tool_tested: (d) => `Tested ${d.name} v${d.version}`,
  tool_registered: (d) => `New tool available: ${d.name}`,
  tool_updated: (d) => `Updated ${d.name} ${d.previous ?? ""} → ${d.version}`,
  tool_promoted: (d) => `Promoted ${d.name} to the global toolbox (v${d.version})`,
  tool_disabled: (d) => d.deleted ? `Deleted tool ${d.name}` : `Tool ${d.name} disabled`,
  skill_written: (d) => `${d.created ? "New skill" : "Updated skill"}: ${d.name}`,
  plugin_created: (d) => `${d.kind} plugin ${d.name} v${d.version}${d.enabled ? "" : " (not enabled)"}`,
  template_saved: (d) => `Template saved: ${d.title || d.name}`,
};

export function ToolboxEventCard({ ev, data }: { ev: string; data: any }) {
  const openToolbox = useStore((s) => s.openToolbox);
  const title = (EVENT_TITLE[ev] || ((d: any) => `${ev} ${d.name}`))(data);
  const warn = ev === "tool_disabled" || (data.test && data.test.status && data.test.status !== "passed") || data.enabled === false;
  const Icon = ev.startsWith("skill") ? BookOpen : ev.startsWith("plugin") ? PuzzlePiece : ev.startsWith("template") ? FilmStrip : ToolboxIcon;
  if (ev === "tool_registered") {
    return (
      <div className="flex items-center gap-2.5 rounded-2xl border border-accent/30 bg-sunken px-4 py-3" data-testid="toolbox-registered">
        <span className="text-lg" aria-hidden>🧰</span>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium">New tool available: <span className="font-mono">{data.name}</span> <span className="text-muted">v{data.version}</span></div>
          <div className="truncate text-xs text-muted">{data.description}</div>
        </div>
        <ScopeChip scope={data.scope} />
        <Button size="sm" variant="ghost" onPress={() => openToolbox("tools")}>Open</Button>
      </div>
    );
  }
  return (
    <div className={cn("rounded-2xl border p-4", warn ? "border-warning/40 bg-warning/5" : "border-border bg-surface")} data-testid={`toolbox-${ev}`}>
      <div className="flex flex-wrap items-center gap-2">
        <Icon size={18} weight="fill" className={warn ? "text-warning-ink" : "text-accent-ink"} />
        <span className="text-sm font-medium">{title}</span>
        {data.scope && <ScopeChip scope={data.scope} />}
        <TestChip t={data.test} />
        {data.scan && !data.scan.ok && <Chip size="sm" variant="soft" color="danger"><Warning size={12} />static checks</Chip>}
        {data.registered && <Chip size="sm" variant="soft" color="success">re-registered</Chip>}
      </div>
      {data.description && <p className="mt-1 text-[13px] text-muted">{data.description}</p>}
      {data.reason && <p className="mt-1 text-[13px] text-muted">{data.reason}</p>}
      {ev === "template_saved" && data.thumbnail && <img src={data.thumbnail} alt="" className="mt-2 max-h-40 rounded-xl border border-border" />}
      {ev === "template_saved" && data.params && <p className="mt-1 text-xs text-muted">params: {data.params.join(", ")}</p>}
      {ev === "tool_disabled" && data.traces?.length > 0 && (
        <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded-xl bg-surface-secondary p-2 font-mono text-xs">{data.traces.join("\n\n")}</pre>
      )}
      {data.scan?.blocking?.length > 0 && (
        <ul className="mt-2 flex flex-col gap-0.5 text-xs text-danger-ink">
          {data.scan.blocking.map((f: any, i: number) => <li key={i} className="font-mono">{f.file}:{f.line} [{f.rule}] {f.message}</li>)}
        </ul>
      )}
      {data.diff?.length > 0 && <div className="mt-2"><FileDiffs diffs={data.diff} /></div>}
      {data.test?.output && data.test.status !== "passed" && (
        <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded-xl bg-surface-secondary p-2 font-mono text-xs">{data.test.output.slice(-3000)}</pre>
      )}
    </div>
  );
}

/** The promotion part of an approval card: diff, tests, README. */
export function PromotionDetails({ d }: { d: any }) {
  return (
    <div className="mt-3 flex flex-col gap-2" data-testid="promotion-details">
      <div className="flex items-center gap-2"><TestChip t={d.tests} /><span className="text-xs text-muted">{d.diff?.length} file(s)</span></div>
      <FileDiffs diffs={d.diff || []} open={false} />
      {d.readme && (
        <Disclosure>
          <Disclosure.Heading>
            <Button slot="trigger" variant="ghost" size="sm" className="gap-1.5"><BookOpen size={14} />README<Disclosure.Indicator /></Button>
          </Disclosure.Heading>
          <Disclosure.Content><Disclosure.Body><div className="md rounded-xl bg-surface p-3"><ReactMarkdown remarkPlugins={[remarkGfm]}>{d.readme}</ReactMarkdown></div></Disclosure.Body></Disclosure.Content>
        </Disclosure>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ the panel
export function ToolboxPanel({ version }: { version: number }) {
  const { toolboxView, openToolbox } = useStore();
  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="toolbox-panel">
      <div className="flex shrink-0 gap-1 overflow-x-auto px-3 pb-1 pt-2">
        {[["tools", "Tools", ToolboxIcon], ["skills", "Skills", BookOpen], ["plugins", "Plugins", PuzzlePiece], ["templates", "Templates", FilmStrip]].map(([id, label, Icon]: any) => (
          <Button key={id} size="sm" variant={toolboxView === id ? "secondary" : "ghost"} onPress={() => openToolbox(id)} aria-pressed={toolboxView === id}>
            <Icon size={15} />{label}
          </Button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {toolboxView === "skills" ? <SkillsList version={version} /> : toolboxView === "plugins" ? <PluginsList version={version} />
          : toolboxView === "templates" ? <TemplatesGallery /> : <ToolsList version={version} />}
      </div>
    </div>
  );
}

function FilterSelect({ label, value, options, onChange }: { label: string; value: string; options: [string, string][]; onChange: (v: string) => void }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs font-medium text-muted">{label}</span>
      <Select selectedKey={value} onSelectionChange={(k) => onChange(String(k))} className="w-full" aria-label={label}>
        <Select.Trigger className="h-8 text-sm"><Select.Value /><Select.Indicator /></Select.Trigger>
        <Select.Popover><ListBox>{options.map(([id, l]) => <ListBox.Item key={id} id={id} textValue={l}>{l}<ListBox.ItemIndicator /></ListBox.Item>)}</ListBox></Select.Popover>
      </Select>
    </div>
  );
}

function ToolsList({ version }: { version: number }) {
  const { projectId } = useStore();
  const [rows, setRows] = useState<ToolRow[] | null>(null);
  const [q, setQ] = useState("");
  const [scope, setScope] = useState("all");
  const [status, setStatus] = useState("all");
  const [author, setAuthor] = useState("all");
  const [tag, setTag] = useState<string | null>(null);
  const [open, setOpen] = useState<ToolRow | null>(null);
  const [creating, setCreating] = useState(false);
  const load = useCallback(() => {
    const p = new URLSearchParams();
    if (projectId) p.set("project_id", projectId);
    if (scope !== "all") p.set("scope", scope);
    if (status !== "all") p.set("status", status);
    if (author !== "all") p.set("author", author);
    if (tag) p.set("tag", tag);
    if (q.trim()) p.set("q", q.trim());
    api<ToolRow[]>(`/api/toolbox/tools?${p}`).then(setRows).catch(() => setRows([]));
  }, [projectId, scope, status, author, tag, q]);
  useEffect(() => { const t = setTimeout(load, q ? 250 : 0); return () => clearTimeout(t); }, [load, version, q]);
  const tags = useMemo(() => Array.from(new Set((rows || []).flatMap((r) => r.tags || []))).sort().slice(0, 18), [rows]);
  const active = [scope, status, author].filter((v) => v !== "all").length + (tag ? 1 : 0);
  return (
    <div className="flex flex-col gap-2 p-3">
      <div className="flex items-center gap-2">
        <div className="relative min-w-0 flex-1">
          <MagnifyingGlass size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
          <Input aria-label="Search tools" placeholder="Search tools…" value={q} onChange={(e) => setQ(e.target.value)} className="h-8 w-full pl-8 text-sm" />
        </div>
        <Popover>
          <Button size="sm" variant="secondary" aria-label="Filters" className="shrink-0">
            <Funnel size={14} /><span className="hidden sm:inline">Filters</span>
            {active > 0 && <span className="flex size-4 items-center justify-center rounded-full bg-accent text-xs font-medium text-accent-foreground">{active}</span>}
          </Button>
          <Popover.Content placement="bottom end" className="w-[288px]">
            <Popover.Dialog className="flex flex-col gap-3 p-3" aria-label="Tool filters">
              <FilterSelect label="Scope" value={scope} onChange={setScope} options={[["all", "All scopes"], ["global", "Global"], ["project", "This project"]]} />
              <FilterSelect label="Status" value={status} onChange={setStatus}
                options={[["all", "Any status"], ["enabled", "Enabled"], ["disabled", "Disabled"], ["failing", "Failing"], ["modified", "Modified"], ["deprecated", "Deprecated"]]} />
              <FilterSelect label="Author" value={author} onChange={setAuthor} options={[["all", "Any author"], ["agent", "Agent"], ["user", "User"]]} />
              {tags.length > 0 && (
                <div className="flex flex-col gap-1.5">
                  <span className="text-xs font-medium text-muted">Tags</span>
                  <div className="flex flex-wrap gap-1">
                    {tags.map((t) => (
                      <button key={t} onClick={() => setTag(tag === t ? null : t)} aria-pressed={tag === t}
                        className={cn("rounded-full px-2 py-0.5 text-xs transition-colors", tag === t ? "bg-accent text-accent-foreground" : "bg-surface-secondary text-muted hover:text-foreground")}>
                        #{t}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {active > 0 && <Button size="sm" variant="ghost" onPress={() => { setScope("all"); setStatus("all"); setAuthor("all"); setTag(null); }}>Clear filters</Button>}
            </Popover.Dialog>
          </Popover.Content>
        </Popover>
        <Button size="sm" variant="secondary" className="shrink-0" onPress={() => setCreating(true)} aria-label="New tool"><Plus size={14} /><span className="hidden sm:inline">New tool</span></Button>
      </div>
      {rows === null ? <SkeletonRows rows={5} className="px-1" /> : rows.length === 0 ? (
        <p className="py-8 text-center text-sm text-muted">No tools match. The director builds tools as it works — or author one with “New tool”.</p>
      ) : (
        <ul className="flex flex-col">
          {rows.map((r) => (
            <li key={r.id}>
              <button onClick={() => setOpen(r)} data-testid="tool-row" data-name={r.name}
                className="flex w-full flex-col gap-0.5 rounded-xl px-3 py-2.5 text-left outline-none transition-colors hover:bg-surface-secondary focus-visible:ring-2 focus-visible:ring-focus">
                <div className="flex items-center gap-2">
                  <span className="min-w-0 truncate font-mono text-[13px] font-medium">{r.name}</span>
                  <span className="shrink-0 text-xs text-muted">v{r.version}</span>
                  {r.status !== "enabled" && <StatusChip status={r.status} />}
                  {r.overrides_global && <Chip size="sm" variant="soft" color="warning">overrides global</Chip>}
                  {r.shadowed && <Chip size="sm" variant="soft">shadowed</Chip>}
                  <span className="ms-auto flex shrink-0 items-center gap-1 text-xs text-muted" title={`${r.scope} tool by ${r.author}`}>
                    {r.scope === "global" ? <Globe size={12} /> : <FolderSimple size={12} />}{r.author === "agent" ? <Sparkle size={12} /> : null}
                  </span>
                </div>
                <p className="line-clamp-1 text-[13px] text-muted">{r.description}</p>
                <div className="text-xs tabular-nums text-muted">{statsLine(r.stats)}{r.stats?.last_error && r.status === "failing" ? ` · last error: ${String(r.stats.last_error).slice(0, 80)}` : ""}</div>
              </button>
            </li>
          ))}
        </ul>
      )}
      <ToolDetail row={open} onClose={() => setOpen(null)} onChanged={load} />
      <NewToolModal open={creating} onClose={() => setCreating(false)} onCreated={load} />
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ tool detail
function ToolDetail({ row, onClose, onChanged }: { row: ToolRow | null; onClose: () => void; onChanged: () => void }) {
  const { projectId, notify } = useStore();
  const [d, setD] = useState<any>(null);
  const [tab, setTab] = useState("overview");
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmDel, setConfirmDel] = useState(false);
  const [promo, setPromo] = useState<any>(null);
  const qs = row?.scope === "project" ? `?project_id=${row.project_id}` : "";
  const base = row ? `/api/toolbox/tools/${row.scope}/${row.name}` : "";
  const load = useCallback(() => { if (row) api(`${base}${qs}`).then(setD).catch((e) => notify(e.message, "bad")); }, [row, base, qs]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setD(null); setTab("overview"); setPromo(null); load(); }, [load]);
  const act = async (name: string, fn: () => Promise<any>, ok?: string) => {
    setBusy(name);
    try {
      const r = await fn();
      if (ok) notify(ok, "ok");
      load();
      onChanged();
      return r;
    } catch (e: any) {
      notify(e.message, "bad");
    } finally {
      setBusy(null);
    }
  };
  if (!row) return null;
  return (
    <Modal.Backdrop isOpen={!!row} onOpenChange={(v) => !v && onClose()}>
      <Modal.Container size="lg" scroll="inside">
        <Modal.Dialog className="sm:max-w-[920px]" aria-label={`Tool ${row.name}`} data-testid="tool-detail">
          <Modal.CloseTrigger />
          <Modal.Header className="flex flex-col items-start gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <Modal.Heading className="font-mono">{row.name}</Modal.Heading>
              <span className="text-sm text-muted">v{d?.version ?? row.version}</span>
              <ScopeChip scope={row.scope} />
              <StatusChip status={d?.status ?? row.status} />
            </div>
            <p className="text-sm text-muted">{d?.description ?? row.description}</p>
            <div className="flex flex-wrap items-center gap-2">
              <Switch isSelected={(d?.status ?? row.status) === "enabled"} isDisabled={!!busy} aria-label="Enabled"
                onChange={(on) => act("enable", () => api(`${base}/${on ? "enable" : "disable"}${qs}`, { method: "POST" }), on ? "Tool registered" : "Tool disabled")}>
                <Switch.Content><Switch.Control><Switch.Thumb /></Switch.Control><Label className="text-sm">Enabled</Label></Switch.Content>
              </Switch>
              {row.scope === "project" && (
                <Button size="sm" variant="secondary" isPending={busy === "promote-preview"}
                  onPress={() => act("promote-preview", () => api(`/api/toolbox/tools/project/${row.name}/promotion${qs}`).then(setPromo))}>
                  <ArrowFatLinesUp size={14} />Promote to global
                </Button>
              )}
              <Button size="sm" variant="ghost" className="text-danger-ink" onPress={() => setConfirmDel(true)}><Trash size={14} />Delete</Button>
            </div>
          </Modal.Header>
          <Modal.Body>
            {!d ? <Spinner size="sm" /> : promo ? (
              <PromotionPane row={row} p={promo} busy={busy === "promote"} onCancel={() => setPromo(null)}
                onPromote={() => act("promote", () => api(`/api/toolbox/tools/project/${row.name}/promote${qs}`, { method: "POST" }), "Promoted to the global toolbox").then((r) => { if (r) onClose(); })} />
            ) : (
              <Tabs selectedKey={tab} onSelectionChange={(k) => setTab(String(k))}>
                <Tabs.ListContainer><Tabs.List aria-label="Tool sections">
                  {[["overview", "Overview"], ["source", "Source"], ["tests", "Tests"], ["try", "Try it"], ["usage", "Usage"], ["history", "History"]].map(([id, l]) => (
                    <Tabs.Tab key={id} id={id}>{l}<Tabs.Indicator /></Tabs.Tab>
                  ))}
                </Tabs.List></Tabs.ListContainer>
                <Tabs.Panel id="overview" className="flex flex-col gap-3 pt-3">
                  {d.deprecated && <div className="rounded-xl bg-warning/10 px-3 py-2 text-sm">Deprecated: {d.deprecated.reason}{d.deprecated.replacement ? ` → use ${d.deprecated.replacement}` : ""}</div>}
                  <div className="md rounded-xl border border-border p-4" data-testid="tool-readme"><ReactMarkdown remarkPlugins={[remarkGfm]}>{d.files["README.md"] || "_No README_"}</ReactMarkdown></div>
                  <div>
                    <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted">tool.yaml</div>
                    <Code_ code={d.files["tool.yaml"] || ""} language="yaml" maxH="320px" />
                  </div>
                  {d.files.fixtures?.length > 0 && <p className="text-xs text-muted">fixtures: {d.files.fixtures.map((f: any) => `${f.name} (${f.size} B)`).join(", ")}</p>}
                  {d.scan && !d.scan.ok && (
                    <ul className="flex flex-col gap-0.5 rounded-xl bg-danger/5 p-3 text-xs text-danger-ink">
                      {d.scan.blocking.map((f: any, i: number) => <li key={i} className="font-mono">{f.file}:{f.line} [{f.rule}] {f.message}</li>)}
                    </ul>
                  )}
                </Tabs.Panel>
                <Tabs.Panel id="source" className="pt-3"><Code_ code={d.files["main.py"] || ""} language="python" maxH="60vh" /></Tabs.Panel>
                <Tabs.Panel id="tests" className="flex flex-col gap-3 pt-3">
                  <div className="flex items-center gap-2">
                    <Chip size="sm" variant="soft" color={d.test_status === "passed" ? "success" : d.test_status === "failed" ? "danger" : "default"}>tests {d.test_status}</Chip>
                    <Button size="sm" variant="secondary" isPending={busy === "test"} onPress={() => act("test", () => api(`${base}/test${qs}`, { method: "POST" }))}>
                      <Flask size={14} />Run tests
                    </Button>
                  </div>
                  <Code_ code={d.files["test_tool.py"] || ""} language="python" maxH="340px" />
                  {d.test_output && <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-xl bg-night p-3 font-mono text-xs text-white/85" data-testid="test-output">{d.test_output}</pre>}
                </Tabs.Panel>
                <Tabs.Panel id="try" className="pt-3"><TryIt row={row} detail={d} onRan={load} projectId={projectId} /></Tabs.Panel>
                <Tabs.Panel id="usage" className="flex flex-col gap-3 pt-3">
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                    {[["Calls", d.stats?.calls ?? 0], ["Success", d.stats?.success_rate != null ? `${Math.round(d.stats.success_rate * 100)}%` : "—"],
                      ["Avg duration", d.stats?.avg_s != null ? `${d.stats.avg_s}s` : "—"], ["Fail streak", d.stats?.fail_streak ?? 0]].map(([k, v]) => (
                      <div key={k as string} className="rounded-xl border border-border px-3 py-2"><div className="text-xs text-muted">{k}</div><div className="text-lg tabular-nums">{v}</div></div>
                    ))}
                  </div>
                  {d.stats?.last_error && <pre className="max-h-32 overflow-auto whitespace-pre-wrap rounded-xl bg-danger/5 p-2 font-mono text-xs text-danger-ink">{d.stats.last_error}</pre>}
                  <table className="w-full text-left text-xs">
                    <thead className="text-muted"><tr><th className="py-1">When</th><th>Run</th><th>Source</th><th>Status</th><th>Duration</th></tr></thead>
                    <tbody>
                      {d.recent_calls.map((c: any) => (
                        <tr key={c.id} className="border-t border-border">
                          <td className="py-1 tabular-nums">{new Date(c.created_at * 1000).toLocaleString()}</td>
                          <td>{c.run_id ? <RunLink runId={c.run_id} projectId={c.project_id} /> : "—"}</td>
                          <td>{c.source}</td>
                          <td className={c.status === "success" ? "text-success-ink" : "text-danger-ink"} title={c.error || ""}>{c.status}</td>
                          <td className="tabular-nums">{c.duration_s.toFixed(2)}s</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {!d.recent_calls.length && <p className="text-sm text-muted">Not called yet.</p>}
                </Tabs.Panel>
                <Tabs.Panel id="history" className="pt-3"><History row={row} versions={d.versions} base={base} qs={qs} onReverted={() => { load(); onChanged(); }} /></Tabs.Panel>
              </Tabs>
            )}
          </Modal.Body>
        </Modal.Dialog>
      </Modal.Container>
      <AlertDialog.Backdrop isOpen={confirmDel} onOpenChange={setConfirmDel}>
        <AlertDialog.Container><AlertDialog.Dialog className="sm:max-w-[420px]">
          <AlertDialog.Header><AlertDialog.Icon status="danger" /><AlertDialog.Heading>Delete {row.name}?</AlertDialog.Heading></AlertDialog.Header>
          <AlertDialog.Body><p>{row.scope === "global" ? "Every project loses this tool." : "It is removed from this project."} Its history stays in the git repository.</p></AlertDialog.Body>
          <AlertDialog.Footer>
            <Button slot="close" variant="tertiary">Cancel</Button>
            <Button variant="danger" onPress={() => { setConfirmDel(false); act("delete", () => api(`${base}${qs}`, { method: "DELETE" }), "Tool deleted").then(onClose); }}>Delete</Button>
          </AlertDialog.Footer>
        </AlertDialog.Dialog></AlertDialog.Container>
      </AlertDialog.Backdrop>
    </Modal.Backdrop>
  );
}

function RunLink({ runId, projectId }: { runId: string; projectId: string | null }) {
  const { selectProject, setRun } = useStore();
  return (
    <button className="font-mono text-link" onClick={async () => { if (projectId) await selectProject(projectId); setRun(runId); }}>{runId.slice(0, 10)}</button>
  );
}

function PromotionPane({ row, p, busy, onCancel, onPromote }: { row: ToolRow; p: any; busy: boolean; onCancel: () => void; onPromote: () => void }) {
  const blocked = p.findings?.length > 0;
  return (
    <div className="flex flex-col gap-3" data-testid="promotion-pane">
      <div className="text-sm">Promote <span className="font-mono">{row.name}</span> to the global toolbox{p.replaces_global ? ` (replaces global v${p.replaces_global})` : ""}. Review the code, tests and README.</div>
      {blocked ? (
        <div className="rounded-xl bg-danger/5 p-3 text-sm text-danger-ink">
          <div className="mb-1 font-medium">Blocked by the promotion scan — parameterize these first:</div>
          <ul className="flex flex-col gap-0.5 font-mono text-xs">{p.findings.map((f: any, i: number) => <li key={i}>{f.file}:{f.line} [{f.rule}] {f.message}</li>)}</ul>
        </div>
      ) : <div className="rounded-xl bg-success/10 px-3 py-2 text-sm text-success-ink">The scan found no project-specific values or secrets.</div>}
      <PromotionDetails d={p} />
      <div className="flex justify-end gap-2">
        <Button variant="tertiary" onPress={onCancel}>Back</Button>
        <Button isDisabled={blocked} isPending={busy} onPress={onPromote}><ArrowFatLinesUp size={16} />Approve & promote</Button>
      </div>
    </div>
  );
}

function History({ row, versions, base, qs, onReverted }: { row: ToolRow; versions: any[]; base: string; qs: string; onReverted: () => void }) {
  const notify = useStore((s) => s.notify);
  const tags: string[] = Array.from(new Set(versions.flatMap((v) => v.versions || [])));
  const [a, setA] = useState<string>(tags[1] ?? tags[0] ?? "");
  const [b, setB] = useState<string>(tags[0] ?? "");
  const [diff, setDiff] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const show = async () => {
    try {
      const r = await api<{ diff: string }>(`${base}/diff${qs}${qs ? "&" : "?"}a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`);
      setDiff(r.diff || "(no changes)");
    } catch (e: any) { notify(e.message, "bad"); }
  };
  const revert = async (v: string) => {
    setBusy(true);
    try {
      const r = await api<any>(`${base}/revert${qs}`, { method: "POST", json: { version: v } });
      notify(`Restored v${v} as v${r.version}`, "ok");
      onReverted();
    } catch (e: any) { notify(e.message, "bad"); } finally { setBusy(false); }
  };
  return (
    <div className="flex flex-col gap-3" data-testid="tool-history">
      {tags.length >= 2 && (
        <div className="flex flex-wrap items-end gap-2">
          <FilterSelect label="From" value={a} onChange={setA} options={tags.map((t) => [t, `v${t}`])} />
          <span className="pb-1.5 text-muted">→</span>
          <FilterSelect label="To" value={b} onChange={setB} options={tags.map((t) => [t, `v${t}`])} />
          <Button size="sm" variant="secondary" onPress={show}><GitDiff size={14} />Show diff</Button>
        </div>
      )}
      {diff !== null && <DiffText diff={diff} maxH="360px" />}
      <ul className="flex flex-col gap-1.5">
        {versions.map((v) => (
          <li key={v.sha} className="flex items-center gap-2 rounded-xl border border-border px-3 py-2 text-sm">
            <span className="font-mono text-xs text-muted">{v.sha.slice(0, 7)}</span>
            <span className="min-w-0 flex-1 truncate">{v.message}</span>
            {(v.versions || []).map((t: string) => <Chip key={t} size="sm" variant="soft">v{t}</Chip>)}
            <span className="text-xs text-muted">{v.author} · {new Date(v.ts * 1000).toLocaleString()}</span>
            {(v.versions || []).length > 0 && v.versions[0] !== row.version && (
              <Button size="sm" variant="ghost" isPending={busy} onPress={() => revert(v.versions[0])}><ArrowCounterClockwise size={14} />Revert</Button>
            )}
          </li>
        ))}
      </ul>
      {!versions.length && <p className="text-sm text-muted">No history yet.</p>}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ "Try it"
export function SchemaForm({ schema, value, onChange }: { schema: any; value: Record<string, any>; onChange: (v: Record<string, any>) => void }) {
  const props: Record<string, any> = schema?.properties || {};
  const req = new Set<string>(schema?.required || []);
  const set = (k: string, v: any) => onChange({ ...value, [k]: v });
  return (
    <div className="grid gap-3 sm:grid-cols-2" data-testid="schema-form">
      {Object.entries(props).map(([k, p]) => {
        const types = Array.isArray(p.type) ? p.type : [p.type];
        const label = `${k}${req.has(k) ? " *" : ""}`;
        const desc = p.description;
        if (p.enum) {
          return (
            <Select key={k} selectedKey={value[k] ?? null} onSelectionChange={(x) => set(k, String(x))} aria-label={k} className="w-full">
              <Label>{label}</Label>
              <Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
              <Select.Popover><ListBox>{p.enum.map((o: any) => <ListBox.Item key={String(o)} id={String(o)} textValue={String(o)}>{String(o)}<ListBox.ItemIndicator /></ListBox.Item>)}</ListBox></Select.Popover>
              {desc && <p className="text-xs text-muted">{desc}</p>}
            </Select>
          );
        }
        if (types.includes("boolean") && types.length === 1) {
          return (
            <Switch key={k} isSelected={!!value[k]} onChange={(v) => set(k, v)} aria-label={k}>
              <Switch.Content><Switch.Control><Switch.Thumb /></Switch.Control><Label className="text-sm">{label}</Label></Switch.Content>
            </Switch>
          );
        }
        if (types.includes("number") || types.includes("integer")) {
          if (!types.includes("string")) {
            return (
              <TextField key={k} value={value[k] == null ? "" : String(value[k])} className="w-full"
                onChange={(v) => set(k, v === "" ? undefined : types.includes("integer") ? parseInt(v, 10) : parseFloat(v))}>
                <Label>{label}</Label><Input type="number" step={types.includes("integer") ? 1 : "any"} aria-label={k} />
                {desc && <p className="text-xs text-muted">{desc}</p>}
              </TextField>
            );
          }
        }
        if (types.includes("array") || types.includes("object")) {
          const [text, err] = [typeof value[k] === "string" ? value[k] : value[k] === undefined ? "" : JSON.stringify(value[k]), null];
          return (
            <TextField key={k} value={text} onChange={(v) => { try { set(k, v.trim() ? JSON.parse(v) : undefined); } catch { set(k, v); } }} className="w-full sm:col-span-2">
              <Label>{label} <span className="text-muted">(JSON)</span></Label><TextArea rows={2} className="font-mono text-xs" aria-label={k} />
              {(desc || err) && <p className="text-xs text-muted">{desc}</p>}
            </TextField>
          );
        }
        return (
          <TextField key={k} value={value[k] == null ? "" : String(value[k])} onChange={(v) => set(k, v === "" ? undefined : v)} className="w-full">
            <Label>{label}</Label><Input aria-label={k} placeholder={/path|file|svg|image|video|audio|reference|scene/i.test(k) ? "assets/… or work/…" : undefined} />
            {desc && <p className="text-xs text-muted">{desc}</p>}
          </TextField>
        );
      })}
    </div>
  );
}

function defaults(schema: any): Record<string, any> {
  const out: Record<string, any> = {};
  for (const [k, p] of Object.entries<any>(schema?.properties || {})) if (p.default !== undefined) out[k] = p.default;
  return out;
}

function TryIt({ row, detail, onRan, projectId }: { row: ToolRow; detail: any; onRan: () => void; projectId: string | null }) {
  const schema = detail.manifest?.parameters;
  const [vals, setVals] = useState<Record<string, any>>(() => defaults(schema));
  const [res, setRes] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    if (!projectId) return;
    setBusy(true);
    setRes(null);
    try {
      const params = Object.fromEntries(Object.entries(vals).filter(([, v]) => v !== undefined));
      setRes(await api(`/api/toolbox/tools/${row.scope}/${row.name}/try`, { method: "POST", json: { project_id: projectId, params } }));
      onRan();
    } catch (e: any) {
      setRes({ ok: false, error: e.message });
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex flex-col gap-3" data-testid="try-it">
      <p className="text-xs text-muted">Runs the tool by hand in the current project's workspace (paths are relative to it). The form is generated from the tool's JSON Schema.</p>
      <SchemaForm schema={schema} value={vals} onChange={setVals} />
      <div><Button onPress={run} isPending={busy} isDisabled={!projectId}><Play size={14} weight="fill" />Run</Button>
        {!projectId && <span className="ms-2 text-xs text-muted">Select a project first.</span>}</div>
      {res && (
        <div className="flex flex-col gap-2" data-testid="try-result">
          <div className="flex items-center gap-2">
            <Chip size="sm" variant="soft" color={res.ok ? "success" : "danger"}>{res.ok ? "success" : res.kind || "error"}</Chip>
            {res.duration != null && <span className="text-xs tabular-nums text-muted">{res.duration}s</span>}
          </div>
          {res.error && <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-xl bg-danger/5 p-2 font-mono text-xs text-danger-ink">{res.error}{res.traceback ? `\n\n${res.traceback}` : ""}</pre>}
          {res.ok && <Code_ code={JSON.stringify(res.result, null, 2)} language="json" maxH="320px" />}
          {res.images?.length > 0 && <div className="grid grid-cols-2 gap-2">{res.images.map((u: string) => <img key={u} src={u} alt="" className="rounded-xl border border-border" />)}</div>}
          {res.logs?.length > 0 && <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded-xl bg-night p-2 font-mono text-xs text-white/80">{res.logs.join("\n")}</pre>}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ authoring
const MANIFEST_TEMPLATE = `description: >-
  What the tool does, precisely, in 1–3 sentences (the director reads this).
parameters:
  type: object
  properties:
    image: {type: string, description: workspace path}
  required: [image]
returns:
  type: object
  properties:
    value: {type: number}
  required: [value]
dependencies: []
timeout_s: 120
network: false
produces_files: false
tags: [analysis]
`;
const MAIN_TEMPLATE = `def run(params, ctx):
    path = ctx.path(params["image"])  # workspace-relative → absolute (confined to the workspace)
    ctx.log(f"reading {path.name}")
    return {"value": path.stat().st_size}
`;
const TEST_TEMPLATE = `from main import run

from luma_engine.toolkit import make_test_ctx


def test_value(tmp_path):
    (tmp_path / "x.bin").write_bytes(b"12345")
    assert run({"image": "x.bin"}, make_test_ctx(tmp_path, workspace=tmp_path))["value"] == 5
`;

function NewToolModal({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: () => void }) {
  const { projectId, notify } = useStore();
  const [name, setName] = useState("");
  const [scope, setScope] = useState("project");
  const [manifest, setManifest] = useState(MANIFEST_TEMPLATE);
  const [main, setMain] = useState(MAIN_TEMPLATE);
  const [test, setTest] = useState(TEST_TEMPLATE);
  const [readme, setReadme] = useState("# my_tool\n\nWhat it does, why it exists, how it works.\n\nExample: …\n\nLimitations: …\n");
  const [res, setRes] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const create = async () => {
    setBusy(true);
    try {
      const r = await api<any>("/api/toolbox/tools", { method: "POST", json: { name, scope, project_id: projectId, manifest, main_py: main, test_py: test, readme } });
      setRes(r);
      onCreated();
      notify(r.test.status === "passed" ? `Created ${name} — tests pass; enable it to register` : `Created ${name} — tests ${r.test.status}`, r.test.status === "passed" ? "ok" : "bad");
    } catch (e: any) {
      notify(e.message, "bad");
      setRes({ error: e.message });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal.Backdrop isOpen={open} onOpenChange={(v) => { if (!v) { setRes(null); onClose(); } }}>
      <Modal.Container size="lg" scroll="inside">
        <Modal.Dialog className="sm:max-w-[900px]" aria-label="New tool">
          <Modal.CloseTrigger />
          <Modal.Header><Modal.Heading>New tool</Modal.Heading></Modal.Header>
          <Modal.Body className="flex flex-col gap-3">
            <p className="text-sm text-muted">Hand-written tools go through the same checks as the director's: manifest validation, ruff, the security scan and the tests. They are marked <span className="font-mono">author: user</span>.</p>
            <div className="flex flex-wrap gap-3">
              <TextField value={name} onChange={setName} className="min-w-[220px] flex-1"><Label>Name (snake_case)</Label><Input className="font-mono" /></TextField>
              <Select selectedKey={scope} onSelectionChange={(k) => setScope(String(k))} className="w-44">
                <Label>Scope</Label>
                <Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
                <Select.Popover><ListBox>
                  <ListBox.Item id="project" textValue="This project">This project<ListBox.ItemIndicator /></ListBox.Item>
                  <ListBox.Item id="global" textValue="Global">Global<ListBox.ItemIndicator /></ListBox.Item>
                </ListBox></Select.Popover>
              </Select>
            </div>
            {[["tool.yaml", manifest, setManifest, 12], ["main.py", main, setMain, 10], ["test_tool.py", test, setTest, 9], ["README.md", readme, setReadme, 6]].map(([l, v, fn, rows]: any) => (
              <TextField key={l} value={v} onChange={fn} className="w-full"><Label className="font-mono">{l}</Label><TextArea rows={rows} className="font-mono text-[12px]" spellCheck={false} /></TextField>
            ))}
            {res?.test && (
              <div className="flex flex-col gap-2">
                <div className="flex gap-2"><TestChip t={res.test} />{res.scan && !res.scan.ok && <Chip size="sm" variant="soft" color="danger">static checks failed</Chip>}</div>
                {res.scan?.blocking?.length > 0 && <ul className="text-xs text-danger-ink">{res.scan.blocking.map((f: any, i: number) => <li key={i} className="font-mono">{f.file}:{f.line} [{f.rule}] {f.message}</li>)}</ul>}
                {res.test.status !== "passed" && <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-xl bg-surface-secondary p-2 font-mono text-xs">{res.test.output}</pre>}
              </div>
            )}
          </Modal.Body>
          <Modal.Footer>
            <Button slot="close" variant="tertiary">Close</Button>
            <Button onPress={create} isPending={busy} isDisabled={!name.trim() || (scope === "project" && !projectId)}><Plus size={14} />Create & test</Button>
          </Modal.Footer>
        </Modal.Dialog>
      </Modal.Container>
    </Modal.Backdrop>
  );
}

// ------------------------------------------------------------------------------------------------ skills
function SkillsList({ version }: { version: number }) {
  const [rows, setRows] = useState<any[] | null>(null);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const load = useCallback(() => {
    api<any[]>(`/api/toolbox/skills${q.trim() ? `?q=${encodeURIComponent(q.trim())}` : ""}`).then(setRows).catch(() => setRows([]));
  }, [q]);
  useEffect(() => { const t = setTimeout(load, q ? 250 : 0); return () => clearTimeout(t); }, [load, version, q]);
  return (
    <div className="flex flex-col gap-3 p-4">
      <div className="flex gap-2">
        <Input aria-label="Search skills" placeholder="Search skills…" value={q} onChange={(e) => setQ(e.target.value)} className="h-8 flex-1 text-sm" />
        <Button size="sm" variant="secondary" onPress={() => setOpen("")}><Plus size={14} />New skill</Button>
      </div>
      {rows === null ? <Spinner size="sm" /> : (
        <ul className="flex flex-col gap-2">
          {rows.map((s) => (
            <li key={s.name}>
              <button onClick={() => setOpen(s.name)} data-testid="skill-row" className="flex w-full flex-col gap-1 rounded-2xl border border-border bg-surface px-3.5 py-3 text-left hover:border-accent/50">
                <div className="flex items-center gap-2"><BookOpen size={15} className="text-accent-ink" /><span className="font-mono text-[13px] font-medium">{s.name}</span>
                  <span className="ms-auto text-xs text-muted">{s.author}{s.reads ? ` · read ${s.reads}×` : ""}</span></div>
                <p className="text-[13px] text-muted">{s.description}</p>
                {(s.tags?.length > 0 || s.tools_used?.length > 0) && <div className="text-xs text-muted">{s.tags.map((t: string) => `#${t}`).join(" ")}{s.tools_used?.length ? ` · tools: ${s.tools_used.join(", ")}` : ""}</div>}
              </button>
            </li>
          ))}
          {!rows.length && <p className="py-6 text-center text-sm text-muted">No skills yet.</p>}
        </ul>
      )}
      <SkillModal name={open} onClose={() => setOpen(null)} onSaved={load} />
    </div>
  );
}

const SKILL_TEMPLATE = (n: string) => `---
name: ${n || "my_skill"}
description: When to use this playbook, in one sentence.
tags: []
tools_used: []
---

## When to use

## Steps
1.

## Pitfalls
-

## Verification
-

## Example
`;

function SkillModal({ name, onClose, onSaved }: { name: string | null; onClose: () => void; onSaved: () => void }) {
  const notify = useStore((s) => s.notify);
  const isNew = name === "";
  const [content, setContent] = useState("");
  const [newName, setNewName] = useState("");
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setEditing(isNew);
    setNewName("");
    if (name) api<any>(`/api/toolbox/skills/${name}`).then((s) => setContent(s.content)).catch(() => setContent(""));
    else if (isNew) setContent(SKILL_TEMPLATE(""));
  }, [name]); // eslint-disable-line react-hooks/exhaustive-deps
  const body = content.replace(/^---[\s\S]*?\n---\s*\n?/, "");
  const save = async () => {
    setBusy(true);
    try {
      const n = isNew ? newName.trim() : name!;
      await api(`/api/toolbox/skills/${n}`, { method: "PUT", json: { content } });
      notify("Skill saved", "ok");
      setEditing(false);
      onSaved();
      if (isNew) onClose();
    } catch (e: any) {
      notify(e.message, "bad");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal.Backdrop isOpen={name !== null} onOpenChange={(v) => !v && onClose()}>
      <Modal.Container size="lg" scroll="inside">
        <Modal.Dialog className="sm:max-w-[820px]" aria-label="Skill" data-testid="skill-modal">
          <Modal.CloseTrigger />
          <Modal.Header><Modal.Heading className="font-mono">{isNew ? "New skill" : name}</Modal.Heading></Modal.Header>
          <Modal.Body className="flex flex-col gap-3">
            {isNew && <TextField value={newName} onChange={(v) => { setNewName(v); setContent(content.replace(/^name: .*$/m, `name: ${v}`)); }}><Label>Name</Label><Input className="font-mono" /></TextField>}
            {editing ? <TextArea aria-label="SKILL.md" rows={22} value={content} onChange={(e) => setContent(e.target.value)} className="w-full font-mono text-[12px]" spellCheck={false} />
              : <div className="md" data-testid="skill-body"><ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown></div>}
          </Modal.Body>
          <Modal.Footer>
            {!editing ? <Button variant="secondary" onPress={() => setEditing(true)}><Code size={14} />Edit</Button>
              : <Button onPress={save} isPending={busy} isDisabled={isNew && !newName.trim()}>Save</Button>}
          </Modal.Footer>
        </Modal.Dialog>
      </Modal.Container>
    </Modal.Backdrop>
  );
}

// ------------------------------------------------------------------------------------------------ plugins
function PluginsList({ version }: { version: number }) {
  const notify = useStore((s) => s.notify);
  const [rows, setRows] = useState<any[] | null>(null);
  const [open, setOpen] = useState<any>(null);
  const load = useCallback(() => { api<any[]>("/api/toolbox/plugins").then(setRows).catch(() => setRows([])); }, []);
  useEffect(load, [load, version]);
  const toggle = async (p: any, on: boolean) => {
    try { await api(`/api/toolbox/plugins/${p.name}/${on ? "enable" : "disable"}`, { method: "POST" }); load(); } catch (e: any) { notify(e.message, "bad"); }
  };
  return (
    <div className="flex flex-col gap-2 p-4">
      <p className="text-xs text-muted">Engine extensions — effects, audio instruments, QC checks and templates. Scenes use them with <span className="font-mono">from luma_engine.plugins import name</span>.</p>
      {rows === null ? <Spinner size="sm" /> : rows.map((p) => (
        <div key={p.name} className="flex items-center gap-2 rounded-2xl border border-border bg-surface px-3.5 py-3" data-testid="plugin-row">
          <PuzzlePiece size={16} className="text-accent-ink" />
          <button className="min-w-0 flex-1 text-left" onClick={() => api(`/api/toolbox/plugins/${p.name}`).then(setOpen)}>
            <div className="flex items-center gap-2"><span className="font-mono text-[13px] font-medium">{p.name}</span><Chip size="sm" variant="soft">{p.kind}</Chip><span className="text-xs text-muted">v{p.version}</span>
              <Chip size="sm" variant="soft" color={p.test_status === "passed" ? "success" : "danger"}>tests {p.test_status}</Chip></div>
            <p className="line-clamp-2 text-[13px] text-muted">{p.description}</p>
          </button>
          <Switch isSelected={!!p.enabled} onChange={(v) => toggle(p, v)} aria-label={`Enable ${p.name}`}><Switch.Content><Switch.Control><Switch.Thumb /></Switch.Control></Switch.Content></Switch>
        </div>
      ))}
      {rows?.length === 0 && <p className="py-6 text-center text-sm text-muted">No plugins yet — the director creates them with plugin_create / template_save.</p>}
      <Modal.Backdrop isOpen={!!open} onOpenChange={(v) => !v && setOpen(null)}>
        <Modal.Container size="lg" scroll="inside"><Modal.Dialog className="sm:max-w-[860px]" aria-label="Plugin">
          <Modal.CloseTrigger />
          <Modal.Header><Modal.Heading className="font-mono">{open?.name}</Modal.Heading></Modal.Header>
          <Modal.Body className="flex flex-col gap-3">
            {open && Object.entries<string>(open.files || {}).map(([f, code]) => (
              <div key={f}><div className="mb-1 font-mono text-xs text-muted">{f}</div>
                {f.endsWith(".md") ? <div className="md rounded-xl border border-border p-3"><ReactMarkdown remarkPlugins={[remarkGfm]}>{code}</ReactMarkdown></div>
                  : <Code_ code={code} language={f.endsWith(".yaml") ? "yaml" : "python"} maxH="300px" />}</div>
            ))}
            {open?.test_output && <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-xl bg-night p-2 font-mono text-xs text-white/80">{open.test_output}</pre>}
          </Modal.Body>
        </Modal.Dialog></Modal.Container>
      </Modal.Backdrop>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ templates
export function TemplatesGallery() {
  const { refreshProjects, selectProject, notify, setTemplatesOpen } = useStore();
  const [rows, setRows] = useState<any[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => { api<any[]>("/api/toolbox/templates").then(setRows).catch(() => setRows([])); }, []);
  const use = async (t: any) => {
    setBusy(t.name);
    try {
      const r = await api<{ project: Project }>(`/api/toolbox/templates/${t.name}/use`, { method: "POST", json: {} });
      await refreshProjects();
      await selectProject(r.project.id);
      setTemplatesOpen(false);
      notify(`New project from “${t.title}” — its parameters are in work/scene.py`, "ok");
    } catch (e: any) {
      notify(e.message, "bad");
    } finally {
      setBusy(null);
    }
  };
  if (rows === null) return <div className="p-4"><Spinner size="sm" /></div>;
  const saved = rows.filter((t) => t.source === "plugin");
  const builtin = rows.filter((t) => t.source === "builtin");
  return (
    <div className="flex flex-col gap-4 p-4" data-testid="templates-gallery">
      <div>
        <div className="mb-2 text-xs font-medium uppercase tracking-wide text-muted">Saved from your projects</div>
        {saved.length === 0 ? <p className="text-sm text-muted">None yet — the director saves successful scenes with template_save.</p> : (
          <div className="grid gap-3 sm:grid-cols-2">
            {saved.map((t) => (
              <div key={t.name} className="flex flex-col overflow-hidden rounded-2xl border border-border bg-surface" data-testid="template-card">
                {t.preview ? (
                  <video src={t.preview} poster={t.thumbnail || undefined} muted loop playsInline preload="metadata" className="aspect-video w-full bg-night object-cover"
                    onMouseEnter={(e) => e.currentTarget.play().catch(() => {})} onMouseLeave={(e) => e.currentTarget.pause()} />
                ) : t.thumbnail ? <img src={t.thumbnail} alt="" className="aspect-video w-full bg-night object-cover" /> : <div className="aspect-video w-full bg-night" />}
                <div className="flex flex-1 flex-col gap-1 p-3">
                  <div className="flex items-center gap-2"><span className="text-sm font-medium">{t.title}</span><span className="text-xs text-muted">v{t.version}</span>
                    {!t.enabled && <Chip size="sm" variant="soft" color="danger">disabled</Chip>}</div>
                  <p className="line-clamp-2 text-xs text-muted">{t.description}</p>
                  {t.params && <p className="text-xs text-muted">params: {Object.keys(t.params.properties || {}).join(", ")}</p>}
                  <Button size="sm" className="mt-auto self-start" isDisabled={!t.enabled} isPending={busy === t.name} onPress={() => use(t)}>Use template</Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
      <div>
        <div className="mb-2 text-xs font-medium uppercase tracking-wide text-muted">Built in</div>
        <ul className="grid gap-2 sm:grid-cols-2">
          {builtin.map((t) => (
            <li key={t.name} className="rounded-2xl border border-border px-3 py-2.5"><div className="font-mono text-[13px]">{t.name}</div><p className="text-xs text-muted">{t.description}</p></li>
          ))}
        </ul>
        <p className="mt-2 text-xs text-muted">Ask the director to use a built-in template in a new project (“make a fan_unfold outro for our logo”).</p>
      </div>
    </div>
  );
}

export function TemplatesModal() {
  const { templatesOpen, setTemplatesOpen } = useStore();
  return (
    <Modal.Backdrop isOpen={templatesOpen} onOpenChange={setTemplatesOpen}>
      <Modal.Container size="lg" scroll="inside"><Modal.Dialog className="sm:max-w-[900px]" aria-label="Templates">
        <Modal.CloseTrigger />
        <Modal.Header><Modal.Heading>Templates</Modal.Heading></Modal.Header>
        <Modal.Body className="p-0">{templatesOpen && <TemplatesGallery />}</Modal.Body>
      </Modal.Dialog></Modal.Container>
    </Modal.Backdrop>
  );
}

/** Left-nav section: counts + shortcuts into the Toolbox. Collapsible (remembered); counts stay quiet. */
export function ToolboxNav() {
  const { openToolbox, setTemplatesOpen, projectId } = useStore();
  const [sum, setSum] = useState<any>(null);
  const [open, setOpen] = useState(() => { try { return localStorage.getItem("luma.toolboxNav") !== "closed"; } catch { return true; } });
  const toggle = () => { const v = !open; setOpen(v); try { localStorage.setItem("luma.toolboxNav", v ? "open" : "closed"); } catch { /* ignore */ } };
  useEffect(() => {
    const load = () => api(`/api/toolbox/summary${projectId ? `?project_id=${projectId}` : ""}`).then(setSum).catch(() => {});
    load();
    const iv = setInterval(load, 30000);
    return () => clearInterval(iv);
  }, [projectId]);
  const item = (label: string, n: number | undefined, Icon: any, onPress: () => void, testid: string) => (
    <button onClick={onPress} data-testid={testid} className="flex h-8 w-full items-center gap-2.5 rounded-lg px-2.5 text-left text-[13px] text-foreground/85 outline-none transition-colors hover:bg-surface-tertiary/70 focus-visible:ring-2 focus-visible:ring-focus">
      <Icon size={16} className="text-muted" /><span className="flex-1">{label}</span>{n ? <span className="text-xs tabular-nums text-muted">{n}</span> : null}
    </button>
  );
  return (
    <div className="px-3 pb-2" data-testid="toolbox-nav">
      <button onClick={toggle} aria-expanded={open}
        className="flex h-8 w-full items-center gap-1 rounded-lg px-1 text-xs font-medium uppercase tracking-wide text-muted outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-focus">
        Toolbox <CaretDown size={11} className={cn("transition-transform duration-150", !open && "-rotate-90")} />
      </button>
      {open && (
        <div className="animate-rise">
          {item("Tools", sum?.tools, ToolboxIcon, () => openToolbox("tools"), "nav-tools")}
          {item("Skills", sum?.skills, BookOpen, () => openToolbox("skills"), "nav-skills")}
          {item("Plugins", sum?.plugins, PuzzlePiece, () => openToolbox("plugins"), "nav-plugins")}
          {item("Templates", sum?.templates, FilmStrip, () => setTemplatesOpen(true), "nav-templates")}
        </div>
      )}
    </div>
  );
}
