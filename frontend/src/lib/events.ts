import { getToken } from "./api";

const BASE = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");

export type LiveEvent =
  | { type: "job"; workspace_id: string | null; job_id: string; job_type: string; state: string; progress: number; message: string | null }
  | { type: "data_changed"; schema: string }
  | { type: "workspaces" }
  | { type: "resync" };

/** Server-Sent Events over fetch (so the Authorization header is used, not a URL token). Reconnects automatically. */
export function connectEvents(onEvent: (e: LiveEvent) => void, onState?: (up: boolean) => void): () => void {
  const ctl = new AbortController();
  let stopped = false;
  (async () => {
    let wait = 1000;
    while (!stopped) {
      try {
        const res = await fetch(`${BASE}/api/v1/events`, { headers: { Authorization: `Bearer ${getToken() ?? ""}`, Accept: "text/event-stream" }, signal: ctl.signal });
        if (!res.ok || !res.body) throw new Error(String(res.status));
        onState?.(true); wait = 1000;
        onEvent({ type: "resync" }); // anything missed while disconnected
        const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
        let buf = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += value;
          let i: number;
          while ((i = buf.indexOf("\n\n")) >= 0) {
            const frame = buf.slice(0, i); buf = buf.slice(i + 2);
            const line = frame.split("\n").find((l) => l.startsWith("data: "));
            if (line) { try { onEvent(JSON.parse(line.slice(6))); } catch { /* ignore malformed frame */ } }
          }
        }
      } catch { /* network drop or abort */ }
      onState?.(false);
      if (stopped) return;
      await new Promise((r) => setTimeout(r, wait));
      wait = Math.min(wait * 2, 15000);
    }
  })();
  return () => { stopped = true; ctl.abort(); };
}
