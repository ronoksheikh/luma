import { AlertTriangle, Check, ChevronDown, Eye, EyeOff, KeyRound, Loader2, Minus, Search, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Badge, Button, Input, Label, Switch, cn } from "../components/ui";
import { api, type Keys } from "../lib/api";
import { useStore } from "../lib/store";

type Check = { name: string; status: "ok" | "fail" | "warn" | "skip"; detail: string; ms?: number };

const PRESETS: { id: string; label: string; url: string }[] = [
  { id: "openai", label: "OpenAI", url: "https://api.openai.com/v1" },
  { id: "openrouter", label: "OpenRouter", url: "https://openrouter.ai/api/v1" },
  { id: "custom", label: "Custom", url: "" },
];

const CHECK_LABEL: Record<string, string> = {
  auth: "Authentication",
  streaming: "Streaming",
  tool_calling: "Tool calling (round trip)",
  vision: "Vision (optional)",
  tts: "Text to speech",
  voice_design: "Voice design",
  sfx: "Sound effects",
  music: "Music",
  speech_to_text: "Speech to text",
};

export function StatusIcon({ status }: { status: string }) {
  if (status === "ok") return <Check className="h-4 w-4 text-ok" />;
  if (status === "fail") return <X className="h-4 w-4 text-bad" />;
  if (status === "warn") return <AlertTriangle className="h-4 w-4 text-warn" />;
  if (status === "running") return <Loader2 className="h-4 w-4 animate-spin text-muted" />;
  return <Minus className="h-4 w-4 text-faint" />;
}

function SecretInput({ value, onChange, placeholder, id }: { value: string; onChange: (v: string) => void; placeholder?: string; id?: string }) {
  const [show, setShow] = useState(false);
  return (
    <div className="relative">
      <KeyRound className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-faint" />
      <Input id={id} type={show ? "text" : "password"} autoComplete="off" spellCheck={false} value={value} onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder} className="pl-8 pr-9 font-mono text-xs" />
      <button type="button" onClick={() => setShow(!show)} className="absolute right-2 top-2 text-faint hover:text-fg" aria-label={show ? "Hide key" : "Show key"}>
        {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
      </button>
    </div>
  );
}

function ModelPicker({ value, onChange, models, loading, onLoad }: {
  value: string; onChange: (v: string) => void; models: { id: string; name?: string; tools?: boolean; vision?: boolean; context_length?: number }[];
  loading: boolean; onLoad: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const h = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);
  const list = useMemo(() => {
    const s = (q || "").toLowerCase();
    return models.filter((m) => !s || m.id.toLowerCase().includes(s) || (m.name || "").toLowerCase().includes(s)).slice(0, 300);
  }, [models, q]);
  return (
    <div className="relative" ref={box}>
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Input id="llm-model" value={value} onChange={(e) => { onChange(e.target.value); setQ(e.target.value); }} onFocus={() => models.length && setOpen(true)}
            placeholder="any model ID — free text is fine" className="pr-8 font-mono text-xs" />
          {models.length > 0 && (
            <button type="button" className="absolute right-2 top-2.5 text-faint hover:text-fg" onClick={() => setOpen(!open)} aria-label="Show models">
              <ChevronDown className="h-4 w-4" />
            </button>
          )}
        </div>
        <Button type="button" onClick={onLoad} loading={loading} variant="outline">Load models</Button>
      </div>
      {open && models.length > 0 && (
        <div className="absolute z-30 mt-1 max-h-72 w-full overflow-hidden rounded-lg border border-line-strong bg-panel-2 shadow-2xl">
          <div className="flex items-center gap-2 border-b border-line px-2.5 py-2">
            <Search className="h-3.5 w-3.5 text-faint" />
            <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder={`Search ${models.length} models`}
              className="w-full bg-transparent text-xs outline-none placeholder:text-faint" />
          </div>
          <div className="max-h-60 overflow-y-auto py-1">
            {list.map((m) => (
              <button key={m.id} type="button" onClick={() => { onChange(m.id); setOpen(false); }}
                className={cn("flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left text-xs hover:bg-raised", m.id === value && "bg-raised")}>
                <span className="truncate font-mono">{m.id}</span>
                <span className="flex shrink-0 gap-1">
                  {m.tools && <Badge tone="ok">tools</Badge>}
                  {m.vision && <Badge tone="info">vision</Badge>}
                  {m.context_length ? <Badge>{Math.round(m.context_length / 1000)}k</Badge> : null}
                </span>
              </button>
            ))}
            {!list.length && <div className="px-3 py-2 text-xs text-faint">No match — the typed ID will be used as-is.</div>}
          </div>
        </div>
      )}
    </div>
  );
}

