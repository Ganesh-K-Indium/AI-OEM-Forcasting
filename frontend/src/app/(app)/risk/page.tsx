"use client";
import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, patch, post, put } from "@/lib/api";
import { useRun } from "@/lib/run-context";
import { useAuth } from "@/lib/auth";
import type { Alert, RiskSummary, Threshold } from "@/lib/types";
import { Badge, Button, CapabilityNote, Card, CardHeader, Empty, ErrorBox, Input, Kpi, Label, PageHeader, Select, Sheet, Spinner, Table, Td, Textarea, Th, sevTone } from "@/components/ui";
import { fmtMonth, fmtPct, fmtUsd } from "@/lib/utils";

const TYPES = ["REVENUE_GAP", "SUPPLY_BOTTLENECK", "PIPELINE_VULNERABILITY"];
const typeLabel = (t: string) => t.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());

function AlertSheet({ a, onClose }: { a: Alert | null; onClose: () => void }) {
  const qc = useQueryClient();
  const { can } = useAuth();
  const [owner, setOwner] = useState(""); const [note, setNote] = useState("");
  const m = useMutation({
    mutationFn: (b: { status?: string }) => patch<Alert>(`/risk/alerts/${a!.id}`, { ...b, owner: owner || a!.owner, note: note || a!.note }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["alerts"] }); qc.invalidateQueries({ queryKey: ["risk-summary"] }); qc.invalidateQueries({ queryKey: ["dashboard"] }); onClose(); },
  });
  return (
    <Sheet open={!!a} onOpenChange={(o) => !o && onClose()} title={a?.title ?? ""}>
      {a && (
        <div className="space-y-4 text-sm">
          <div className="flex gap-2"><Badge tone={sevTone(a.severity)}>{a.severity}</Badge><Badge>{typeLabel(a.alert_type)}</Badge><Badge tone={a.status === "OPEN" ? "warn" : a.status === "RESOLVED" ? "good" : "info"}>{a.status}</Badge></div>
          <div>Financial impact <b className="text-lg tabular-nums">{fmtUsd(a.financial_impact_usd)}</b></div>
          <div className="text-ink2">{a.oem} / {a.region} / {a.product} · {fmtMonth(a.first_month)} – {fmtMonth(a.last_month)}</div>
          <Card><CardHeader title="Evidence" />
            <dl className="grid grid-cols-2 gap-2 p-3 text-xs">{Object.entries(a.detail ?? {}).filter(([, v]) => typeof v !== "object").map(([k, v]) => <div key={k}><dt className="text-ink2">{k.replace(/_/g, " ")}</dt><dd className="font-medium">{typeof v === "number" ? (Math.abs(v) > 1000 ? fmtUsd(v) : v.toFixed(3)) : String(v)}</dd></div>)}</dl>
          </Card>
          {can("planner", "sales_rep") && (
            <div className="space-y-2">
              <div><Label>Owner</Label><Input value={owner} onChange={(e) => setOwner(e.target.value)} placeholder={a.owner ?? "email"} /></div>
              <div><Label>Note</Label><Textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} placeholder={a.note ?? "Action taken / mitigation"} /></div>
              {m.error && <div role="alert" className="text-crit">{(m.error as Error).message}</div>}
              <div className="flex gap-2"><Button variant="outline" disabled={m.isPending} onClick={() => m.mutate({ status: "ACKNOWLEDGED" })}>Acknowledge</Button><Button disabled={m.isPending} onClick={() => m.mutate({ status: "RESOLVED" })}>Resolve</Button></div>
            </div>
          )}
        </div>
      )}
    </Sheet>
  );
}

function Thresholds() {
  const qc = useQueryClient();
  const { can } = useAuth();
  const q = useQuery({ queryKey: ["thresholds"], queryFn: () => get<Threshold[]>("/risk/thresholds") });
  const [edit, setEdit] = useState<Record<number, number>>({});
  const m = useMutation({ mutationFn: (t: Threshold) => put<Threshold>("/risk/thresholds", { ...t, min_coverage: edit[t.id] ?? t.min_coverage }), onSuccess: () => qc.invalidateQueries({ queryKey: ["thresholds"] }) });
  return (
    <Card className="mt-4">
      <CardHeader title="Coverage thresholds" sub="Effective threshold = min(configured floor, historical P10 coverage by product × horizon) when relaxation is on" />
      {q.isLoading ? <Spinner /> : (
        <Table><thead><tr><Th>Product</Th><Th>Region</Th><Th className="text-right">Min coverage</Th><Th>Hist. relax</Th><Th className="text-right">Concentration</Th><Th /></tr></thead>
          <tbody>{q.data?.map((t) => (
            <tr key={t.id}><Td>{t.product_code ?? "default"}</Td><Td>{t.region_code ?? "all"}</Td>
              <Td className="text-right"><Input type="number" step="0.05" min="0" max="1.5" className="ml-auto h-7 w-20 text-right" disabled={!can("planner")} value={edit[t.id] ?? t.min_coverage} onChange={(e) => setEdit({ ...edit, [t.id]: Number(e.target.value) })} /></Td>
              <Td>{t.use_historical_baseline ? "yes" : "no"}</Td><Td className="text-right">{fmtPct(t.concentration_threshold, 0)} @ stage ≤{t.concentration_max_stage}</Td>
              <Td>{can("planner") && edit[t.id] != null && <Button size="sm" onClick={() => m.mutate(t)}>Save</Button>}</Td></tr>
          ))}</tbody></Table>
      )}
    </Card>
  );
}

