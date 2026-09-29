import { Button, Label, Modal, NumberField, Spinner, Tabs, TextArea } from "@heroui/react";
import { ArrowCounterClockwise, GearSix } from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import { SecretField, ThemeSwitch, Toggle } from "../ui/kit";
import { ElevenLabsConnection, LlmConnection } from "./Connections";

export function SettingsModal() {
  const { settingsOpen, setSettingsOpen, settingsTab, setSettingsTab } = useStore();
  return (
    <Modal.Backdrop isOpen={settingsOpen} onOpenChange={setSettingsOpen}>
      <Modal.Container size="lg" scroll="inside">
        <Modal.Dialog className="sm:max-w-[680px]">
          <Modal.CloseTrigger />
          <Modal.Header>
            <Modal.Icon className="bg-default text-foreground"><GearSix size={20} /></Modal.Icon>
            <Modal.Heading>Settings</Modal.Heading>
          </Modal.Header>
          <Modal.Body className="min-h-[420px]">
            <Tabs selectedKey={settingsTab} onSelectionChange={(k) => setSettingsTab(String(k))}>
              <Tabs.ListContainer>
                <Tabs.List aria-label="Settings sections">
                  <Tabs.Tab id="connections">Connections<Tabs.Indicator /></Tabs.Tab>
                  <Tabs.Tab id="director">Director<Tabs.Indicator /></Tabs.Tab>
                  <Tabs.Tab id="limits">Limits<Tabs.Indicator /></Tabs.Tab>
                  <Tabs.Tab id="appearance">Appearance<Tabs.Indicator /></Tabs.Tab>
                  <Tabs.Tab id="account">Account<Tabs.Indicator /></Tabs.Tab>
                </Tabs.List>
              </Tabs.ListContainer>
              <Tabs.Panel id="connections" className="pt-6">
                <section>
                  <h3 className="text-[15px] text-foreground">Language model</h3>
                  <p className="mb-4 mt-0.5 text-sm text-muted">Any OpenAI-compatible endpoint with streaming tool calls.</p>
                  <LlmConnection />
                </section>
                <section className="mt-8 border-t border-separator pt-6">
                  <h3 className="text-[15px] text-foreground">ElevenLabs</h3>
                  <p className="mb-4 mt-0.5 text-sm text-muted">Voice, sound effects and music. Optional.</p>
                  <ElevenLabsConnection />
                </section>
              </Tabs.Panel>
              <Tabs.Panel id="director" className="pt-6"><DirectorPrompt /></Tabs.Panel>
              <Tabs.Panel id="limits" className="pt-6"><Limits /></Tabs.Panel>
              <Tabs.Panel id="appearance" className="pt-6"><Appearance /></Tabs.Panel>
              <Tabs.Panel id="account" className="pt-6"><Account /></Tabs.Panel>
            </Tabs>
          </Modal.Body>
        </Modal.Dialog>
      </Modal.Container>
    </Modal.Backdrop>
  );
}

function Appearance() {
  return (
    <section>
      <h3 className="text-[16px] text-foreground">Theme</h3>
      <p className="mb-4 mt-0.5 text-sm text-muted">System follows your device and switches with it. The choice is remembered in this browser.</p>
      <ThemeSwitch />
    </section>
  );
}

function DirectorPrompt() {
  const notify = useStore((s) => s.notify);
  const [prompt, setPrompt] = useState("");
  const [customized, setCustomized] = useState(false);
  const [saving, setSaving] = useState(false);
  const load = () => api<{ prompt: string; customized: boolean }>("/api/settings/director-prompt").then((r) => { setPrompt(r.prompt); setCustomized(r.customized); });
  useEffect(() => { load(); }, []);
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-muted">
        The system prompt that sets the director's workflow and quality bar. The brief, output settings, asset analysis and plan are appended automatically.
      </p>
      <TextArea aria-label="Director system prompt" value={prompt} onChange={(e) => setPrompt(e.target.value)} spellCheck={false}
        className="h-[46vh] w-full resize-none font-mono text-[12px] leading-relaxed" />
      <div className="flex justify-between">
        <Button variant="ghost" isDisabled={!customized} onPress={async () => {
          await api("/api/settings", { method: "PUT", json: { reset_director_prompt: true } });
          await load();
          notify("Director prompt reset", "ok");
        }}>
          <ArrowCounterClockwise size={16} /> Reset to default
        </Button>
        <Button isPending={saving} onPress={async () => {
          setSaving(true);
          try {
            await api("/api/settings", { method: "PUT", json: { director_prompt: prompt } });
            setCustomized(true);
            notify("Director prompt saved", "ok");
          } catch (e: any) {
            notify(e.message, "bad");
          } finally {
            setSaving(false);
          }
        }}>
          {({ isPending }) => <>{isPending && <Spinner size="sm" color="current" />}Save prompt</>}
        </Button>
      </div>
    </div>
  );
}

