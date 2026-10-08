"use client";
import { createContext, useContext, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { get } from "./api";
import type { Meta, Run } from "./types";

interface Ctx { runId: string | undefined; setRunId: (id: string) => void; runs: Run[]; meta?: Meta }
const RunCtx = createContext<Ctx>({ runId: undefined, setRunId: () => {}, runs: [] });

export function RunProvider({ children }: { children: React.ReactNode }) {
  const meta = useQuery({ queryKey: ["meta"], queryFn: () => get<Meta>("/meta"), refetchInterval: 120_000 });
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => get<Run[]>("/runs") });
  const [picked, setPicked] = useState<string>();
  // follow the newest run: first load, and again whenever a new CURRENT run appears (e.g. a forecast job finished)
  const latest = meta.data?.current_run_id ?? undefined;
  const seen = useRef<string>();
  useEffect(() => { if (latest && latest !== seen.current) { seen.current = latest; setPicked(latest); } }, [latest]);
  return <RunCtx.Provider value={{ runId: picked ?? meta.data?.current_run_id ?? undefined, setRunId: setPicked, runs: runs.data ?? [], meta: meta.data }}>{children}</RunCtx.Provider>;
}
export const useRun = () => useContext(RunCtx);
