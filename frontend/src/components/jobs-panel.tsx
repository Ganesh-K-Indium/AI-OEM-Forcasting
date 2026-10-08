"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ChevronDown, ChevronRight, CircleAlert, Clock, Loader2, Play, RotateCcw } from "lucide-react";
import { get, post } from "@/lib/api";
import { useWorkspace } from "@/lib/workspace-context";
import type { Capabilities, Job } from "@/lib/types";
import { Badge, Button, Card, CardHeader, Empty, ErrorBox, Label, Select, Spinner } from "@/components/ui";
import { cn, fmtTs, parseTs } from "@/lib/utils";

type Opt = { key: string; label: string; kind: "select" | "number" | "check"; choices?: { v: string; l: string }[]; def: string | number | boolean; help?: string };
interface Action {
  type: string; group: "Prepare data" | "Forecast" | "Insight & quality"; title: string; what: string; when: string; takes: string; icon?: string;
  needs?: (c: Partial<Capabilities>) => string | null; only?: "synthetic"; exclusive?: boolean; options?: Opt[];
  params?: (o: Record<string, any>) => Record<string, any>;
}
const noSales = (c: Partial<Capabilities>) => (c.has_sales ? null : "Load sales data first (Data page).");
const ACTIONS: Action[] = [
  { type: "seed_demo", group: "Prepare data", title: "Generate demo data", only: "synthetic", exclusive: true, takes: "3–15 min",
    what: "Replaces everything in this workspace with a generated ERP, CRM, backlog and capacity estate, maps it, and builds a first forecast.",
    when: "Fresh install, or to reset a demo workspace.",
    options: [{ key: "fast", label: "Mode", kind: "select", choices: [{ v: "1", l: "Fast (smaller, quicker)" }, { v: "0", l: "Full (all history and cycles)" }], def: "1" }],
    params: (o) => ({ fast: o.fast === "1", replay_cycles: o.fast === "1" ? 2 : 6 }) },
  { type: "mapping_pipeline", group: "Prepare data", title: "Run mapping", needs: noSales, takes: "under 1 min",
    what: "Re-runs the Sold-To → OEM resolution (rules, then fuzzy match) for accounts without a mapping, queues uncertain matches for steward review, then rebuilds the history.",
    when: "After new accounts arrived, or rules / OEM aliases changed." },
  { type: "materialize", group: "Prepare data", title: "Rebuild history", needs: noSales, takes: "under 1 min",
    what: "Recomputes the OEM × region × product × month history the models train on from the sales rows and the current approved mappings.",
    when: "After a steward approved or edited mappings. Run a forecast afterwards to use the new history." },
  { type: "run_dq", group: "Insight & quality", title: "Check data quality", needs: noSales, takes: "seconds",
    what: "Runs the data-quality gate: negative values, missing FX, price outliers, stale CRM/backlog snapshots, capacity coverage, unmapped share. Results appear under “Data quality & drift”.",
    when: "Before a forecast, or to see why one was blocked." },
  { type: "run_forecast", group: "Forecast", title: "Run forecast", exclusive: true, needs: (c) => (c.has_mapped ? null : "No mapped history yet: load data or run mapping first."), takes: "fast: 2–5 min · full: 10+ min",
    what: "Backtests the models, picks a champion per series group, forecasts units with P10/P50/P90, adds CRM uplift if present, reconciles the hierarchy, converts to revenue, and raises risk alerts. Creates a new run.",
    when: "After data or mapping changes, or at the start of a planning cycle.",
    options: [{ key: "mode", label: "Mode", kind: "select", choices: [{ v: "fast", l: "Fast (fewer backtests)" }, { v: "full", l: "Full (all models, all origins)" }], def: "fast" }],
    params: (o) => ({ mode: o.mode }) },
  { type: "refresh_risk", group: "Insight & quality", title: "Recompute risk alerts", needs: (c) => (c.has_forecast ? null : "Needs a completed forecast run."), takes: "seconds",
    what: "Recomputes coverage (backlog ÷ consensus), supply bottlenecks and pipeline vulnerability for the latest run, using the current thresholds and overrides.",
    when: "After changing risk thresholds, backlog, capacity or overrides." },
  { type: "compute_fva", group: "Insight & quality", title: "Compute forecast value added", needs: (c) => (c.has_forecast ? null : "Needs a completed forecast run."), takes: "seconds",
    what: "Compares naive, AI and consensus accuracy on locked forecasts that now have actuals, with confidence intervals. Empty until a locked cycle has matured.",
    when: "After new actuals were loaded." },
];
const TITLE: Record<string, string> = { ...Object.fromEntries(ACTIONS.map((a) => [a.type, a.title])), import_dataset: "Import dataset" };
const GROUPS = ["Prepare data", "Forecast", "Insight & quality"] as const;
const ACTIVE = (s: string) => s === "PENDING" || s === "RUNNING";
const tone = (s: string) => (s === "SUCCESS" ? "good" : s === "FAILED" ? "crit" : s === "RUNNING" ? "info" : "neutral") as "good" | "crit" | "info" | "neutral";

