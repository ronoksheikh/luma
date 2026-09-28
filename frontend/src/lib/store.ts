import { toast } from "@heroui/react";
import { create } from "zustand";
import { api, loadKeys, saveKeys, type Keys, type Project, type Run, type User } from "./api";

export type ServerSettings = {
  settings: Record<string, any>;
  llm: { configured: boolean; base_url: string; model: string; key_source: string; key_masked: string; capabilities: Record<string, any> };
  elevenlabs: { configured: boolean; key_source: string; key_masked: string; capabilities: Record<string, any> };
  env_prefill: { llm_base_url: string; llm_model: string; llm_api_key: boolean; elevenlabs_api_key: boolean };
  remembered: Record<string, boolean>;
  presets: Record<string, { base_url: string; label: string }>;
  limits: { max_files: number; max_file_mb: number };
  network_control_available: boolean;
};

type State = {
  user: User | null;
  authChecked: boolean;
  signupOpen: boolean;
  setUser: (u: User | null) => void;
  checkAuth: () => Promise<User | null>;
  logout: () => Promise<void>;
  keys: Keys;
  server: ServerSettings | null;
  projects: Project[];
  projectId: string | null;
  runId: string | null;
  runs: Run[];
  settingsOpen: boolean;
  setupDone: boolean;
  rightTab: string;

  setKeys: (k: Partial<Keys>) => void;
  loadServer: () => Promise<ServerSettings>;
  refreshProjects: () => Promise<Project[]>;
  selectProject: (id: string | null) => Promise<void>;
  refreshRuns: () => Promise<void>;
  setRun: (id: string | null) => void;
  setSettingsOpen: (v: boolean) => void;
  settingsTab: string;
  setSettingsTab: (t: string) => void;
  openSettings: (tab?: string) => void;
  projectSettingsOpen: boolean;
  setProjectSettingsOpen: (v: boolean) => void;
  navOpen: boolean;
  assetsVersion: number;
  bumpAssets: () => void;
  setNavOpen: (v: boolean) => void;
  setSetupDone: (v: boolean) => void;
  setRightTab: (t: string) => void;
  notify: (text: string, tone?: "ok" | "bad" | "info") => void;
  mainView: "chat" | "inspector";
  setMainView: (v: "chat" | "inspector") => void;
};

export const useStore = create<State>((set, get) => ({
  user: null,
  authChecked: false,
  signupOpen: true,
  setUser: (u) => set({ user: u }),
  checkAuth: async () => {
    const st = await api<{ user: User | null; signup_open: boolean }>("/api/auth/state");
    set({ user: st.user, authChecked: true, signupOpen: st.signup_open });
    return st.user;
  },
  logout: async () => {
    await api("/api/auth/logout", { method: "POST" }).catch(() => {});
    localStorage.removeItem("luma.project");
    set({ user: null, projects: [], projectId: null, runId: null, runs: [], server: null });
  },
  keys: loadKeys(),
  server: null,
  projects: [],
  projectId: localStorage.getItem("luma.project") || null,
  runId: null,
  runs: [],
  settingsOpen: false,
  setupDone: localStorage.getItem("luma.setupDone") === "1",
  rightTab: localStorage.getItem("luma.rightTab") || "preview",
  mainView: "chat",
  setMainView: (v) => set({ mainView: v }),
  setKeys: (k) => {
    const keys = { ...get().keys, ...k };
    saveKeys(keys);
    set({ keys });
  },
  loadServer: async () => {
    const s = await api<ServerSettings>("/api/settings");
    set({ server: s });
    return s;
  },
  refreshProjects: async () => {
    const ps = await api<Project[]>("/api/projects");
    set({ projects: ps });
    const cur = get().projectId;
    if ((!cur || !ps.find((p) => p.id === cur)) && ps.length) await get().selectProject(ps[0].id);
    if (!ps.length) set({ projectId: null, runId: null, runs: [] });
    return ps;
  },
  selectProject: async (id) => {
    if (id) localStorage.setItem("luma.project", id);
    set({ projectId: id, runId: null, runs: [] });
    if (id) await get().refreshRuns();
  },
  refreshRuns: async () => {
    const pid = get().projectId;
    if (!pid) return;
    const runs = (await api<Run[]>(`/api/projects/${pid}/runs`)).filter((r) => r.kind !== "subagent");
    const keep = get().runId && runs.find((r) => r.id === get().runId) ? get().runId : runs[0]?.id ?? null;
    set({ runs, runId: keep });
  },
  setRun: (id) => set({ runId: id }),
  setSettingsOpen: (v) => set({ settingsOpen: v }),
  settingsTab: "connections",
  setSettingsTab: (t) => set({ settingsTab: t }),
  openSettings: (tab = "connections") => set({ settingsOpen: true, settingsTab: tab }),
  projectSettingsOpen: false,
  setProjectSettingsOpen: (v) => set({ projectSettingsOpen: v }),
  navOpen: false,
  assetsVersion: 0,
  bumpAssets: () => set({ assetsVersion: get().assetsVersion + 1 }),
  setNavOpen: (v) => set({ navOpen: v }),
  setSetupDone: (v) => {
    localStorage.setItem("luma.setupDone", v ? "1" : "0");
    set({ setupDone: v });
  },
  setRightTab: (t) => {
    localStorage.setItem("luma.rightTab", t);
    set({ rightTab: t });
  },
  notify: (text, tone = "info") => {
    if (tone === "ok") toast.success(text);
    else if (tone === "bad") toast.danger(text);
    else toast(text);
  },
}));
