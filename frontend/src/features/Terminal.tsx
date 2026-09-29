import { FitAddon } from "@xterm/addon-fit";
import { Terminal as XTerm } from "@xterm/xterm";
import { Switch } from "@heroui/react";
import { useEffect, useRef, useState } from "react";
import { useStore } from "../lib/store";
import { cn } from "../ui/kit";

// Night (#071738) surface from the brand palette, with sky/blue accents.
const THEME = {
  background: "#071738", foreground: "#dbe5f7", cursor: "#5daeff", cursorAccent: "#071738", selectionBackground: "#5daeff40",
  black: "#0d2150", brightBlack: "#5a6d93", blue: "#5daeff", brightBlue: "#8cc6ff", cyan: "#6fd3e8", brightCyan: "#9fe4f2",
  green: "#5fd49a", brightGreen: "#8ae6b7", yellow: "#f2c86b", brightYellow: "#f7da97", red: "#ff7a85", brightRed: "#ff9fa7",
  magenta: "#b99bff", brightMagenta: "#d0bcff", white: "#dbe5f7", brightWhite: "#ffffff",
};

export function TerminalPanel({ visible }: { visible: boolean }) {
  const projectId = useStore((s) => s.projectId);
  const host = useRef<HTMLDivElement>(null);
  const term = useRef<XTerm | null>(null);
  const fit = useRef<FitAddon | null>(null);
  const ws = useRef<WebSocket | null>(null);
  const [takeover, setTakeover] = useState(false);
  const [status, setStatus] = useState<{ alive?: boolean; busy?: boolean }>({});
  const [connected, setConnected] = useState(false);
  const takeoverRef = useRef(false);
  takeoverRef.current = takeover;

  useEffect(() => {
    if (!host.current) return;
    const t = new XTerm({
      fontFamily: '"Geist Mono Variable", ui-monospace, monospace', fontSize: 12.5, lineHeight: 1.3, cursorBlink: true,
      scrollback: 5000, theme: THEME,
    });
    const f = new FitAddon();
    t.loadAddon(f);
    t.open(host.current);
    term.current = t;
    fit.current = f;
    t.onData((d) => { if (takeoverRef.current && ws.current?.readyState === 1) ws.current.send(JSON.stringify({ type: "input", data: d })); });
    const ro = new ResizeObserver(() => {
      try {
        f.fit();
        if (ws.current?.readyState === 1) ws.current.send(JSON.stringify({ type: "resize", cols: t.cols, rows: t.rows }));
      } catch { /* hidden */ }
    });
    ro.observe(host.current);
    return () => { ro.disconnect(); t.dispose(); };
  }, []);

  useEffect(() => {
    if (!projectId || !term.current) return;
    let closed = false;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const sock = new WebSocket(`${proto}://${location.host}/ws/terminal/${projectId}`);
      ws.current = sock;
      term.current!.reset();
      sock.onopen = () => {
        setConnected(true);
        try { fit.current?.fit(); } catch { /* */ }
        sock.send(JSON.stringify({ type: "resize", cols: term.current!.cols, rows: term.current!.rows }));
        sock.send(JSON.stringify({ type: "takeover", on: takeoverRef.current }));
      };
      sock.onmessage = (e) => {
        const m = JSON.parse(e.data);
        if (m.type === "output") term.current!.write(m.data);
        else if (m.type === "status") { setStatus(m); setTakeover(!!m.takeover); }
      };
      sock.onclose = (e) => {
        setConnected(false);
        if (!closed && e.code !== 4401 && e.code !== 4404) retry = setTimeout(connect, 1500);
      };
    };
    connect();
    return () => { closed = true; clearTimeout(retry); ws.current?.close(); };
  }, [projectId]);

  useEffect(() => { if (visible) setTimeout(() => { try { fit.current?.fit(); } catch { /* */ } }, 30); }, [visible]);

  const toggle = (on: boolean) => {
    setTakeover(on);
    ws.current?.send(JSON.stringify({ type: "takeover", on }));
    if (on) term.current?.focus();
  };

  return (
    <div className="flex h-full min-h-0 flex-col p-3">
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl bg-night ring-1 ring-border">
        <div className="flex items-center justify-between gap-3 border-b border-white/[0.06] px-4 py-2.5">
          <span className="flex items-center gap-2 text-xs text-white/60">
            <span className={cn("size-1.5 rounded-full", connected ? (status.busy ? "animate-breathe bg-brand-sky" : "bg-[#5fd49a]") : "bg-[#ff7a85]")} />
            {connected ? (status.busy ? "Director is running a command" : "Live sandbox shell") : "Connecting…"}
          </span>
          <Switch isSelected={takeover} onChange={toggle} size="sm" aria-label="Take over the terminal">
            <Switch.Content className="text-xs text-white/70">
              Take over
              <Switch.Control><Switch.Thumb /></Switch.Control>
            </Switch.Content>
          </Switch>
        </div>
        <div ref={host} className="min-h-0 flex-1" onClick={() => takeover && term.current?.focus()} data-testid="terminal" />
        {takeover && <div className="border-t border-white/[0.06] px-4 py-1.5 text-xs text-brand-sky">You're typing into the sandbox. The director's next command still runs here.</div>}
      </div>
    </div>
  );
}