export function LlmConnection({ onValid }: { onValid?: () => void }) {
  const { keys, setKeys, server, loadServer, notify } = useStore();
  const [draft, setDraft] = useState<Keys>(keys);
  const [models, setModels] = useState<any[]>([]);
  const [loadingModels, setLoadingModels] = useState(false);
  const [testing, setTesting] = useState(false);
  const [checks, setChecks] = useState<Check[] | null>(null);
  const [remember, setRemember] = useState(!!server?.remembered?.llm_api_key);
  const [error, setError] = useState("");

  useEffect(() => {
    // env-var prefill (keys themselves stay on the server)
    if (server && !keys.llmBaseUrl && server.env_prefill.llm_base_url) {
      setDraft((d) => ({ ...d, llmBaseUrl: server.env_prefill.llm_base_url, llmModel: d.llmModel || server.env_prefill.llm_model, preset: "custom" }));
    } else if (!keys.llmBaseUrl && !draft.llmBaseUrl) {
      setDraft((d) => ({ ...d, llmBaseUrl: PRESETS[1].url, preset: "openrouter" }));
    }
  }, [server]); // eslint-disable-line react-hooks/exhaustive-deps

  const body = { api_key: draft.llmApiKey || undefined, base_url: draft.llmBaseUrl || undefined, model: draft.llmModel || undefined };

  const loadModels = async () => {
    setError("");
    setLoadingModels(true);
    try {
      const r = await api<{ models: any[] }>("/api/models", { method: "POST", json: body, keys: { ...draft, elevenlabsApiKey: "" } });
      setModels(r.models);
      notify(`Loaded ${r.models.length} models`, "ok");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoadingModels(false);
    }
  };

  const test = async () => {
    setError("");
    setTesting(true);
    setChecks(["auth", "streaming", "tool_calling", "vision"].map((n) => ({ name: n, status: "running" as any, detail: "" })));
    try {
      const r = await api<{ ok: boolean; checks: Check[] }>("/api/test-connection", { method: "POST", json: body, keys: { ...draft, elevenlabsApiKey: "" } });
      setChecks(r.checks);
      if (r.ok) {
        setKeys({ llmApiKey: draft.llmApiKey, llmBaseUrl: draft.llmBaseUrl, llmModel: draft.llmModel, preset: draft.preset });
        if (remember) {
          await api("/api/settings/remember", { method: "POST", json: { llm_api_key: draft.llmApiKey, llm_base_url: draft.llmBaseUrl, llm_model: draft.llmModel } });
        }
        await api("/api/settings", { method: "PUT", json: { llm_base_url: draft.llmBaseUrl, llm_model: draft.llmModel, llm_preset: draft.preset } });
        await loadServer();
        notify("LLM connection verified and saved", "ok");
        onValid?.();
      }
    } catch (e: any) {
      setChecks(null);
      setError(e.message);
    } finally {
      setTesting(false);
    }
  };

  const envKey = server?.env_prefill.llm_api_key;
  return (
    <div className="space-y-4">
      <div>
        <Label>Provider preset</Label>
        <div className="grid grid-cols-3 gap-2">
          {PRESETS.map((p) => (
            <button key={p.id} type="button" onClick={() => setDraft({ ...draft, preset: p.id, llmBaseUrl: p.url || (draft.preset === "custom" ? draft.llmBaseUrl : "") })}
              className={cn("rounded-lg border px-3 py-2 text-sm transition-colors", draft.preset === p.id ? "border-accent/60 bg-accent/10 text-fg" : "border-line-strong text-muted hover:text-fg")}>
              {p.label}
            </button>
          ))}
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <Label htmlFor="llm-key">API key {envKey && <span className="text-faint">(server has LLM_API_KEY — leave empty to use it)</span>}</Label>
          <SecretInput id="llm-key" value={draft.llmApiKey} onChange={(v) => setDraft({ ...draft, llmApiKey: v })}
            placeholder={draft.preset === "openrouter" ? "sk-or-v1-…" : draft.preset === "openai" ? "sk-…" : "optional for local servers"} />
        </div>
        <div className="sm:col-span-2">
          <Label htmlFor="llm-url">Base URL</Label>
          <Input id="llm-url" value={draft.llmBaseUrl} onChange={(e) => setDraft({ ...draft, llmBaseUrl: e.target.value, preset: "custom" })}
            placeholder="http://host.docker.internal:11434/v1" className="font-mono text-xs" />
        </div>
        <div className="sm:col-span-2">
          <Label htmlFor="llm-model">Model ID</Label>
          <ModelPicker value={draft.llmModel} onChange={(v) => setDraft({ ...draft, llmModel: v })} models={models} loading={loadingModels} onLoad={loadModels} />
        </div>
      </div>
      <div className="flex items-center justify-between gap-3 rounded-lg border border-line bg-bg/60 px-3 py-2.5">
        <div>
          <div className="text-sm">Remember on this server</div>
          <div className="text-xs text-faint">Default: keys stay in this browser and are sent per request. Remembered keys are encrypted at rest in /data.</div>
        </div>
        <Switch checked={remember} onCheckedChange={setRemember} label="Remember on this server" />
      </div>
      {error && <div className="rounded-lg border border-bad/30 bg-bad/10 px-3 py-2 text-xs text-bad">{error}</div>}
      {checks && (
        <div className="divide-y divide-line rounded-lg border border-line">
          {checks.map((c) => (
            <div key={c.name} className="flex items-start gap-2.5 px-3 py-2">
              <div className="mt-0.5"><StatusIcon status={c.status} /></div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between text-sm"><span>{CHECK_LABEL[c.name] || c.name}</span>{c.ms ? <span className="text-[11px] text-faint">{c.ms} ms</span> : null}</div>
                {c.detail && <div className="break-words text-xs text-muted">{c.detail}</div>}
              </div>
            </div>
          ))}
        </div>
      )}
      <div className="flex justify-end">
        <Button variant="primary" onClick={test} loading={testing} disabled={!draft.llmBaseUrl || !draft.llmModel}>Test connection</Button>
      </div>
    </div>
  );
}

