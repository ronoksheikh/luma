// Run event stream (SSE) → timeline state. Reconnects with Last-Event-ID and replays
// history, so reloading mid-run shows everything and continues live.
import { useEffect, useRef, useState } from "react";

export type Media = { path: string; url: string; label?: string; kind?: string; text?: string; chars?: number; cached?: boolean; duration?: number; lufs?: number; true_peak_dbtp?: number; tool_call_id?: string; ts?: number };
export type Progress = { frame?: number; total?: number; eta_s?: number | null; stage?: string; message?: string; fps?: number };

export type ToolItem = {
  kind: "tool";
  id: string;
  name: string;
  args: string;
  status: "running" | "success" | "error" | "cancelled";
  output: string;
  result?: string;
  seconds?: number;
  ui?: any;
  images: Media[];
  audios: Media[];
  progress?: Progress;
  job?: { name: string; status: string };
  startedAt: number;
};

export type Artifact = {
  id: string; project_id: string; run_id: string | null; type: string; path: string | null; url: string | null; title: string;
  version_group: string; version: number; meta: any; favorite: boolean; created_at: number;
};

export type Todo = {
  id: string; parent_id: string | null; title: string; detail: string; status: string; priority: string; order: number;
  acceptance_criteria: string; evidence: any[]; note: string | null; started_at: number | null; completed_at: number | null; run_id: string;
};

export type Req = {
  kind: "request"; id: string; reqKind: "ask" | "approval" | "options"; data: any; status: "pending" | "answered" | "timeout" | "cancelled";
  answer?: any; auto?: boolean;
};

export type Item =
  | { kind: "user"; id: string; text: string }
  | { kind: "assistant"; id: string; turn: number; text: string; reasoning: string; final?: boolean }
  | ToolItem
  | { kind: "notice"; id: string; tone: "error" | "warn" | "info"; text: string; code?: string }
  | { kind: "card"; id: string; card: string; artifact: Artifact }
  | Req
  | { kind: "subagent"; id: string; child: string; label: string; task: string; tools: string[]; status: string; result?: any }
  | { kind: "toolbox"; id: string; event: string; data: any };

export type Usage = { steps?: number; prompt_tokens?: number; completion_tokens?: number; el_chars?: number; max_steps?: number; el_budget?: number; cost_usd?: number };
export type Budget = { steps: number; max_steps: number; tokens: number; max_tokens: number | null; cost_usd: number; max_cost_usd: number | null;
  pricing_known: boolean; el_chars: number; el_budget: number; elapsed_s: number; wall_clock_s: number };
export type Stage = { stage: string; percent: number; eta_s?: number | null; detail?: string; ts: number };
export type Notice = { id: number; message: string; level: string; ts: number };

export type RunState = {
  items: Item[];
  status: string;
  question?: { text: string; options: string[] };
  usage: Usage;
  images: Media[];
  audios: Media[];
  artifacts: Media[];
  connected: boolean;
  lastId: number;
  summary?: string;
  todos: Todo[];
  plan: { done: number; total: number; current: { id: string; title: string } | null };
  stage?: Stage;
  budget?: Budget;
  notifications: Notice[];
  versions: { artifacts: number; memory: number; checkpoints: number; toolbox: number };
  pendingRequest?: Req;
};

const EMPTY: RunState = { items: [], status: "idle", usage: {}, images: [], audios: [], artifacts: [], connected: false, lastId: 0,
  todos: [], plan: { done: 0, total: 0, current: null }, notifications: [], versions: { artifacts: 0, memory: 0, checkpoints: 0, toolbox: 0 } };

export const TOOLBOX_EVENTS = ["tool_created", "tool_tested", "tool_registered", "tool_updated", "tool_promoted", "tool_disabled", "skill_written",
  "plugin_created", "template_saved"];

const TYPES = ["text_delta", "tool_call_start", "tool_args_delta", "tool_output_delta", "tool_result", "image", "audio", "progress", "artifact",
  "usage", "error", "run_status", "user_message", "job", "retry", "context", "todo_update", "plan_revision", "memory_update", "checkpoint", "present",
  "ask_user", "approval_request", "approval_result", "options_request", "notify", "progress_stage", "budget", "subagent_start", "subagent_end",
  "compaction", "system_note", ...TOOLBOX_EVENTS];

const REQ_KIND: Record<string, Req["reqKind"]> = { ask_user: "ask", approval_request: "approval", options_request: "options" };

type Ev = { id: number; type: string; data: any; ts: number };

