import { Button, Chip, Label, ListBox, Modal, NumberField, Select, Spinner, TextArea, TextField, ToggleButton, ToggleButtonGroup } from "@heroui/react";
import { Plus, SlidersHorizontal, X } from "@phosphor-icons/react";
import { useEffect, useState, type Key } from "react";
import { api, type ProjectSettings as PS } from "../lib/api";
import { useStore } from "../lib/store";
import { Field, Toggle } from "../ui/kit";

const RESOLUTIONS = [
  { id: "1920x1080", label: "1920 × 1080", note: "16:9" },
  { id: "1080x1920", label: "1080 × 1920", note: "9:16" },
  { id: "1080x1080", label: "1080 × 1080", note: "1:1" },
  { id: "3840x2160", label: "3840 × 2160", note: "4K" },
];

function Num({ label, value, onChange, step, min = 0, max }: { label: string; value: number; onChange: (v: number) => void; step: number; min?: number; max?: number }) {
  return (
    <NumberField value={value ?? 0} onChange={(v) => Number.isFinite(v) && onChange(v)} step={step} minValue={min} maxValue={max} className="w-full">
      <Label>{label}</Label>
      <NumberField.Group>
        <NumberField.DecrementButton />
        <NumberField.Input className="tabular-nums" />
        <NumberField.IncrementButton />
      </NumberField.Group>
    </NumberField>
  );
}

