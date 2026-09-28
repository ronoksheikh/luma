import { RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";
import { Button, Dialog, Input, Label, Switch, Tabs, TabsContent, TabsList, TabsTrigger, Textarea } from "../components/ui";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import { ElevenLabsConnection, LlmConnection } from "./Connections";

export function SettingsDialog() {
  const { settingsOpen, setSettingsOpen, server, loadServer, notify } = useStore();
  const [s, setS] = useState<Record<string, any>>({});
  const [prompt, setPrompt] = useState("");
  const [customized, setCustomized] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!settingsOpen) return;
    loadServer().then((r) => setS(r.settings));
    api<{ prompt: string; customized: boolean }>("/api/settings/director-prompt").then((r) => { setPrompt(r.prompt); setCustomized(r.customized); });
  }, [settingsOpen]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async (patch: Record<string, any>) => {
    setSaving(true);
    try {
      const r = await api("/api/settings", { method: "PUT", json: patch });
      setS(r);
      notify("Settings saved", "ok");
    } catch (e: any) {
      notify(e.message, "bad");
    } finally {
      setSaving(false);
    }
  };

  const num = (k: string, label: string, hint: string, step = 1) => (
    <div>
      <Label htmlFor={`s-${k}`}>{label}</Label>
      <Input id={`s-${k}`} type="number" step={step} value={s[k] ?? ""} onChange={(e) => setS({ ...s, [k]: e.target.value === "" ? "" : Number(e.target.value) })} />
      <p className="mt-1 text-[11px] text-faint">{hint}</p>
    </div>
  );

  return (
    <Dialog open={settingsOpen} onOpenChange={setSettingsOpen} title="Settings" description="Connections, director prompt and run limits." wide>
      <Tabs defaultValue="connections" className="flex flex-col">
        <TabsList className="mb-4 px-0">
          <TabsTrigger value="connections">Connections</TabsTrigger>
          <TabsTrigger value="director">Director prompt</TabsTrigger>
          <TabsTrigger value="limits">Limits & sandbox</TabsTrigger>
        </TabsList>
        <TabsContent value="connections" className="space-y-6">
          <section>
            <h3 className="mb-3 text-sm font-semibold">Language model</h3>
            <LlmConnection />
          </section>
          <section className="border-t border-line pt-5">
            <h3 className="mb-3 text-sm font-semibold">ElevenLabs</h3>
            <ElevenLabsConnection />
          </section>
          {server && Object.values(server.remembered).some(Boolean) && (
            <div className="flex items-center justify-between rounded-lg border border-line px-3 py-2 text-xs text-muted">
              Keys remembered on this server: {Object.entries(server.remembered).filter(([, v]) => v).map(([k]) => k).join(", ")}
              <Button size="sm" variant="danger" onClick={async () => { await api("/api/settings/remember", { method: "DELETE" }); await loadServer(); notify("Forgot remembered keys", "ok"); }}>Forget</Button>
            </div>
          )}
        </TabsContent>
        <TabsContent value="director" className="space-y-3">
          <p className="text-xs text-muted">The system prompt that encodes the workflow and quality bar. Pinned project facts (brief, settings, assets, work/brand.json, work/plan.md, work/events.json) are appended automatically.</p>
          <Textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} className="h-[48vh] font-mono text-xs leading-relaxed" spellCheck={false} />
          <div className="flex justify-between">
            <Button variant="ghost" disabled={!customized} onClick={async () => {
              await api("/api/settings", { method: "PUT", json: { reset_director_prompt: true } });
              const r = await api<{ prompt: string; customized: boolean }>("/api/settings/director-prompt");
              setPrompt(r.prompt); setCustomized(false); notify("Director prompt reset", "ok");
            }}><RotateCcw className="h-3.5 w-3.5" /> Reset to default</Button>
            <Button variant="primary" loading={saving} onClick={async () => { await save({ director_prompt: prompt }); setCustomized(true); }}>Save prompt</Button>
          </div>
        </TabsContent>
        <TabsContent value="limits" className="space-y-5">
          <div className="grid gap-4 sm:grid-cols-2">
            {num("temperature", "Temperature", "0–2. Lower is more deterministic.", 0.1)}
            {num("max_steps", "Max tool steps", "Model calls per activation (default 120).")}
            {num("wall_clock_minutes", "Wall-clock limit (minutes)", "Stops a run that goes on too long.")}
            {num("context_budget_tokens", "Context budget (tokens)", "Older turns are summarised near this budget.", 1000)}
            {num("el_char_budget", "ElevenLabs character budget per run", "Hard cap, checked before every request.", 100)}
            {num("job_concurrency", "Concurrent render jobs", "Detached jobs beyond this queue up.")}
            {num("tool_wait_seconds", "render_final wait (s)", "After this, the render keeps running as a job.")}
          </div>
          <div className="flex items-center justify-between rounded-lg border border-line px-3 py-2.5">
            <div>
              <div className="text-sm">Terminal network access</div>
              <div className="text-xs text-faint">On by default so installs work. Off blocks outbound traffic from the sandbox user
                {server?.network_control_available ? " (iptables owner match)." : " — not available: the container lacks the network helper (see README)."}</div>
            </div>
            <Switch checked={!!s.terminal_network} onCheckedChange={(v) => { setS({ ...s, terminal_network: v }); save({ terminal_network: v }); }} label="Terminal network access" />
          </div>
          <div className="flex justify-end">
            <Button variant="primary" loading={saving} onClick={() => {
              const keys = ["temperature", "max_steps", "wall_clock_minutes", "context_budget_tokens", "el_char_budget", "job_concurrency", "tool_wait_seconds"];
              save(Object.fromEntries(keys.filter((k) => s[k] !== "" && s[k] != null).map((k) => [k, s[k]])));
            }}>Save limits</Button>
          </div>
        </TabsContent>
      </Tabs>
    </Dialog>
  );
}
