"use client";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { get, setWorkspace } from "./api";
import { connectEvents } from "./events";
import { Spinner } from "@/components/ui";
import type { Workspace } from "./types";

interface Ctx { workspaces: Workspace[]; current?: Workspace; select: (slug: string) => void; refresh: () => void }
const WsCtx = createContext<Ctx>({ workspaces: [], select: () => {}, refresh: () => {} });
const KEY = "oem.workspace";
const read = () => { try { return localStorage.getItem(KEY) ?? undefined; } catch { return undefined; } };
const write = (v: string) => { try { localStorage.setItem(KEY, v); } catch { /* storage blocked */ } };

/** Holds the active workspace. Switching it remounts everything below, so no query result can leak between workspaces. */
export function WorkspaceProvider({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["workspaces"], queryFn: () => get<Workspace[]>("/workspaces"),
    refetchInterval: 60_000, // safety net only; live updates arrive over SSE
  });
  const [slug, setSlug] = useState<string>();
  const slugRef = useRef<string>();
  const schemas = useRef<Record<string, string>>({});
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const debounce = useCallback((key: string, fn: () => void, ms = 400) => { clearTimeout(timers.current[key]); timers.current[key] = setTimeout(fn, ms); }, []);
  // Live updates: the API pushes job progress and "data changed" events; pages refetch the moment something finishes.
  useEffect(() => {
    return connectEvents((e) => {
      const refetchPages = () => debounce("pages", () => qc.invalidateQueries({ predicate: (query) => query.queryKey[0] !== "workspaces" }));
      if (e.type === "resync") { qc.invalidateQueries({ queryKey: ["workspaces"] }); refetchPages(); }
      else if (e.type === "workspaces") debounce("ws", () => qc.invalidateQueries({ queryKey: ["workspaces"] }));
      else if (e.type === "job") {
        qc.setQueryData<Workspace[]>(["workspaces"], (old) => old?.map((w) => w.id !== e.workspace_id ? w
          : { ...w, active_job: e.state === "PENDING" || e.state === "RUNNING" ? { id: e.job_id, type: e.job_type, state: e.state, progress: e.progress, message: e.message } : null }));
        qc.setQueryData(["job", e.job_id], (old: any) => (old ? { ...old, state: e.state, progress: e.progress, message: e.message } : old));
        if (e.state === "SUCCESS" || e.state === "FAILED") { qc.invalidateQueries({ queryKey: ["job", e.job_id] }); qc.invalidateQueries({ queryKey: ["jobs"] }); debounce("ws", () => qc.invalidateQueries({ queryKey: ["workspaces"] })); refetchPages(); }
      } else if (e.type === "data_changed") {
        if (e.schema === schemas.current[slugRef.current ?? ""]) refetchPages();
      }
    });
  }, [qc, debounce]);
  useEffect(() => { setSlug(read()); }, []);
  const workspaces = q.data ?? [];
  const current = workspaces.find((w) => w.slug === slug) ?? workspaces[0];
  slugRef.current = current?.slug;
  schemas.current = Object.fromEntries(workspaces.map((w) => [w.slug, w.schema_name]));
  setWorkspace(current?.slug ?? null); // synchronous: child queries issued in this render already carry the header

  const select = useCallback((s: string) => {
    write(s); setWorkspace(s);
    qc.removeQueries({ predicate: (query) => query.queryKey[0] !== "workspaces" });
    setSlug(s);
  }, [qc]);
  const refresh = useCallback(() => { qc.invalidateQueries({ queryKey: ["workspaces"] }); }, [qc]);

  if (q.isLoading) return <Spinner />;
  return (
    <WsCtx.Provider value={{ workspaces, current, select, refresh }}>
      <div key={current?.slug ?? "none"} className="contents">{children}</div>
    </WsCtx.Provider>
  );
}
export const useWorkspace = () => useContext(WsCtx);