export function ProjectSettingsModal() {
  const { projects, projectId, projectSettingsOpen, setProjectSettingsOpen, refreshProjects, notify } = useStore();
  const project = projects.find((p) => p.id === projectId);
  const [st, setSt] = useState<PS | null>(null);
  const [brief, setBrief] = useState("");
  const [avoid, setAvoid] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (projectSettingsOpen && project) { setSt(project.settings); setBrief(project.brief); }
  }, [projectSettingsOpen, project?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const up = (p: Partial<PS>) => st && setSt({ ...st, ...p });
  const addAvoid = () => {
    const v = avoid.trim().replace(/^#/, "");
    if (!st || !/^[0-9a-fA-F]{3}([0-9a-fA-F]{3})?$/.test(v)) return;
    up({ avoid_colors: Array.from(new Set([...st.avoid_colors, "#" + v.toUpperCase()])) });
    setAvoid("");
  };
  const save = async () => {
    if (!project || !st) return;
    setSaving(true);
    try {
      await api(`/api/projects/${project.id}`, { method: "PATCH", json: { brief, settings: st } });
      await refreshProjects();
      notify("Project saved", "ok");
      setProjectSettingsOpen(false);
    } catch (e: any) {
      notify(e.message, "bad");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal.Backdrop isOpen={projectSettingsOpen} onOpenChange={setProjectSettingsOpen}>
      <Modal.Container size="lg" scroll="inside">
        <Modal.Dialog className="sm:max-w-[600px]">
          <Modal.CloseTrigger />
          <Modal.Header>
            <Modal.Icon className="bg-default text-foreground"><SlidersHorizontal size={20} /></Modal.Icon>
            <Modal.Heading>Brief & output</Modal.Heading>
            <p className="text-sm text-muted">The director reads these before every step.</p>
          </Modal.Header>
          <Modal.Body>
            {st && (
              <div className="flex flex-col gap-6">
                <TextField value={brief} onChange={setBrief} className="w-full">
                  <Label>Brief</Label>
                  <TextArea rows={4} placeholder="Audience, mood, must-haves, what to avoid…" className="w-full resize-none" />
                </TextField>

                <div className="grid gap-4 sm:grid-cols-2">
                  <Select selectedKey={`${st.width}x${st.height}`} className="w-full"
                    onSelectionChange={(k) => { const [w, h] = String(k).split("x").map(Number); up({ width: w, height: h }); }}>
                    <Label>Resolution</Label>
                    <Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
                    <Select.Popover>
                      <ListBox>
                        {RESOLUTIONS.map((r) => (
                          <ListBox.Item key={r.id} id={r.id} textValue={`${r.label} ${r.note}`}>
                            <span className="flex-1 tabular-nums">{r.label}<span className="ml-2 text-xs text-muted">{r.note}</span></span>
                            <ListBox.ItemIndicator />
                          </ListBox.Item>
                        ))}
                      </ListBox>
                    </Select.Popover>
                  </Select>
                  <div className="flex flex-col gap-1">
                    <Label>Frame rate</Label>
                    <ToggleButtonGroup selectionMode="single" disallowEmptySelection selectedKeys={new Set([String(st.fps)])} className="w-full"
                      onSelectionChange={(keys) => up({ fps: Number([...(keys as Set<Key>)][0]) })} aria-label="Frame rate">
                      {["24", "30", "60"].map((f, i) => (
                        <ToggleButton key={f} id={f} className="flex-1 tabular-nums">{i > 0 && <ToggleButtonGroup.Separator />}{f} fps</ToggleButton>
                      ))}
                    </ToggleButtonGroup>
                  </div>
                  <NumberField value={st.duration} onChange={(v) => Number.isFinite(v) && up({ duration: v })} minValue={3} maxValue={180} step={0.5} className="w-full">
                    <Label>Duration (seconds)</Label>
                    <NumberField.Group>
                      <NumberField.DecrementButton />
                      <NumberField.Input className="tabular-nums" />
                      <NumberField.IncrementButton />
                    </NumberField.Group>
                  </NumberField>
                </div>

                <div className="flex flex-col gap-4 rounded-2xl border border-border bg-surface p-4">
                  <Toggle isSelected={st.formats.includes("prores")} onChange={(v) => up({ formats: v ? ["mp4", "prores"] : ["mp4"] })}
                    label="ProRes 422 HQ master" description="Delivered alongside the H.264 MP4." />
                  <Toggle isSelected={st.captions} onChange={(v) => up({ captions: v })} label="Captions" description="Burned-in and SRT for voiced films." />
                </div>

                <div className="flex flex-col gap-2">
                  <Label>Colours to avoid</Label>
                  {st.avoid_colors.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {st.avoid_colors.map((c) => (
                        <Chip key={c} variant="secondary" className="gap-1.5 pr-1 font-mono">
                          <span className="size-3 rounded-full ring-1 ring-black/10" style={{ background: c }} />
                          <Chip.Label>{c}</Chip.Label>
                          <button aria-label={`Remove ${c}`} className="rounded-full p-0.5 text-muted hover:text-danger-ink"
                            onClick={() => up({ avoid_colors: st.avoid_colors.filter((x) => x !== c) })}><X size={12} /></button>
                        </Chip>
                      ))}
                    </div>
                  )}
                  <div className="flex gap-2">
                    <div className="flex-1" onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addAvoid(); } }}>
                      <Field label="Hex colour" value={avoid} onChange={setAvoid} placeholder="#FF6A00" mono />
                    </div>
                    <Button variant="secondary" className="mt-6" onPress={addAvoid} aria-label="Add colour"><Plus size={16} /> Add</Button>
                  </div>
                </div>

                <section className="flex flex-col gap-4 rounded-2xl border border-border bg-surface p-4" aria-label="Collaboration">
                  <div>
                    <h3 className="text-[16px] text-foreground">Collaboration</h3>
                    <p className="text-xs text-muted">When the director must stop for your sign-off.</p>
                  </div>
                  <Toggle isSelected={!!st.autopilot} onChange={(v) => up({ autopilot: v })} label="Autopilot"
                    description="Skip approval gates: expensive steps run without asking." />
                  {!st.autopilot && (
                    <div className="grid gap-3 sm:grid-cols-2">
                      <Num label="Render longer than (min)" value={st.approval_render_minutes} step={1} onChange={(v) => up({ approval_render_minutes: v })} />
                      <Num label="ElevenLabs request over (chars)" value={st.approval_el_chars} step={100} onChange={(v) => up({ approval_el_chars: v })} />
                      <Num label="Run cost over ($)" value={st.approval_cost_usd} step={0.5} onChange={(v) => up({ approval_cost_usd: v })} />
                      <Num label="Run time over (min)" value={st.approval_run_minutes} step={5} onChange={(v) => up({ approval_run_minutes: v })} />
                    </div>
                  )}
                  <Toggle isSelected={!!st.web_access} onChange={(v) => up({ web_access: v })} label="Web access"
                    description="Let the director search and read web pages (docs, font licences). Content is treated as untrusted." />
                  <Toggle isSelected={!!st.subagents} onChange={(v) => up({ subagents: v })} label="Sub-agents"
                    description="Let the director delegate parallel sub-tasks to helper agents." />
                  {st.subagents && (
                    <div className="grid gap-3 sm:grid-cols-2">
                      <Num label="Parallel sub-agents" value={st.subagent_max_concurrency} step={1} min={1} max={6} onChange={(v) => up({ subagent_max_concurrency: v })} />
                      <Num label="Cost cap per sub-agent ($)" value={st.subagent_max_cost_usd} step={0.25} onChange={(v) => up({ subagent_max_cost_usd: v })} />
                    </div>
                  )}
                </section>

                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="Voice language" value={st.voice_language} onChange={(v) => up({ voice_language: v })} placeholder="en" />
                  <Field label="Voice tone" value={st.voice_tone} onChange={(v) => up({ voice_tone: v })} placeholder="Calm, warm" />
                </div>
              </div>
            )}
          </Modal.Body>
          <Modal.Footer>
            <Button slot="close" variant="tertiary">Cancel</Button>
            <Button onPress={save} isPending={saving}>{({ isPending }) => <>{isPending && <Spinner size="sm" color="current" />}Save</>}</Button>
          </Modal.Footer>
        </Modal.Dialog>
      </Modal.Container>
    </Modal.Backdrop>
  );
}
