import { Button, Chip, ComboBox, Description, Input, Label, ListBox, Meter, Spinner, ToggleButton, ToggleButtonGroup } from "@heroui/react";
import { CheckCircle, CircleDashed, MinusCircle, Warning, XCircle } from "@phosphor-icons/react";
import { useEffect, useState, type Key } from "react";
import { api, type Keys } from "../lib/api";
import { useStore } from "../lib/store";
import { Field, SecretField, Toggle } from "../ui/kit";

type Check = { name: string; status: "ok" | "fail" | "warn" | "skip" | "running"; detail: string; ms?: number };
type ModelInfo = { id: string; name?: string; tools?: boolean; vision?: boolean; context_length?: number };

const PRESETS = [
  { id: "openai", label: "OpenAI", url: "https://api.openai.com/v1" },
  { id: "openrouter", label: "OpenRouter", url: "https://openrouter.ai/api/v1" },
  { id: "custom", label: "Custom", url: "" },
];

const LABEL: Record<string, string> = {
  auth: "Authentication", streaming: "Streaming", tool_calling: "Tool calling", vision: "Vision · optional",
  tts: "Text to speech", voice_design: "Voice design", sfx: "Sound effects", music: "Music", speech_to_text: "Speech to text",
};

export function StatusIcon({ status }: { status: string }) {
  if (status === "ok") return <CheckCircle size={18} weight="fill" className="text-success-ink" />;
  if (status === "fail") return <XCircle size={18} weight="fill" className="text-danger-ink" />;
  if (status === "warn") return <Warning size={18} weight="fill" className="text-warning-ink" />;
  if (status === "running") return <Spinner size="sm" />;
  if (status === "skip") return <MinusCircle size={18} className="text-muted" />;
  return <CircleDashed size={18} className="text-muted" />;
}

function Checks({ checks }: { checks: Check[] }) {
  return (
    <ul className="divide-y divide-separator overflow-hidden rounded-xl border border-border">
      {checks.map((c) => (
        <li key={c.name} className="flex items-start gap-3 bg-surface px-3.5 py-2.5">
          <span className="mt-px"><StatusIcon status={c.status} /></span>
          <div className="min-w-0 flex-1">
            <div className="flex items-baseline justify-between gap-2 text-sm">
              <span>{LABEL[c.name] || c.name}</span>
              {c.ms ? <span className="text-xs tabular-nums text-muted">{c.ms} ms</span> : null}
            </div>
            {c.detail && <p className="mt-0.5 break-words text-xs text-muted">{c.detail}</p>}
          </div>
        </li>
      ))}
    </ul>
  );
}

