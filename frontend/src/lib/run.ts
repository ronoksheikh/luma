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

export type Item =
  | { kind: "user"; id: string; text: string }
  | { kind: "assistant"; id: string; turn: number; text: string; reasoning: string; final?: boolean }
  | ToolItem
  | { kind: "notice"; id: string; tone: "error" | "warn" | "info"; text: string; code?: string };

export type Usage = { steps?: number; prompt_tokens?: number; completion_tokens?: number; el_chars?: number; max_steps?: number; el_budget?: number };

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
};

const EMPTY: RunState = { items: [], status: "idle", usage: {}, images: [], audios: [], artifacts: [], connected: false, lastId: 0 };

const TYPES = ["text_delta", "tool_call_start", "tool_args_delta", "tool_output_delta", "tool_result", "image", "audio", "progress", "artifact",
  "usage", "error", "run_status", "user_message", "job", "retry", "context"];

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
      next.question = d.status === "waiting_input" ? { text: d.question, options: d.options || [] } : undefined;
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