function dur(j: Job, now: number) {
  const start = j.started_at ? parseTs(j.started_at).getTime() : null;
  if (!start) return ACTIVE(j.state) ? "queued" : "—";
  const sec = Math.max(0, Math.round(((j.finished_at ? parseTs(j.finished_at).getTime() : now) - start) / 1000));
  return sec < 60 ? `${sec}s` : `${Math.floor(sec / 60)}m ${sec % 60}s`;
}
function summary(j: Job): string {
  const r = j.result ?? {};
  if (j.state === "FAILED") return "Failed: open for details";
  if (j.job_type === "run_forecast" && r.run_id) return `New run ${String(r.run_id).slice(0, 8)} · cycle ${String(r.cycle_month ?? "").slice(0, 7)}`;
  if (j.job_type === "refresh_risk") return `${r.alerts ?? 0} alerts computed`;
  if (j.job_type === "compute_fva") return `${r.rows ?? 0} FVA rows`;
  if (j.job_type === "run_dq") return `${r.checks ?? 0} checks, ${r.failed ?? 0} failed`;
  if (j.job_type === "materialize") return `${typeof r === "number" ? r : r.rows ?? r.n ?? ""} series rows rebuilt`.trim();
  return j.message ?? "";
}

function ActionCard({ a, caps, kind, busyExclusive, onRun, pending }: { a: Action; caps: Partial<Capabilities>; kind?: string; busyExclusive: boolean; onRun: (a: Action, params: Record<string, any>) => void; pending: boolean }) {
  const [o, setO] = useState<Record<string, any>>(Object.fromEntries((a.options ?? []).map((x) => [x.key, x.def])));
  const blocked = a.needs?.(caps) ?? null;
  const wrongKind = a.only && kind !== a.only ? "Only for synthetic workspaces. Use Data → Import for this one." : null;
  const why = wrongKind ?? blocked ?? (a.exclusive && busyExclusive ? "Another heavy job is running in this workspace." : null);
  return (
    <div className={cn("flex flex-col rounded-lg border bg-surface p-4", wrongKind && "opacity-60")}>
      <div className="flex items-start justify-between gap-2">
        <h4 className="text-sm font-semibold">{a.title}</h4>
        <span className="flex shrink-0 items-center gap-1 text-[11px] text-ink2"><Clock size={11} />{a.takes}</span>
      </div>
      <p className="mt-1.5 text-xs text-ink2">{a.what}</p>
      <p className="mt-1.5 text-xs"><b>Use when:</b> <span className="text-ink2">{a.when}</span></p>
      {a.options?.map((x) => (
        <div key={x.key} className="mt-3"><Label>{x.label}</Label>
          <Select className="w-full" value={String(o[x.key])} onChange={(e) => setO({ ...o, [x.key]: e.target.value })}>{x.choices?.map((c) => <option key={c.v} value={c.v}>{c.l}</option>)}</Select></div>
      ))}
      <div className="mt-auto pt-3">
        {why && <p className="mb-2 flex items-start gap-1 text-xs text-warn"><CircleAlert size={12} className="mt-0.5 shrink-0" />{why}</p>}
        <Button className="w-full" disabled={!!why || pending} onClick={() => onRun(a, a.params ? a.params(o) : {})}><Play size={13} />{pending ? "Starting…" : "Run"}</Button>
      </div>
    </div>
  );
}

