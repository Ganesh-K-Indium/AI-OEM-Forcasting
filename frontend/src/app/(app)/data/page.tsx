"use client";
import Link from "next/link";
import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Download, FileUp, Info, OctagonAlert, Trash2 } from "lucide-react";
import { download, get, post, upload } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useWorkspace } from "@/lib/workspace-context";
import type { Capabilities, Job } from "@/lib/types";
import { Badge, Button, Card, CardHeader, ErrorBox, Input, Label, PageHeader, Select, Spinner, Table, Td, Th } from "@/components/ui";
import { cn, fmtNum } from "@/lib/utils";

interface Status {
  workspace: { id: string; slug: string; name: string; kind: "synthetic" | "m5" | "custom"; status: string };
  capabilities: Capabilities;
  presets: { synthetic: { available: boolean }; m5: { available: boolean; missing: string[]; folder: string } };
  last_job: { id: string; type: string; state: string; progress: number; message: string | null; error: string | null; result: any } | null;
  fields: Record<string, { required: string[]; optional: string[] }>;
}
interface Uploaded { file: string; name: string; role: string; size: number; columns: string[]; preview: Record<string, string>[]; suggested: Record<string, string | null>; fields: { required: string[]; optional: string[] } }
interface Report { ok: boolean; issues: { level: "error" | "warn" | "info"; message: string }[]; summary: Record<string, any>; preview: Record<string, string>[] }

const FIELD_HELP: Record<string, string> = {
  month: "Date or month of the sale (daily/weekly rows are summed into months)", customer: "Sold-to / account / store name or id", product: "SKU, product line or department",
  units: "Quantity sold", revenue: "Sales value (or map a price column instead)", price: "Unit price — revenue = units × price", region: "Territory / state / country (optional — one region if omitted)",
  oem: "Parent OEM / brand if the customer is a distributor (optional — customer is the OEM if omitted)", family: "Product category (optional)",
  allocation_pct: "Share of the customer that belongs to this OEM (0–1, optional)", snapshot_month: "Month the backlog was captured", delivery_month: "Month the open order is due", value: "Order value", capacity_units: "Units the factory can supply",
};
const ROLES = [
  { id: "mapping", title: "Customer → OEM mapping", text: "Declare which OEM each distributor or sold-to really serves (with optional splits).", tpl: "mapping" },
  { id: "backlog", title: "Backlog snapshots", text: "Open orders by delivery month → coverage ratio and revenue-gap alerts.", tpl: "backlog" },
  { id: "capacity", title: "Capacity allocation", text: "Factory capacity by region, product and month → supply-bottleneck alerts.", tpl: "capacity" },
] as const;

const levelIcon = { error: <OctagonAlert size={14} className="text-crit" />, warn: <AlertTriangle size={14} className="text-warn" />, info: <Info size={14} className="text-brand" /> };