const LIMITS: { key: string; label: string; hint: string; step?: number; min?: number; max?: number }[] = [
  { key: "temperature", label: "Temperature", hint: "Lower is more deterministic.", step: 0.1, min: 0, max: 2 },
  { key: "max_steps", label: "Max tool steps", hint: "Model calls per activation.", min: 1 },
  { key: "wall_clock_minutes", label: "Time limit (min)", hint: "Stops runs that go on too long.", min: 1 },
  { key: "context_budget_tokens", label: "Context budget", hint: "Older turns are summarised here.", step: 1000, min: 8000 },
  { key: "el_char_budget", label: "ElevenLabs characters / run", hint: "Hard cap, checked before each request.", step: 100, min: 0 },
  { key: "job_concurrency", label: "Parallel render jobs", hint: "Extra jobs queue up.", min: 1 },
  { key: "tool_wait_seconds", label: "Render wait (s)", hint: "Then the render continues as a job.", min: 5 },
  { key: "plan_required_after_steps", label: "Plan required after (steps)", hint: "Without a plan, only planning tools run after this. 0 = off.", min: 0 },
  { key: "max_cost_usd", label: "Cost cap per run ($)", hint: "Stops the run gracefully. 0 = none.", step: 0.5, min: 0 },
  { key: "max_tokens", label: "Token cap per run", hint: "Stops the run gracefully. 0 = none.", step: 10000, min: 0 },
  { key: "toolbox_max_tools", label: "Toolbox tools per step", hint: "Most relevant + most used; the rest via toolbox_search.", min: 0, max: 100 },
  { key: "skill_required_after_steps", label: "Skill required after (steps)", hint: "finish asks for a playbook after a hard run. 0 = off.", min: 0 },
];

function Limits() {
  const { server, loadServer, notify } = useStore();
  const [s, setS] = useState<Record<string, any>>({});
  const [saving, setSaving] = useState(false);
  useEffect(() => { loadServer().then((r) => setS(r.settings)); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async (patch: Record<string, any>) => {
    setSaving(true);
    try {
      setS(await api("/api/settings", { method: "PUT", json: patch }));
      notify("Saved", "ok");
    } catch (e: any) {
      notify(e.message, "bad");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <div className="grid gap-x-4 gap-y-5 sm:grid-cols-2">
        {LIMITS.map((l) => (
          <NumberField key={l.key} value={typeof s[l.key] === "number" ? s[l.key] : NaN} onChange={(v) => setS({ ...s, [l.key]: v })}
            step={l.step ?? 1} minValue={l.min} maxValue={l.max} className="w-full">
            <Label>{l.label}</Label>
            <NumberField.Group>
              <NumberField.DecrementButton />
              <NumberField.Input className="tabular-nums" />
              <NumberField.IncrementButton />
            </NumberField.Group>
            <p className="mt-1 text-xs text-muted">{l.hint}</p>
          </NumberField>
        ))}
      </div>
      <div className="rounded-xl border border-border bg-surface p-4">
        <Toggle isSelected={!!s.terminal_network} onChange={(v) => { setS({ ...s, terminal_network: v }); save({ terminal_network: v }); }}
          label="Terminal network access"
          description={server?.network_control_available
            ? "On so package installs work. Off blocks all outbound traffic from the sandbox user."
            : "The container has no network helper, so this can't be enforced here (see README)."} />
        <div className="my-4 h-px bg-separator" />
        <Toggle isSelected={!!s.toolbox_network} onChange={(v) => { setS({ ...s, toolbox_network: v }); save({ toolbox_network: v }); }}
          label="Toolbox tools may use the network"
          description="Only tools whose manifest declares network: true, and only while this is on. Others run firewalled." />
        <div className="my-4 h-px bg-separator" />
        <Toggle isSelected={!!s.allow_global_promotion} onChange={(v) => { setS({ ...s, allow_global_promotion: v }); save({ allow_global_promotion: v }); }}
          label="Autopilot may promote tools to global"
          description="With project Autopilot, tool_promote skips the approval card. The promotion scan still applies." />
      </div>
      <div className="flex justify-end">
        <Button isPending={saving} onPress={() => save(Object.fromEntries(LIMITS.filter((l) => Number.isFinite(s[l.key])).map((l) => [l.key, s[l.key]])))}>
          {({ isPending }) => <>{isPending && <Spinner size="sm" color="current" />}Save limits</>}
        </Button>
      </div>
    </div>
  );
}

function Account() {
  const { user, logout, notify, server, loadServer } = useStore();
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const saved = server ? Object.entries(server.remembered).filter(([, v]) => v).map(([k]) => k) : [];
  const change = async () => {
    setBusy(true);
    setError("");
    try {
      await api("/api/auth/password", { method: "POST", json: { current_password: cur, new_password: next } });
      notify("Password changed. Please sign in again.", "ok");
      await logout();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex flex-col gap-8">
      <div className="flex items-center justify-between rounded-xl border border-border bg-surface px-4 py-3">
        <div>
          <div className="text-sm text-muted">Signed in as</div>
          <div className="font-medium">{user?.username}</div>
        </div>
        <Button variant="secondary" onPress={logout}>Sign out</Button>
      </div>
      <section className="flex flex-col gap-4">
        <h3 className="text-[15px] text-foreground">Change password</h3>
        <SecretField label="Current password" value={cur} onChange={setCur} autoComplete="current-password" />
        <SecretField label="New password" value={next} onChange={setNext} autoComplete="new-password" description="At least 8 characters. You'll be signed out everywhere." />
        {error && <p role="alert" className="text-sm text-danger-ink">{error}</p>}
        <div className="flex justify-end">
          <Button variant="secondary" isPending={busy} isDisabled={!cur || next.length < 8} onPress={change}>
            {({ isPending }) => <>{isPending && <Spinner size="sm" color="current" />}Update password</>}
          </Button>
        </div>
      </section>
      <section className="flex flex-col gap-3 border-t border-separator pt-6">
        <h3 className="text-[15px] text-foreground">Saved keys</h3>
        <p className="text-sm text-muted">
          {saved.length ? `Encrypted on this server for your account: ${saved.join(", ").replaceAll("_", " ")}.` : "No keys are saved to your account."}
        </p>
        {saved.length > 0 && (
          <div>
            <Button variant="danger-soft" onPress={async () => { await api("/api/settings/remember", { method: "DELETE" }); await loadServer(); notify("Saved keys removed", "ok"); }}>
              Remove saved keys
            </Button>
          </div>
        )}
      </section>
    </div>
  );
}
