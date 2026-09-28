// The bell (notification history) and browser notifications for approvals, finished runs and notify().
import { Badge, Button, Popover } from "@heroui/react";
import { Bell, CheckCircle, Info, Warning, XCircle } from "@phosphor-icons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { RunState } from "../lib/run";
import { useStore } from "../lib/store";
import { cn } from "../ui/kit";

type N = { id: number; message: string; level: string; created_at: number; read: boolean; project_id: string; run_id: string | null };

const PERM_KEY = "luma.notifyAsked";

export function askNotificationPermission() {
  try {
    if (typeof Notification === "undefined" || Notification.permission !== "default" || localStorage.getItem(PERM_KEY)) return;
    localStorage.setItem(PERM_KEY, "1");  // ask once
    Notification.requestPermission().catch(() => {});
  } catch { /* storage or API unavailable */ }
}

function desktop(title: string, body: string) {
  try {
    if (typeof Notification !== "undefined" && Notification.permission === "granted" && document.visibilityState !== "visible") {
      new Notification(title, { body, icon: "/favicon.svg", tag: title + body });
    }
  } catch { /* ignore */ }
}

const LEVEL_ICON: Record<string, any> = { success: CheckCircle, warning: Warning, error: XCircle, info: Info };

/** Toasts + desktop notifications for LIVE events of the open run (not for replayed history). */
export function useLiveNotifications(run: RunState) {
  const notify = useStore((s) => s.notify);
  const since = useRef(Date.now() / 1000 - 2);
  const seenN = useRef(0), seenReq = useRef<string | null>(null), lastStatus = useRef(run.status);
  const runId = useStore((s) => s.runId);
  useEffect(() => { since.current = Date.now() / 1000 - 2; seenN.current = 0; }, [runId]);
  useEffect(() => {
    const fresh = run.notifications.slice(seenN.current);
    seenN.current = run.notifications.length;
    for (const n of fresh) {
      if (n.ts < since.current) continue;
      notify(n.message, n.level === "success" ? "ok" : n.level === "error" ? "bad" : "info");
      desktop("Luma Studio", n.message);
    }
  }, [run.notifications, notify]);
  useEffect(() => {
    const r = run.pendingRequest;
    if (r && r.id !== seenReq.current) {
      seenReq.current = r.id;
      if (r.reqKind === "approval") desktop("Waiting for your approval", r.data.title || "");
      else desktop("The director has a question", r.data.question || r.data.title || "");
    }
  }, [run.pendingRequest]);
  useEffect(() => {
    if (lastStatus.current !== run.status && ["completed", "failed"].includes(run.status) && lastStatus.current === "running") {
      desktop(run.status === "completed" ? "Film delivered" : "Run failed", run.summary || "");
    }
    lastStatus.current = run.status;
  }, [run.status, run.summary]);
}

export function NotificationBell({ live }: { live: number }) {
  const [items, setItems] = useState<N[]>([]);
  const [open, setOpen] = useState(false);
  const { projects, selectProject } = useStore();
  const load = useCallback(() => api<N[]>("/api/notifications").then(setItems).catch(() => {}), []);
  useEffect(() => { load(); }, [load, live]);
  const unread = items.filter((n) => !n.read).length;
  return (
    <Popover isOpen={open} onOpenChange={(v) => { setOpen(v); if (v) askNotificationPermission(); }}>
      <Button isIconOnly size="sm" variant="ghost" aria-label={`Notifications${unread ? ` (${unread} unread)` : ""}`} data-testid="bell">
          {unread ? (
            <Badge.Anchor>
              <Bell size={18} />
              <Badge color="danger" size="sm">{unread > 9 ? "9+" : unread}</Badge>
            </Badge.Anchor>
          ) : <Bell size={18} />}
      </Button>
      <Popover.Content placement="bottom end" className="w-[340px] p-0">
        <Popover.Dialog className="p-0">
          <div className="flex items-center justify-between border-b border-separator px-3.5 py-2.5">
            <span className="text-sm font-medium">Notifications</span>
            {unread > 0 && <Button size="sm" variant="ghost" onPress={async () => { await api("/api/notifications/read", { method: "POST", json: {} }); load(); }}>Mark all read</Button>}
          </div>
          <ul className="max-h-[360px] overflow-y-auto">
            {!items.length && <li className="px-3.5 py-6 text-center text-sm text-muted">Nothing yet.</li>}
            {items.map((n) => {
              const I = LEVEL_ICON[n.level] || Info;
              return (
                <li key={n.id}>
                  <button className={cn("flex w-full gap-2.5 px-3.5 py-2.5 text-left hover:bg-surface-secondary", !n.read && "bg-offwhite/60")}
                    onClick={() => { selectProject(n.project_id); setOpen(false); }}>
                    <I size={16} weight="fill" className={cn("mt-0.5 shrink-0", n.level === "success" ? "text-success" : n.level === "error" ? "text-danger" : n.level === "warning" ? "text-warning" : "text-accent")} />
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm">{n.message}</span>
                      <span className="block text-xs text-muted">{projects.find((p) => p.id === n.project_id)?.name} · {new Date(n.created_at * 1000).toLocaleString([], { dateStyle: "short", timeStyle: "short" })}</span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </Popover.Dialog>
      </Popover.Content>
    </Popover>
  );
}