/** Polls a job and renders a progress bar; calls onDone once when it succeeds. */
function JobProgress({ jobId, onDone }: { jobId: string; onDone?: () => void }) {
  const done = useRef(false);
  const q = useQuery({
    queryKey: ["job", jobId], queryFn: () => get<Job>(`/admin/jobs/${jobId}`),
    refetchInterval: (query) => { const s = (query.state.data as Job | undefined)?.state; return s === "SUCCESS" || s === "FAILED" ? false : 20_000; }, // live via SSE; slow poll as a fallback
  });
  const j = q.data;
  if (j?.state === "SUCCESS" && !done.current) { done.current = true; onDone?.(); }
  if (!j) return <Spinner />;
  return (
    <div className="space-y-2" aria-live="polite">
      <div className="flex items-center justify-between text-sm"><span>{j.message ?? "Queued…"}</span><Badge tone={j.state === "SUCCESS" ? "good" : j.state === "FAILED" ? "crit" : "info"}>{j.state}</Badge></div>
      <div className="h-2 rounded bg-line" role="progressbar" aria-valuenow={Math.round(j.progress * 100)} aria-valuemin={0} aria-valuemax={100}><div className={cn("h-2 rounded transition-all", j.state === "FAILED" ? "bg-crit" : "bg-brand")} style={{ width: `${Math.max(j.progress, 0.02) * 100}%` }} /></div>
      {j.state === "FAILED" && <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded bg-crit/10 p-2 text-xs">{j.error}</pre>}
      {j.state === "SUCCESS" && <p className="flex items-center gap-1.5 text-sm"><CheckCircle2 size={15} className="text-good" />Done. <Link className="text-brand underline" href="/dashboard">Open the dashboard</Link></p>}
    </div>
  );
}

function CapabilityStrip({ c }: { c: Capabilities }) {
  const rows: [string, number, boolean, string][] = [
    ["Sales history", c.sales, c.has_sales, "required for everything"], ["OEM mapping", c.accounts, c.accounts > 0, "who buys what"], ["CRM pipeline", c.opportunity_snapshots, c.has_crm, "commercial uplift, pipeline risk"],
    ["Backlog", c.backlog, c.has_backlog, "coverage ratio, revenue-gap alerts"], ["Capacity", c.capacity, c.has_capacity, "supply-bottleneck alerts"], ["Contracts", c.contracts, c.has_contracts, "contract-based ASP"],
  ];
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {rows.map(([l, n, on, why]) => (
        <div key={l} className={cn("rounded-md border p-3", on ? "bg-good/10" : "bg-line/30")}>
          <div className="flex items-center justify-between text-sm font-medium">{l}<Badge tone={on ? "good" : "neutral"}>{on ? `${fmtNum(n, 0)} rows` : "not provided"}</Badge></div>
          <div className="mt-0.5 text-xs text-ink2">{on ? "Enables: " : "Unlocks: "}{why}</div>
        </div>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ synthetic
function SyntheticPanel({ hasData }: { hasData: boolean }) {
  const [mode, setMode] = useState<"fast" | "full">("fast"); const [jobId, setJobId] = useState<string>(); const qc = useQueryClient(); const { refresh } = useWorkspace();
  const m = useMutation({
    mutationFn: () => post<Job>("/admin/jobs", { job_type: "seed_demo", params: { fast: mode === "fast", replay_cycles: mode === "fast" ? 2 : 6 } }),
    onSuccess: (j) => { setJobId(j.id); refresh(); },
  });
  const go = () => { if (!hasData || window.confirm("This replaces ALL data in this workspace (forecasts, overrides, audit log). Continue?")) m.mutate(); };
  return (
    <Card>
      <CardHeader title="Generate synthetic data" sub="A complete fictional estate: ERP sales, distributors, CRM pipeline, backlog, capacity — with known ground truth." />
      <div className="space-y-4 p-4">
        <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Size">
          {([["fast", "Quick (about 2–3 min)", "Fast model set, 2 replay cycles. Enough to click through everything."], ["full", "Full (20+ min)", "Whole model zoo incl. Chronos-2 when installed, 6 replay cycles for richer FVA."]] as const).map(([id, t, d]) => (
            <button key={id} type="button" role="radio" aria-checked={mode === id} onClick={() => setMode(id)} className={cn("rounded-md border p-3 text-left hover:bg-line/40", mode === id && "border-brand bg-brand/10")}>
              <div className="text-sm font-medium">{t}</div><div className="text-xs text-ink2">{d}</div></button>
          ))}
        </div>
        {m.error && <ErrorBox error={m.error} />}
        <Button onClick={go} disabled={m.isPending || !!jobId}>{hasData ? "Regenerate (replaces data)" : "Generate data"}</Button>
        {jobId && <JobProgress jobId={jobId} onDone={() => { refresh(); qc.invalidateQueries({ queryKey: ["data-status"] }); }} />}
      </div>
    </Card>
  );
}

// ------------------------------------------------------------------------------------------------ M5
function M5Panel({ st }: { st: Status }) {
  const [limit, setLimit] = useState(""); const [level, setLevel] = useState("dept_id"); const [jobId, setJobId] = useState<string>(); const { refresh } = useWorkspace(); const qc = useQueryClient();
  const p = st.presets.m5; const ref = useRef<HTMLInputElement>(null); const [over, setOver] = useState(false); const [log, setLog] = useState<string[]>([]);
  const need = [["sales", "sales_train_evaluation.csv"], ["calendar", "calendar.csv"], ["prices", "sell_prices.csv"]] as const;
  const up = useMutation({
    mutationFn: async (files: File[]) => { for (const f of files) { const r = await upload<{ stored: string[] }>("/data/m5/upload", f); setLog((l) => [...l, `${f.name} → ${r.stored.join(", ")}`]); } },
    onSettled: () => qc.invalidateQueries({ queryKey: ["data-status"] }),
  });
  const dl = useMutation({
    mutationFn: (url: string) => post<{ stored: string[] }>("/data/m5/fetch", { url }),
    onSuccess: (r) => setLog((l) => [...l, `link → ${r.stored.join(", ")}`]),
    onSettled: () => qc.invalidateQueries({ queryKey: ["data-status"] }),
  });
  const m = useMutation({
    mutationFn: () => post<{ job_id: string }>("/data/import", { source: "m5", options: { limit_items: limit ? Number(limit) : null, m5_product_level: level, forecast_mode: "fast" } }),
    onSuccess: (r) => { setJobId(r.job_id); refresh(); },
  });
  return (
    <Card>
      <CardHeader title="M5 benchmark (Walmart)" sub="Public retail data, daily 2011–2016. Mapped as store → OEM, state → region, department → product; revenue = units × weekly sell price." />
      <div className="space-y-4 p-4 text-sm">
        <div>
          <h4 className="mb-1 font-semibold">1. Get the files</h4>
          <p className="text-xs text-ink2">On Kaggle open <b>“M5 Forecasting – Accuracy”</b> → Data → <b>Download all</b> (accept the competition rules once). You get <code>m5-forecasting-accuracy.zip</code> (~50 MB, a few hundred MB unzipped).</p>
        </div>
        <div>
          <h4 className="mb-1 font-semibold">2. Upload them here</h4>
          <div onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
            onDrop={(e) => { e.preventDefault(); setOver(false); const f = Array.from(e.dataTransfer.files); if (f.length) up.mutate(f); }}
            onClick={() => ref.current?.click()} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && ref.current?.click()} aria-label="Upload M5 files"
            className={cn("flex cursor-pointer flex-col items-center gap-1 rounded-md border-2 border-dashed p-6 text-center hover:bg-line/30", over && "border-brand bg-brand/10")}>
            <FileUp size={22} className="text-brand" />
            <span className="font-medium">{up.isPending ? "Uploading… (large files take a minute)" : "Drop the zip here — or the 3 CSV files"}</span>
            <span className="text-xs text-ink2">m5-forecasting-accuracy.zip · or sales_train_evaluation.csv, calendar.csv, sell_prices.csv</span>
            <input ref={ref} type="file" multiple accept=".zip,.csv" hidden onChange={(e) => { const f = Array.from(e.target.files ?? []); if (f.length) up.mutate(f); e.target.value = ""; }} />
          </div>
          <UrlBox pending={dl.isPending} placeholder="…or paste a public link to the M5 zip (a mirror, S3/GCS/Drive direct link)" onSubmit={(url) => dl.mutate(url)} />
          {(up.error || dl.error) && <p role="alert" className="mt-1 text-xs text-crit">{((up.error || dl.error) as Error).message}</p>}
          <ul className="mt-2 grid gap-1 sm:grid-cols-3" aria-label="M5 files on the server">
            {need.map(([k, f]) => { const ok = !p.missing.includes(k); return <li key={k} className={cn("flex items-center gap-1.5 rounded border px-2 py-1 text-xs", ok ? "bg-good/10" : "bg-line/30")}>{ok ? <CheckCircle2 size={13} className="text-good" /> : <span className="h-3 w-3 rounded-full border" />}{f}</li>; })}
          </ul>
          {log.length > 0 && <p className="mt-1 text-[11px] text-ink2">{log.join(" · ")}</p>}
        </div>
        <div>
          <h4 className="mb-1 font-semibold">3. Load and forecast</h4>
          <div className="grid gap-3 sm:grid-cols-2">
            <div><Label>Product level</Label><Select className="w-full" value={level} onChange={(e) => setLevel(e.target.value)}><option value="dept_id">Department (7 products — recommended)</option><option value="cat_id">Category (3 products)</option></Select></div>
            <div><Label>Only the first N items (quick trial, optional)</Label><Input inputMode="numeric" placeholder="all 3,049 items" value={limit} onChange={(e) => setLimit(e.target.value.replace(/\D/g, ""))} /></div>
          </div>
          <p className="my-2 text-xs text-ink2">Not available for M5: distributor mapping, CRM uplift, backlog coverage, supply risk (the data has none of them) — those pages explain what is missing.</p>
          {m.error && <ErrorBox error={m.error} />}
          <Button disabled={!p.available || m.isPending || !!jobId} onClick={() => m.mutate()}>{p.available ? "Load M5 and forecast" : "Upload the files first"}</Button>
          {jobId && <div className="mt-3"><JobProgress jobId={jobId} onDone={() => { refresh(); qc.invalidateQueries({ queryKey: ["data-status"] }); }} /></div>}
        </div>
      </div>
    </Card>
  );
}

// ------------------------------------------------------------------------------------------------ custom import wizard
function UrlBox({ onSubmit, pending, placeholder }: { onSubmit: (url: string) => void; pending: boolean; placeholder: string }) {
  const [url, setUrl] = useState("");
  return (
    <form className="mt-2 flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); if (url.trim()) onSubmit(url.trim()); }}>
      <Input type="url" aria-label="File URL" placeholder={placeholder} value={url} onChange={(e) => setUrl(e.target.value)} />
      <Button type="submit" variant="outline" disabled={pending || !url.trim()}>{pending ? "Downloading…" : "Fetch"}</Button>
    </form>
  );
}

function FileDrop({ role, label, onUploaded }: { role: string; label: string; onUploaded: (u: Uploaded) => void }) {
  const ref = useRef<HTMLInputElement>(null); const [over, setOver] = useState(false);
  const m = useMutation({ mutationFn: (f: File) => upload<Uploaded>("/data/upload", f, { role }), onSuccess: onUploaded });
  const u = useMutation({ mutationFn: (url: string) => post<Uploaded>("/data/fetch", { url, role }), onSuccess: onUploaded });
  const err = m.error || u.error;
  return (
    <div>
      <div onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files?.[0]; if (f) m.mutate(f); }}
        className={cn("flex cursor-pointer flex-col items-center gap-1 rounded-md border-2 border-dashed p-6 text-center text-sm hover:bg-line/30", over && "border-brand bg-brand/10")}
        onClick={() => ref.current?.click()} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && ref.current?.click()} aria-label={label}>
        <FileUp size={22} className="text-brand" /><span className="font-medium">{m.isPending ? "Uploading…" : label}</span><span className="text-xs text-ink2">CSV, Parquet or a .zip containing one · drag and drop or click</span>
        <input ref={ref} type="file" accept=".csv,.tsv,.txt,.parquet,.zip" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) m.mutate(f); e.target.value = ""; }} />
      </div>
      <UrlBox pending={u.isPending} placeholder="…or paste a public link (https://…/sales.csv or .zip)" onSubmit={(url) => u.mutate(url)} />
      {err && <p role="alert" className="mt-1 text-xs text-crit">{(err as Error).message}</p>}
    </div>
  );
}

