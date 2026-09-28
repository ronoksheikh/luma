// Keys are saved (encrypted) to the signed-in account by default, so they follow the user
// to any browser.  "Browser only" mode keeps them in localStorage and sends them per request.
export type Keys = {
  llmApiKey: string;
  llmBaseUrl: string;
  llmModel: string;
  elevenlabsApiKey: string;
  preset: string;
};

export type User = { id: string; username: string; created_at: number };

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

const KEYS_STORAGE = "luma.keys.v1";

export const emptyKeys: Keys = { llmApiKey: "", llmBaseUrl: "", llmModel: "", elevenlabsApiKey: "", preset: "openrouter" };

export function loadKeys(): Keys {
  try {
    return { ...emptyKeys, ...JSON.parse(localStorage.getItem(KEYS_STORAGE) || "{}") };
  } catch {
    return { ...emptyKeys };
  }
}

export function saveKeys(k: Keys) {
  localStorage.setItem(KEYS_STORAGE, JSON.stringify(k));
}

export function keyHeaders(k: Keys = loadKeys()): Record<string, string> {
  const h: Record<string, string> = {};
  if (k.llmApiKey) h["X-LLM-Api-Key"] = k.llmApiKey;
  if (k.llmBaseUrl) h["X-LLM-Base-Url"] = k.llmBaseUrl;
  if (k.llmModel) h["X-LLM-Model"] = k.llmModel;
  if (k.elevenlabsApiKey) h["X-ElevenLabs-Api-Key"] = k.elevenlabsApiKey;
  return h;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T = any>(path: string, init: RequestInit & { json?: unknown; keys?: Keys } = {}): Promise<T> {
  const headers: Record<string, string> = { "X-Luma-Client": "1", ...keyHeaders(init.keys), ...(init.headers as Record<string, string>) };
  let body = init.body;
  if (init.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(init.json);
  }
  const res = await fetch(path, { ...init, headers, body, credentials: "same-origin" });
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (res.status === 401 && !path.startsWith("/api/auth/")) onUnauthorized?.();
  if (!res.ok) {
    const detail = data?.detail;
    const msg = typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((d: any) => d.msg || JSON.stringify(d)).join("; ") : res.statusText;
    throw new ApiError(res.status, msg || `HTTP ${res.status}`);
  }
  return data as T;
}

// ---------------------------------------------------------------- types
export type ProjectSettings = {
  width: number;
  height: number;
  fps: number;
  duration: number;
  formats: string[];
  avoid_colors: string[];
  voice_language: string;
  voice_tone: string;
  captions: boolean;
  autopilot: boolean;
  approval_render_minutes: number;
  approval_el_chars: number;
  approval_cost_usd: number;
  approval_run_minutes: number;
  web_access: boolean;
  subagents: boolean;
  subagent_max_concurrency: number;
  subagent_max_cost_usd: number;
};

export type Project = {
  id: string;
  name: string;
  brief: string;
  kind: string;
  settings: ProjectSettings;
  asset_count: number;
  max_assets: number;
  latest_run: { id: string; status: string; kind: string } | null;
  updated_at: number;
};

export type Asset = {
  id: string;
  filename: string;
  kind: string;
  size: number;
  path: string;
  url: string;
  thumb_url: string | null;
  analysis: any;
};

export type Run = {
  id: string;
  project_id: string;
  kind: string;
  status: string;
  model: string;
  steps: number;
  prompt_tokens: number;
  completion_tokens: number;
  el_chars: number;
  error: string | null;
  summary: string | null;
  active: boolean;
};

export type FileItem = { path: string; url: string; size: number; mtime: number; ext: string };

export function fmtBytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