function JobRow({ j, now, onAgain, canAgain }: { j: Job; now: number; onAgain: (j: Job) => void; canAgain: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <tr className="cursor-pointer hover:bg-line/30" onClick={() => setOpen(!open)}>
        <td className="border-b px-3 py-2">{open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</td>
        <td className="border-b px-3 py-2 font-medium">{TITLE[j.job_type] ?? j.job_type}</td>
        <td className="border-b px-3 py-2"><Badge tone={tone(j.state)}>{j.state}</Badge></td>
        <td className="w-36 border-b px-3 py-2">{ACTIVE(j.state) ? <div className="h-2 w-full rounded bg-line" role="progressbar" aria-valuenow={Math.round(j.progress * 100)} aria-valuemin={0} aria-valuemax={100}><div className="h-2 rounded bg-brand transition-all" style={{ width: `${Math.max(3, j.progress * 100)}%` }} /></div> : <span className="text-xs text-ink2">{Math.round(j.progress * 100)}%</span>}</td>
        <td className="max-w-[320px] truncate border-b px-3 py-2 text-xs text-ink2">{ACTIVE(j.state) ? j.message : summary(j)}</td>
        <td className="whitespace-nowrap border-b px-3 py-2 text-xs tabular-nums">{dur(j, now)}</td>
        <td className="whitespace-nowrap border-b px-3 py-2 text-xs">{fmtTs(j.created_at)}</td>
        <td className="border-b px-3 py-2 text-xs">{j.created_by}</td>
      </tr>
      {open && (
        <tr><td colSpan={8} className="border-b bg-line/20 px-4 py-3 text-xs">
          <div className="grid gap-3 md:grid-cols-2">
            <div><div className="mb-1 font-semibold">Settings it ran with</div><pre className="whitespace-pre-wrap rounded bg-surface p-2">{JSON.stringify(j.params ?? {}, null, 2)}</pre></div>
            <div><div className="mb-1 font-semibold">{j.error ? "Error" : "Result"}</div><pre className={cn("max-h-48 overflow-auto whitespace-pre-wrap rounded bg-surface p-2", j.error && "text-crit")}>{j.error ?? JSON.stringify(j.result ?? {}, null, 2)}</pre></div>
          </div>
          <div className="mt-2 flex items-center gap-3 text-ink2">
            <span>Job {j.id.slice(0, 8)}</span>
            {j.state === "SUCCESS" && j.job_type === "run_forecast" && <Link href="/dashboard" className="text-brand underline">Open dashboard</Link>}
            {j.state === "SUCCESS" && j.job_type === "refresh_risk" && <Link href="/risk" className="text-brand underline">Open Risk Center</Link>}
            {j.state === "SUCCESS" && j.job_type === "run_dq" && <span>See “Data quality &amp; drift” tab</span>}
            {canAgain && !ACTIVE(j.state) && j.job_type !== "import_dataset" && <Button size="sm" variant="outline" onClick={(e) => { e.stopPropagation(); onAgain(j); }}><RotateCcw size={12} />Run again</Button>}
          </div>
        </td></tr>
      )}
    </>
  );
}

export function JobsPanel() {
  const { current } = useWorkspace(); const qc = useQueryClient();
  const caps = (current?.capabilities ?? {}) as Partial<Capabilities>;
  const q = useQuery({ queryKey: ["jobs"], queryFn: () => get<Job[]>("/admin/jobs", { limit: 100 }), refetchInterval: 60_000 });
  const [now, setNow] = useState(() => Date.now());
  const [filter, setFilter] = useState<"all" | "active" | "FAILED" | "SUCCESS">("all");
  const [type, setType] = useState("");
  const [note, setNote] = useState<string>();
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);
  const m = useMutation({
    mutationFn: ({ type, params }: { type: string; params: Record<string, any> }) => post<Job>("/admin/jobs", { job_type: type, params }),
    onSuccess: (j) => { setNote(`${TITLE[j.job_type] ?? j.job_type} started. Progress appears below and updates live.`); qc.invalidateQueries({ queryKey: ["jobs"] }); },
    onError: () => setNote(undefined),
  });
  const jobs = q.data ?? [];
  // live job events arrive over SSE and update the workspace's active_job; mirror them in this list
  const act = current?.active_job;
  useEffect(() => { qc.invalidateQueries({ queryKey: ["jobs"] }); }, [act?.id, act?.state, act?.progress, qc]);
  const running = jobs.filter((j) => ACTIVE(j.state));
  const busyExclusive = running.some((j) => ["seed_demo", "import_dataset", "run_forecast"].includes(j.job_type));
  const shown = jobs.filter((j) => (filter === "all" || (filter === "active" ? ACTIVE(j.state) : j.state === filter)) && (!type || j.job_type === type));
  const run = (a: Action, params: Record<string, any>) => m.mutate({ type: a.type, params });
  const again = (j: Job) => m.mutate({ type: j.job_type, params: j.params ?? {} });
  const failed = jobs.filter((j) => j.state === "FAILED").length;

  return (
    <div className="space-y-4">
      <Card className="p-4 text-sm">
        <div className="font-semibold">What jobs are</div>
        <p className="mt-1 text-ink2">Heavy work runs in the background so the app stays responsive. Pick an action below; it appears in the history with live progress and finishes on its own. Everything here applies to the workspace <b>{current?.name ?? "—"}</b> (change it in the top bar).</p>
        <p className="mt-2 text-xs text-ink2"><b>Typical order:</b> load data (Data page) → Run mapping → Rebuild history → Check data quality → Run forecast → Recompute risk alerts → (later) Compute FVA. Only one of Generate, Import and Run forecast can run at a time per workspace.</p>
      </Card>

      {running.length > 0 && (
        <Card className="border-brand/40 p-4">
          {running.map((j) => (
            <div key={j.id} className="flex items-center gap-3 text-sm">
              <Loader2 size={16} className="animate-spin text-brand" />
              <div className="min-w-0 flex-1">
                <div className="flex justify-between"><b>{TITLE[j.job_type] ?? j.job_type}</b><span className="text-xs tabular-nums text-ink2">{Math.round(j.progress * 100)}% · {dur(j, now)}</span></div>
                <div className="mt-1 h-2 rounded bg-line"><div className="h-2 rounded bg-brand transition-all" style={{ width: `${Math.max(3, j.progress * 100)}%` }} /></div>
                <div className="mt-1 truncate text-xs text-ink2">{j.message}</div>
              </div>
            </div>
          ))}
        </Card>
      )}
      {note && !m.error && <div role="status" className="flex items-center gap-2 rounded border border-good/40 bg-good/10 p-2 text-sm"><CheckCircle2 size={14} className="text-good" />{note}</div>}
      {m.error && <div role="alert" className="rounded border border-crit/40 bg-crit/10 p-2 text-sm">{(m.error as Error).message}</div>}

      {GROUPS.map((g) => {
        const list = ACTIONS.filter((a) => a.group === g);
        return (
          <section key={g}>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink2">{g}</h3>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {list.map((a) => <ActionCard key={a.type} a={a} caps={caps} kind={current?.kind} busyExclusive={busyExclusive} onRun={run} pending={m.isPending && m.variables?.type === a.type} />)}
              {g === "Prepare data" && (
                <div className="flex flex-col rounded-lg border border-dashed bg-surface p-4">
                  <h4 className="text-sm font-semibold">Import your own data</h4>
                  <p className="mt-1.5 text-xs text-ink2">Upload a file, zip or URL, map the columns and import. It has its own wizard with validation.</p>
                  <div className="mt-auto pt-3"><Link href="/data"><Button variant="outline" className="w-full">Go to Data page</Button></Link></div>
                </div>
              )}
            </div>
          </section>
        );
      })}

      <Card>
        <CardHeader title="History" sub={`${jobs.length} most recent jobs in this workspace${failed ? ` · ${failed} failed` : ""}. Click a row for settings, result and errors.`}
          right={<div className="flex items-center gap-2">
            <Select aria-label="State" value={filter} onChange={(e) => setFilter(e.target.value as typeof filter)}><option value="all">All states</option><option value="active">Running / queued</option><option value="SUCCESS">Succeeded</option><option value="FAILED">Failed</option></Select>
            <Select aria-label="Job type" value={type} onChange={(e) => setType(e.target.value)}><option value="">All types</option>{Object.entries(TITLE).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</Select>
          </div>} />
        {q.isLoading ? <Spinner /> : q.error ? <ErrorBox error={q.error} /> : shown.length === 0 ? <Empty>{jobs.length ? "No jobs match these filters." : "No jobs yet. Pick an action above to start one."}</Empty> : (
          <div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr>{["", "Job", "State", "Progress", "Outcome", "Took", "Started", "By"].map((h) => <th key={h} className="whitespace-nowrap border-b px-3 py-2 text-left text-xs font-medium text-ink2">{h}</th>)}</tr></thead>
            <tbody>{shown.map((j) => <JobRow key={j.id} j={j} now={now} onAgain={again} canAgain={!m.isPending} />)}</tbody></table></div>
        )}
      </Card>
    </div>
  );
}