export function ElevenLabsConnection() {
  const { keys, setKeys, server, loadServer, notify } = useStore();
  const [key, setKey] = useState(keys.elevenlabsApiKey);
  const [remember, setRemember] = useState(!!server?.remembered?.elevenlabs_api_key);
  const [res, setRes] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const test = async () => {
    setBusy(true);
    setError("");
    setRes(null);
    try {
      const r = await api<any>("/api/elevenlabs/test", { method: "POST", json: { api_key: key || undefined }, keys: { ...keys, elevenlabsApiKey: key } });
      setRes(r);
      if (r.ok) {
        setKeys({ elevenlabsApiKey: key });
        if (remember) await api("/api/settings/remember", { method: "POST", json: { elevenlabs_api_key: key } });
        await loadServer();
        notify("ElevenLabs connected", "ok");
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const clear = async () => {
    setKey("");
    setKeys({ elevenlabsApiKey: "" });
    await api("/api/settings/remember", { method: "POST", json: { elevenlabs_api_key: "" } }).catch(() => {});
    await loadServer();
    setRes(null);
  };
  const acct = res?.account;
  return (
    <div className="space-y-3">
      <div>
        <Label htmlFor="el-key">ElevenLabs API key {server?.env_prefill.elevenlabs_api_key && <span className="text-faint">(server has ELEVENLABS_API_KEY)</span>}</Label>
        <SecretInput id="el-key" value={key} onChange={setKey} placeholder="sk_…" />
        <p className="mt-1.5 text-xs text-faint">Optional. Without it, the director uses the built-in synthesiser for all sound. Keys never enter the agent's terminal — ElevenLabs is reached only through backend tools, with a hard per-run character budget.</p>
      </div>
      <div className="flex items-center justify-between gap-3 rounded-lg border border-line bg-bg/60 px-3 py-2.5">
        <div className="text-sm">Remember on this server</div>
        <Switch checked={remember} onCheckedChange={setRemember} label="Remember ElevenLabs key on this server" />
      </div>
      {error && <div className="rounded-lg border border-bad/30 bg-bad/10 px-3 py-2 text-xs text-bad">{error}</div>}
      {res && (
        <div className="rounded-lg border border-line">
          {acct && (
            <div className="border-b border-line px-3 py-2.5">
              <div className="flex items-center justify-between text-sm">
                <span>{acct.first_name || "Account"} · <span className="capitalize">{acct.tier}</span></span>
                <span className="text-xs text-muted">{acct.character_count?.toLocaleString()} / {acct.character_limit?.toLocaleString()} chars used</span>
              </div>
              {acct.character_limit ? (
                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-line"><div className="h-full bg-accent" style={{ width: `${Math.min(100, (100 * acct.character_count) / acct.character_limit)}%` }} /></div>
              ) : null}
            </div>
          )}
          <div className="grid grid-cols-1 divide-y divide-line">
            {res.checks.map((c: Check) => (
              <div key={c.name} className="flex items-start gap-2.5 px-3 py-1.5">
                <div className="mt-0.5"><StatusIcon status={c.status} /></div>
                <div className="min-w-0 flex-1 text-sm">{CHECK_LABEL[c.name] || c.name}<div className="break-words text-xs text-muted">{c.detail}</div></div>
              </div>
            ))}
          </div>
        </div>
      )}
      <div className="flex justify-end gap-2">
        {(keys.elevenlabsApiKey || server?.elevenlabs.configured) && <Button variant="ghost" onClick={clear}>Remove</Button>}
        <Button onClick={test} loading={busy} disabled={!key && !server?.env_prefill.elevenlabs_api_key}>Test ElevenLabs</Button>
      </div>
    </div>
  );
}