function MapFields({ up, map, setMap }: { up: Uploaded; map: Record<string, string | null>; setMap: (m: Record<string, string | null>) => void }) {
  const fields = [...up.fields.required, ...up.fields.optional];
  return (
    <Table>
      <thead><tr><Th>Platform field</Th><Th>Your column</Th><Th>Example value</Th></tr></thead>
      <tbody>{fields.map((f) => {
        const req = up.fields.required.includes(f); const col = map[f];
        return (
          <tr key={f}>
            <Td><div className="font-medium">{f}{req && <span className="text-crit" aria-label="required"> *</span>}</div><div className="max-w-xs text-[11px] text-ink2">{FIELD_HELP[f]}</div></Td>
            <Td><Select aria-label={`Column for ${f}`} className={cn("w-56", req && !col && "border-crit")} value={col ?? ""} onChange={(e) => setMap({ ...map, [f]: e.target.value || null })}>
              <option value="">{req ? "— choose —" : "— none —"}</option>{up.columns.map((c) => <option key={c} value={c}>{c}</option>)}</Select></Td>
            <Td className="max-w-[200px] truncate text-xs text-ink2">{col ? up.preview[0]?.[col] : ""}</Td>
          </tr>
        );
      })}</tbody>
    </Table>
  );
}

function Wizard({ st }: { st: Status }) {
  const { refresh } = useWorkspace(); const qc = useQueryClient();
  const [step, setStep] = useState(1);
  const [sales, setSales] = useState<Uploaded>(); const [salesMap, setSalesMap] = useState<Record<string, string | null>>({});
  const [extra, setExtra] = useState<Record<string, { up: Uploaded; map: Record<string, string | null> }>>({});
  const [opts, setOpts] = useState({ default_region: "Global", constant_price: "", forecast_mode: "fast", run_forecast: true });
  const [report, setReport] = useState<Report>(); const [jobId, setJobId] = useState<string>();
  const hasData = st.capabilities.has_sales;
  const body = () => ({
    sales: { file: sales!.file, map: salesMap },
    ...Object.fromEntries(Object.entries(extra).map(([r, v]) => [r, { file: v.up.file, map: v.map }])),
    options: { default_region: opts.default_region || "Global", constant_price: opts.constant_price ? Number(opts.constant_price) : null, forecast_mode: opts.forecast_mode, run_forecast: opts.run_forecast },
  });
  const validate = useMutation({ mutationFn: () => post<Report>("/data/validate", body()), onSuccess: (r) => { setReport(r); setStep(3); } });
  const start = useMutation({
    mutationFn: () => post<{ job_id: string }>("/data/import", body()),
    onSuccess: (r) => { setJobId(r.job_id); setStep(4); refresh(); },
  });
  const requiredOk = !!sales && up_required(sales).every((f) => salesMap[f]) && (!!salesMap.revenue || !!salesMap.price || !!opts.constant_price);
  const stepper = ["Upload", "Map columns", "Check", "Import"];
  return (
    <Card>
      <CardHeader title="Import your own data" sub="Only a sales history is required. Everything else unlocks extra features."
        right={<div className="flex gap-3 text-xs">{["sales", "mapping", "backlog", "capacity"].map((r) => <button key={r} className="flex items-center gap-1 text-brand hover:underline" onClick={() => download(`/data/templates/${r}`, `${r}_template.csv`)}><Download size={12} />{r} template</button>)}</div>} />
      <ol className="flex gap-2 border-b px-4 py-2 text-xs" aria-label="Steps">{stepper.map((s, i) => <li key={s} className={cn("rounded px-2 py-1", step === i + 1 ? "bg-brand/15 font-semibold" : step > i + 1 ? "text-good" : "text-ink2")}>{i + 1}. {s}</li>)}</ol>
      <div className="space-y-4 p-4">
        {step === 1 && (
          <>
            {hasData && <p className="rounded-md border border-warn/50 bg-warn/15 p-3 text-sm">This workspace already holds data. Importing <b>replaces</b> it (forecasts, overrides and audit log included).</p>}
            {!sales ? <FileDrop role="sales" label="Drop your sales history file" onUploaded={(u) => { setSales(u); setSalesMap(u.suggested); }} /> : (
              <div className="rounded-md border bg-good/10 p-3 text-sm"><b>{sales.name}</b> · {fmtNum(sales.size / 1024, 0)} KB · {sales.columns.length} columns <Button size="sm" variant="ghost" onClick={() => setSales(undefined)}>Replace</Button></div>
            )}
            {sales && <>
              <h4 className="pt-2 text-sm font-semibold">Optional files — each one switches on more of the platform</h4>
              <div className="grid gap-3 md:grid-cols-3">
                {ROLES.map((r) => (
                  <div key={r.id} className="rounded-md border p-3"><div className="text-sm font-medium">{r.title}</div><p className="mb-2 text-xs text-ink2">{r.text}</p>
                    {extra[r.id] ? <p className="text-xs"><CheckCircle2 size={12} className="mr-1 inline text-good" />{extra[r.id].up.name} <button className="text-brand underline" onClick={() => setExtra(({ [r.id]: _, ...rest }) => rest)}>remove</button></p>
                      : <FileDrop role={r.id} label="Add file" onUploaded={(u) => setExtra({ ...extra, [r.id]: { up: u, map: u.suggested } })} />}
                  </div>
                ))}
              </div>
              <div className="flex justify-end"><Button onClick={() => setStep(2)}>Next: map columns</Button></div>
            </>}
          </>
        )}
        {step === 2 && sales && (
          <>
            <p className="text-sm text-ink2">We matched your headers automatically — check each line and fix anything that is off.</p>
            <MapFields up={sales} map={salesMap} setMap={setSalesMap} />
            {Object.entries(extra).map(([r, v]) => <div key={r}><h4 className="mb-1 mt-3 text-sm font-semibold">{ROLES.find((x) => x.id === r)?.title} — {v.up.name}</h4><MapFields up={v.up} map={v.map} setMap={(m) => setExtra({ ...extra, [r]: { ...v, map: m } })} /></div>)}
            <div className="grid gap-3 rounded-md border p-3 sm:grid-cols-2 lg:grid-cols-4">
              <div><Label>Region name if no region column</Label><Input value={opts.default_region} onChange={(e) => setOpts({ ...opts, default_region: e.target.value })} /></div>
              <div><Label>Constant price (only if no revenue/price)</Label><Input inputMode="decimal" value={opts.constant_price} onChange={(e) => setOpts({ ...opts, constant_price: e.target.value.replace(/[^\d.]/g, "") })} /></div>
              <div><Label>First forecast</Label><Select className="w-full" value={opts.run_forecast ? opts.forecast_mode : "none"} onChange={(e) => e.target.value === "none" ? setOpts({ ...opts, run_forecast: false }) : setOpts({ ...opts, run_forecast: true, forecast_mode: e.target.value })}><option value="fast">Quick (fast model set)</option><option value="full">Full (all models, slower)</option><option value="none">Don’t forecast yet</option></Select></div>
            </div>
            {validate.error && <ErrorBox error={validate.error} />}
            <div className="flex justify-between"><Button variant="outline" onClick={() => setStep(1)}>Back</Button><Button disabled={!requiredOk || validate.isPending} onClick={() => validate.mutate()}>{validate.isPending ? "Checking…" : "Check my data"}</Button></div>
          </>
        )}
        {step === 3 && report && (
          <>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
              {([["Rows", report.summary.rows], ["Months", report.summary.months], ["Customers", report.summary.customers], ["OEMs", report.summary.oems], ["Regions", report.summary.regions], ["Products", report.summary.products], ["Series", report.summary.series], ["Revenue", report.summary.revenue_total]] as [string, number][]).map(([l, v]) => (
                <div key={l} className="rounded-md border p-2"><div className="text-[11px] text-ink2">{l}</div><div className="text-lg font-semibold tabular-nums">{v == null ? "—" : l === "Revenue" ? Intl.NumberFormat(undefined, { notation: "compact" }).format(v) : fmtNum(v, 0)}</div></div>
              ))}
            </div>
            {report.summary.first_month && <p className="text-sm text-ink2">History {report.summary.first_month.slice(0, 7)} → {report.summary.last_month.slice(0, 7)}.</p>}
            <ul className="space-y-1.5" aria-label="Findings">{report.issues.map((i, k) => <li key={k} className="flex items-start gap-2 text-sm">{levelIcon[i.level]}<span>{i.message}</span></li>)}{report.issues.length === 0 && <li className="flex items-center gap-2 text-sm"><CheckCircle2 size={14} className="text-good" />No problems found.</li>}</ul>
            {report.preview.length > 0 && <details><summary className="cursor-pointer text-sm text-ink2">Preview of the cleaned rows</summary><Table><thead><tr>{Object.keys(report.preview[0]).map((k) => <Th key={k}>{k}</Th>)}</tr></thead><tbody>{report.preview.map((r, i) => <tr key={i}>{Object.values(r).map((v, j) => <Td key={j} className="text-xs">{String(v).slice(0, 24)}</Td>)}</tr>)}</tbody></Table></details>}
            {start.error && <ErrorBox error={start.error} />}
            <div className="flex justify-between"><Button variant="outline" onClick={() => setStep(2)}>Back to mapping</Button>
              <Button disabled={!report.ok || start.isPending} onClick={() => start.mutate()}>{report.ok ? (hasData ? "Replace data and import" : "Import") : "Fix the errors first"}</Button></div>
          </>
        )}
        {step === 4 && jobId && <JobProgress jobId={jobId} onDone={() => { refresh(); qc.invalidateQueries({ queryKey: ["data-status"] }); }} />}
      </div>
    </Card>
  );
}
const up_required = (u: Uploaded) => u.fields.required;