export function LlmConnection({ onValid }: { onValid?: () => void }) {
  const { keys, setKeys, server, loadServer, notify } = useStore();
  const [draft, setDraft] = useState<Keys>(keys);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [testing, setTesting] = useState(false);
  const [checks, setChecks] = useState<Check[] | null>(null);
  const [saveToAccount, setSaveToAccount] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!server) return;
    setDraft((d) => ({
      ...d,
      llmBaseUrl: d.llmBaseUrl || server.llm.base_url || server.env_prefill.llm_base_url || PRESETS[1].url,
      llmModel: d.llmModel || server.llm.model || server.env_prefill.llm_model,
      preset: d.llmBaseUrl || server.llm.base_url ? (PRESETS.find((p) => p.url === (d.llmBaseUrl || server.llm.base_url))?.id ?? "custom") : "openrouter",
    }));
  }, [server]);

  const body = { api_key: draft.llmApiKey || undefined, base_url: draft.llmBaseUrl || undefined, model: draft.llmModel || undefined };
  const reqKeys = { ...draft, elevenlabsApiKey: "" };
  const savedKey = server?.llm.key_source === "server" && !draft.llmApiKey;

  const loadModels = async () => {
    setError("");
    setLoading(true);
    try {
      const r = await api<{ models: ModelInfo[] }>("/api/models", { method: "POST", json: body, keys: reqKeys });
      setModels(r.models);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const test = async () => {
    setError("");
    setTesting(true);
    setChecks(["auth", "streaming", "tool_calling", "vision"].map((n) => ({ name: n, status: "running", detail: "" })));
    try {
      const r = await api<{ ok: boolean; checks: Check[] }>("/api/test-connection", { method: "POST", json: body, keys: reqKeys });
      setChecks(r.checks);
      if (r.ok) {
        if (saveToAccount) {
          await api("/api/settings/remember", { method: "POST", json: { llm_api_key: draft.llmApiKey || undefined, llm_base_url: draft.llmBaseUrl, llm_model: draft.llmModel } });
          setKeys({ llmApiKey: "", llmBaseUrl: "", llmModel: "", preset: draft.preset });
        } else {
          setKeys({ llmApiKey: draft.llmApiKey, llmBaseUrl: draft.llmBaseUrl, llmModel: draft.llmModel, preset: draft.preset });
        }
        await api("/api/settings", { method: "PUT", json: { llm_base_url: draft.llmBaseUrl, llm_model: draft.llmModel, llm_preset: draft.preset } });
        await loadServer();
        notify("Model connected", "ok");
        onValid?.();
      }
    } catch (e: any) {
      setChecks(null);
      setError(e.message);
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="flex flex-col gap-5">
      <div>
        <Label className="mb-2 block">Provider</Label>
        <ToggleButtonGroup selectionMode="single" disallowEmptySelection selectedKeys={new Set([draft.preset])} className="w-full"
          onSelectionChange={(keys) => {
            const id = [...(keys as Set<Key>)][0] as string;
            const p = PRESETS.find((x) => x.id === id)!;
            setDraft({ ...draft, preset: id, llmBaseUrl: p.url || draft.llmBaseUrl });
            setModels([]);
          }}>
          {PRESETS.map((p, i) => (
            <ToggleButton key={p.id} id={p.id} className="flex-1">
              {i > 0 && <ToggleButtonGroup.Separator />}
              {p.label}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
      </div>
      <SecretField label="API key" value={draft.llmApiKey} onChange={(v) => setDraft({ ...draft, llmApiKey: v })}
        placeholder={savedKey ? `Saved to your account (${server?.llm.key_masked})` : draft.preset === "openrouter" ? "sk-or-v1-…" : draft.preset === "openai" ? "sk-…" : "Optional for local servers"}
        description={server?.env_prefill.llm_api_key && !savedKey ? "The server has LLM_API_KEY set — leave empty to use it." : undefined} />
      <Field label="Base URL" mono value={draft.llmBaseUrl} onChange={(v) => setDraft({ ...draft, llmBaseUrl: v, preset: PRESETS.find((p) => p.url === v)?.id ?? "custom" })}
        placeholder="http://host.docker.internal:11434/v1" />
      <div className="flex items-end gap-2">
        <ComboBox allowsCustomValue className="min-w-0 flex-1" inputValue={draft.llmModel} onInputChange={(v) => setDraft({ ...draft, llmModel: v })}
          defaultItems={models} menuTrigger="focus" onSelectionChange={(k) => k && setDraft({ ...draft, llmModel: String(k) })}>
          <Label>Model</Label>
          <ComboBox.InputGroup>
            <Input placeholder="Any model ID" className="font-mono text-[13px]" />
            <ComboBox.Trigger />
          </ComboBox.InputGroup>
          <ComboBox.Popover>
            <ListBox renderEmptyState={() => <div className="px-3 py-2 text-xs text-muted">{models.length ? "No match — the typed ID is used as-is." : "Load models to browse, or type an ID."}</div>}>
              {(m: ModelInfo) => (
                <ListBox.Item id={m.id} textValue={m.id}>
                  <span className="min-w-0 flex-1 truncate font-mono text-[13px]">{m.id}</span>
                  {m.tools && <Chip size="sm" color="success" variant="soft">tools</Chip>}
                  {m.vision && <Chip size="sm" color="accent" variant="soft">vision</Chip>}
                  <ListBox.ItemIndicator />
                </ListBox.Item>
              )}
            </ListBox>
          </ComboBox.Popover>
          {models.length > 0 && <Description>{models.length} models available</Description>}
        </ComboBox>
        <Button variant="secondary" onPress={loadModels} isPending={loading} className={models.length ? "mb-6" : ""}>
          {({ isPending }) => <>{isPending && <Spinner size="sm" color="current" />}Load models</>}
        </Button>
      </div>
      <Toggle isSelected={saveToAccount} onChange={setSaveToAccount} label="Save to my account"
        description="Encrypted on this server, so it works in any browser you sign in from. Off: kept in this browser only." />
      {error && <p role="alert" className="rounded-xl bg-danger/10 px-3 py-2 text-sm text-danger-ink">{error}</p>}
      {checks && <Checks checks={checks} />}
      <div className="flex justify-end">
        <Button onPress={test} isPending={testing} isDisabled={!draft.llmBaseUrl || !draft.llmModel}>
          {({ isPending }) => <>{isPending && <Spinner size="sm" color="current" />}Test & save</>}
        </Button>
      </div>
    </div>
  );
}

export function ElevenLabsConnection() {
  const { keys, setKeys, server, loadServer, notify } = useStore();
  const [key, setKey] = useState(keys.elevenlabsApiKey);
  const [saveToAccount, setSaveToAccount] = useState(true);
  const [res, setRes] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const saved = server?.elevenlabs.key_source === "server";

  const test = async () => {
    setBusy(true);
    setError("");
    setRes(null);
    try {
      const r = await api<any>("/api/elevenlabs/test", { method: "POST", json: { api_key: key || undefined }, keys: { ...keys, elevenlabsApiKey: key } });
      setRes(r);
      if (r.ok && key) {
        if (saveToAccount) {
          await api("/api/settings/remember", { method: "POST", json: { elevenlabs_api_key: key } });
          setKeys({ elevenlabsApiKey: "" });
          setKey("");
        } else setKeys({ elevenlabsApiKey: key });
        await loadServer();
        notify("ElevenLabs connected", "ok");
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    setKey("");
    setKeys({ elevenlabsApiKey: "" });
    await api("/api/settings/remember", { method: "POST", json: { elevenlabs_api_key: "" } }).catch(() => {});
    await loadServer();
    setRes(null);
  };
  const acct = res?.account;
  return (
    <div className="flex flex-col gap-5">
      <SecretField label="API key" value={key} onChange={setKey} placeholder={saved ? `Saved to your account (${server?.elevenlabs.key_masked})` : "sk_…"}
        description="Optional. Enables voice-over with word timings, voice design, sound effects and music. Never enters the agent's terminal." />
      <Toggle isSelected={saveToAccount} onChange={setSaveToAccount} label="Save to my account" description="Encrypted on this server." />
      {error && <p role="alert" className="rounded-xl bg-danger/10 px-3 py-2 text-sm text-danger-ink">{error}</p>}
      {acct && (
        <div className="rounded-xl border border-border bg-surface p-3.5">
          <Meter value={acct.character_limit ? (100 * acct.character_count) / acct.character_limit : 0} aria-label="Characters used">
            <Label className="text-sm capitalize">{acct.tier} plan</Label>
            <Meter.Output className="text-xs text-muted">{acct.character_count?.toLocaleString()} / {acct.character_limit?.toLocaleString()} characters</Meter.Output>
            <Meter.Track><Meter.Fill /></Meter.Track>
          </Meter>
        </div>
      )}
      {res?.checks && <Checks checks={res.checks.filter((c: Check) => c.name !== "auth" || c.status !== "ok")} />}
      <div className="flex justify-end gap-2">
        {(saved || keys.elevenlabsApiKey) && <Button variant="ghost" onPress={remove}>Remove key</Button>}
        <Button variant="secondary" onPress={test} isPending={busy} isDisabled={!key && !saved && !server?.env_prefill.elevenlabs_api_key}>
          {({ isPending }) => <>{isPending && <Spinner size="sm" color="current" />}Test & save</>}
        </Button>
      </div>
    </div>
  );
}