export default function RiskPage() {
  const { runId, meta } = useRun();
  const { can } = useAuth();
  const qc = useQueryClient();
  const [type, setType] = useState(""); const [status, setStatus] = useState("OPEN"); const [sel, setSel] = useState<Alert | null>(null);
  const sum = useQuery({ queryKey: ["risk-summary", runId], queryFn: () => get<RiskSummary>("/risk/summary", { run_id: runId }), enabled: !!runId });
  const q = useQuery({ queryKey: ["alerts", runId, type, status], queryFn: () => get<Alert[]>("/risk/alerts", { run_id: runId, alert_type: type, status }), enabled: !!runId });
  const refresh = useMutation({ mutationFn: () => post("/risk/refresh", undefined, { run_id: runId }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["alerts"] }); qc.invalidateQueries({ queryKey: ["risk-summary"] }); } });
  if (!runId) return <Empty>No run selected.</Empty>;
  return (
    <div>
      {meta?.capabilities && (!meta.capabilities.has_backlog || !meta.capabilities.has_capacity || !meta.capabilities.has_crm) && (
        <CapabilityNote title="Some risk checks are off for this workspace">
          {!meta.capabilities.has_backlog && <>No backlog data → coverage ratio and revenue-gap alerts are disabled. </>}
          {!meta.capabilities.has_capacity && <>No capacity data → supply-bottleneck alerts are disabled. </>}
          {!meta.capabilities.has_crm && <>No CRM data → pipeline-vulnerability alerts are disabled. </>}
          Add the missing tables on the <Link href="/data" className="text-brand underline">Data</Link> page to switch them on.
        </CapabilityNote>
      )}
      <PageHeader title="Risk Center" sub="Coverage = Backlog ÷ Consensus. Sorted by financial impact."
        right={<>
          <Select aria-label="Type" value={type} onChange={(e) => setType(e.target.value)}><option value="">All types</option>{TYPES.map((t) => <option key={t} value={t}>{typeLabel(t)}</option>)}</Select>
          <Select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">All statuses</option><option>OPEN</option><option>ACKNOWLEDGED</option><option>RESOLVED</option></Select>
          {can("planner") && <Button variant="outline" disabled={refresh.isPending} onClick={() => refresh.mutate()}>{refresh.isPending ? "Refreshing…" : "Recompute"}</Button>}
        </>} />
      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi label="Open alerts" value={sum.data?.open_alerts ?? "—"} />
        <Kpi label="Revenue at risk" value={fmtUsd(sum.data?.revenue_at_risk_usd)} />
        {TYPES.slice(0, 2).map((t) => <Kpi key={t} label={typeLabel(t)} value={fmtUsd(sum.data?.by_type?.[t]?.impact ?? sum.data?.by_type?.[t]?.impact_usd)} sub={`${sum.data?.by_type?.[t]?.count ?? 0} open`} />)}
      </div>
      <Card>
        {q.isLoading ? <Spinner /> : q.error ? <ErrorBox error={q.error} /> : !q.data?.length ? <Empty>No alerts match.</Empty> : (
          <Table><thead><tr><Th>Severity</Th><Th>Type</Th><Th>Node</Th><Th>Window</Th><Th className="text-right">Impact</Th><Th>Status</Th><Th>Owner</Th></tr></thead>
            <tbody>{q.data.map((a) => (
              <tr key={a.id} className="cursor-pointer hover:bg-line/40" onClick={() => setSel(a)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && setSel(a)}>
                <Td><Badge tone={sevTone(a.severity)}>{a.severity}</Badge></Td><Td>{typeLabel(a.alert_type)}</Td><Td className="text-xs">{a.oem}/{a.region}/{a.product}</Td>
                <Td className="whitespace-nowrap text-xs">{fmtMonth(a.first_month)}–{fmtMonth(a.last_month)}</Td><Td className="text-right tabular-nums">{fmtUsd(a.financial_impact_usd)}</Td><Td>{a.status}</Td><Td className="text-xs">{a.owner ?? "—"}</Td></tr>
            ))}</tbody></Table>
        )}
      </Card>
      <Thresholds />
      <AlertSheet key={sel?.id} a={sel} onClose={() => setSel(null)} />
    </div>
  );
}