export default function DataPage() {
  const { can } = useAuth(); const { refresh } = useWorkspace(); const qc = useQueryClient();
  const q = useQuery({ queryKey: ["data-status"], queryFn: () => get<Status>("/data/status"), refetchInterval: false });
  const clear = useMutation({ mutationFn: () => post("/data/clear"), onSuccess: () => { refresh(); qc.invalidateQueries({ queryKey: ["data-status"] }); } });
  if (q.isLoading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  const st = q.data!; const w = st.workspace; const c = st.capabilities;
  const admin = can("admin");
  return (
    <div className="space-y-4">
      <PageHeader title="Data" sub={<>Workspace <b>{w.name}</b> <Badge>{w.kind}</Badge> <Badge tone={w.status === "READY" ? "good" : w.status === "FAILED" ? "crit" : "info"}>{w.status}</Badge>{c.first_month && <> · history {c.first_month.slice(0, 7)} → {c.last_month?.slice(0, 7)}</>}</>}
        right={admin && w.kind !== "synthetic" && c.has_sales && <Button variant="outline" onClick={() => window.confirm("Remove all data, forecasts and overrides from this workspace?") && clear.mutate()}><Trash2 size={14} />Clear workspace</Button>} />
      <Card><CardHeader title="What this workspace contains" sub="Pages and engines switch features on or off based on what is here." /><div className="p-4"><CapabilityStrip c={c} /></div></Card>
      {!admin ? <Card className="p-4 text-sm text-ink2">Only administrators can load or replace data. Ask an admin, or switch to a workspace that already has data.</Card>
        : w.kind === "synthetic" ? <SyntheticPanel hasData={c.has_sales} /> : w.kind === "m5" ? <M5Panel st={st} /> : <Wizard st={st} />}
    </div>
  );
}