function findTool(s: RunState, id?: string): ToolItem | undefined {
  if (!id) return undefined;
  for (let i = s.items.length - 1; i >= 0; i--) {
    const it = s.items[i];
    if (it.kind === "tool" && it.id === id) return it;
  }
  return undefined;
}

export function reduce(s: RunState, e: Ev): RunState {
  const d = e.data || {};
  const items = s.items;
  const next: RunState = { ...s, lastId: Math.max(s.lastId, e.id) };
  const clone = (it: ToolItem) => {
    const c = { ...it };
    next.items = items.map((x) => (x === it ? c : x));
    return c;
  };
  if (TOOLBOX_EVENTS.includes(e.type)) {
    next.items = [...items, { kind: "toolbox", id: `tb${e.id}`, event: e.type, data: d }];
    next.versions = { ...s.versions, toolbox: s.versions.toolbox + 1 };
    return next;
  }
  switch (e.type) {
    case "user_message":
      next.items = [...items, { kind: "user", id: `u${e.id}`, text: d.text }];
      break;
    case "text_delta": {
      const last = items[items.length - 1];
      const turn = d.turn ?? -1;
      if (last && last.kind === "assistant" && last.turn === turn && !d.final) {
        const upd = { ...last, text: d.kind === "reasoning" ? last.text : last.text + d.delta, reasoning: d.kind === "reasoning" ? last.reasoning + d.delta : last.reasoning };
        next.items = [...items.slice(0, -1), upd];
      } else {
        next.items = [...items, { kind: "assistant", id: `a${e.id}`, turn, text: d.kind === "reasoning" ? "" : d.delta, reasoning: d.kind === "reasoning" ? d.delta : "", final: !!d.final }];
      }
      break;
    }
    case "tool_call_start":
      next.items = [...items, { kind: "tool", id: d.id, name: d.name, args: "", status: "running", output: "", images: [], audios: [], startedAt: e.ts }];
      break;
    case "tool_args_delta": {
      const t = findTool(s, d.id);
      if (t) clone(t).args = t.args + d.delta;
      break;
    }
    case "tool_output_delta": {
      const t = findTool(s, d.id || d.tool_call_id);
      if (t) {
        const out = t.output + d.delta;
        clone(t).output = out.length > 60000 ? out.slice(-60000) : out;
      }
      break;
    }
    case "tool_result": {
      const t = findTool(s, d.id);
      if (t) {
        const c = clone(t);
        c.status = d.status;
        c.result = d.output;
        c.seconds = d.seconds;
        c.ui = d.ui;
        c.progress = undefined;
      }
      break;
    }
    case "image": {
      const m: Media = { ...d, ts: e.ts };
      next.images = [...s.images, m];
      const t = findTool(s, d.tool_call_id);
      if (t) clone(t).images = [...t.images, m];
      break;
    }
    case "audio": {
      const m: Media = { ...d, ts: e.ts };
      next.audios = [...s.audios, m];
      const t = findTool(s, d.tool_call_id);
      if (t) clone(t).audios = [...t.audios, m];
      break;
    }
    case "progress": {
      const t = findTool(s, d.tool_call_id);
      if (t) clone(t).progress = d;
      break;
    }
    case "job": {
      const t = findTool(s, d.tool_call_id);
      if (t) clone(t).job = { name: d.job, status: d.status };
      break;
    }
    case "artifact":
      next.artifacts = [...s.artifacts, { ...d, ts: e.ts }];
      if (d.artifact) next.versions = { ...s.versions, artifacts: s.versions.artifacts + 1 };
      break;
    case "todo_update":
      next.todos = d.todos || [];
      next.plan = d.progress || next.plan;
      break;
    case "plan_revision":
      next.items = [...items, { kind: "notice", id: `pr${e.id}`, tone: "info", text: `Plan revised (revision ${d.revision}, by ${d.author}): ${d.reason}` }];
      break;
    case "memory_update":
      next.versions = { ...s.versions, memory: s.versions.memory + 1 };
      break;
    case "checkpoint":
      next.versions = { ...s.versions, checkpoints: s.versions.checkpoints + 1 };
      if (d.action === "restore") next.items = [...items, { kind: "notice", id: `cp${e.id}`, tone: "info", text: `Restored checkpoint “${d.checkpoint?.label}”.` }];
      break;
    case "present":
      next.items = [...items, { kind: "card", id: `p${e.id}`, card: d.card, artifact: d.artifact }];
      next.versions = { ...s.versions, artifacts: s.versions.artifacts + 1 };
      break;
    case "ask_user":
    case "approval_request":
    case "options_request": {
      const r: Req = { kind: "request", id: d.request_id, reqKind: REQ_KIND[e.type], data: d, status: d.auto ? "answered" : "pending", auto: !!d.auto };
      next.items = [...items, r];
      if (!d.auto) next.pendingRequest = r;
      break;
    }
    case "approval_result": {
      next.items = items.map((it) => (it.kind === "request" && it.id === d.request_id ? { ...it, status: d.status, answer: d.answer } : it));
      if (s.pendingRequest?.id === d.request_id) next.pendingRequest = undefined;
      break;
    }
    case "notify":
      next.notifications = [...s.notifications, { id: d.id, message: d.message, level: d.level, ts: e.ts }];
      break;
    case "progress_stage":
      next.stage = { ...d, ts: e.ts };
      break;
    case "budget":
      next.budget = d;
      break;
    case "subagent_start":
      next.items = [...items, { kind: "subagent", id: `sa${d.child_run_id}`, child: d.child_run_id, label: d.label, task: d.task, tools: d.tools || [], status: "running" }];
      break;
    case "subagent_end":
      next.items = items.map((it) => (it.kind === "subagent" && it.child === d.child_run_id ? { ...it, status: d.status, result: d } : it));
      break;
    case "compaction":
      next.items = [...items, { kind: "notice", id: `c${e.id}`, tone: "info", text: `Context compacted: ${d.summarized_messages} older messages summarised (${d.reason}). The plan, memory and your requests are kept.` }];
      break;
    case "system_note":
      next.items = [...items, { kind: "notice", id: `sn${e.id}`, tone: "info", text: d.text }];
      break;
    case "usage":
      next.usage = { ...s.usage, ...d };
      break;
    case "error":
      next.items = [...items, { kind: "notice", id: `e${e.id}`, tone: "error", text: d.message || "error", code: d.code }];
      break;
    case "retry":
      next.items = [
        ...(d.discard_turn != null ? items.filter((it) => !(it.kind === "assistant" && it.turn === d.discard_turn)) : items),
        { kind: "notice", id: `r${e.id}`, tone: "warn", text: `Provider error — retrying in ${d.delay_s}s (attempt ${d.attempt}): ${d.error}` },
      ];
      break;
    case "context":
      next.items = [...items, { kind: "notice", id: `c${e.id}`, tone: "info", text: `Context compacted: ${d.summarized_messages} older messages summarised.` }];
      break;
    case "run_status":
      next.status = d.status;
      next.question = d.status === "waiting_input" && !d.request_id ? { text: d.question, options: d.options || [] } : undefined;
      if (d.status !== "waiting_input" && s.pendingRequest && !["running"].includes(d.status)) {
        next.pendingRequest = undefined;
        next.items = next.items.map((it) => (it.kind === "request" && it.status === "pending" ? { ...it, status: "cancelled" } : it));
      }
      if (d.summary) next.summary = d.summary;
      if (d.status === "cancelled" || d.status === "failed") {
        next.items = next.items.map((it) => (it.kind === "tool" && it.status === "running" ? { ...it, status: "cancelled" } : it));
      }
      break;
  }
  return next;
}

export function useRunEvents(runId: string | null) {
  const [state, setState] = useState<RunState>(EMPTY);
  const ref = useRef<EventSource | null>(null);
  useEffect(() => {
    setState(EMPTY);
    if (!runId) return;
    // batch events per animation frame for smooth streaming
    let queue: Ev[] = [];
    let raf = 0;
    const flush = () => {
      raf = 0;
      const q = queue;
      queue = [];
      setState((s) => q.reduce(reduce, s));
    };
    const es = new EventSource(`/api/runs/${runId}/events`);
    ref.current = es;
    const onEvent = (msg: MessageEvent) => {
      try {
        queue.push(JSON.parse(msg.data));
        if (!raf) raf = requestAnimationFrame(flush);
      } catch {
        /* ignore */
      }
    };
    TYPES.forEach((t) => es.addEventListener(t, onEvent as EventListener));
    es.onopen = () => setState((s) => ({ ...s, connected: true }));
    es.onerror = () => setState((s) => ({ ...s, connected: false }));
    return () => {
      es.close();
      if (raf) cancelAnimationFrame(raf);
    };
  }, [runId]);
  return state;
}

export const ACTIVE = new Set(["running", "waiting_input"]);
