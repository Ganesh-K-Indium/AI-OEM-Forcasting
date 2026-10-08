"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post } from "@/lib/api";
import { useRun } from "@/lib/run-context";
import { useAuth } from "@/lib/auth";
import { REASONS, type Detail, type Explorer, type Filters, type Override, type ReasonCode } from "@/lib/types";
import { Badge, Button, Card, CardHeader, Empty, ErrorBox, Input, Kpi, Label, PageHeader, Select, Sheet, Spinner, Table, Tabs, Td, Textarea, Th } from "@/components/ui";
import { FanChart, type Metric } from "@/components/charts/FanChart";
import { fmtMonth, fmtNum, fmtPct, fmtTs, fmtUsd } from "@/lib/utils";

function OverrideForm({ ex, onDone }: { ex: Explorer; onDone: () => void }) {
  const qc = useQueryClient();
  const [month, setMonth] = useState(ex.forecast[0]?.month ?? "");
  const [basis, setBasis] = useState<"REVENUE" | "UNITS">("REVENUE");
  const [value, setValue] = useState("");
  const [reason, setReason] = useState<ReasonCode>("PROJECT_DELAY");
  const [comment, setComment] = useState("");
  const fp = ex.forecast.find((f) => f.month === month);
  const ai = fp ? (basis === "REVENUE" ? fp.revenue_p50 : fp.units_p50) : 0;
  const v = Number(value);
  const dev = ai && value !== "" ? v / ai - 1 : null;
  const needsComment = dev != null && Math.abs(dev) > 0.6;
  const m = useMutation({
    mutationFn: () => post<Override>("/overrides", { run_id: ex.run_id, oem: ex.oem, region: ex.region, product: ex.product, month, basis, value: v, reason_code: reason, comment: comment || null }),
    onSuccess: () => { ["explorer", "detail", "dashboard", "overrides"].forEach((k) => qc.invalidateQueries({ queryKey: [k] })); onDone(); },
  });
  const bad = !month || value === "" || Number.isNaN(v) || v < 0 || (needsComment && comment.trim().length < 15);
  return (
    <form onSubmit={(e) => { e.preventDefault(); m.mutate(); }} className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <div><Label>Month</Label><Select className="w-full" value={month} onChange={(e) => setMonth(e.target.value)}>{ex.forecast.map((f) => <option key={f.month} value={f.month}>{fmtMonth(f.month)}</option>)}</Select></div>
        <div><Label>Basis</Label><Select className="w-full" value={basis} onChange={(e) => setBasis(e.target.value as "REVENUE" | "UNITS")}><option value="REVENUE">Revenue (USD)</option><option value="UNITS">Units (kunits)</option></Select></div>
        <div><Label>Override value (AI P50: {basis === "REVENUE" ? fmtUsd(ai) : `${fmtNum(ai)}k`})</Label><Input type="number" step="any" min="0" value={value} onChange={(e) => setValue(e.target.value)} required /></div>
        <div><Label>Reason code</Label><Select className="w-full" value={reason} onChange={(e) => setReason(e.target.value as ReasonCode)}>{REASONS.map((r) => <option key={r} value={r}>{r.replace(/_/g, " ")}</option>)}</Select></div>
      </div>
      {dev != null && <div className="text-xs text-ink2">Deviation from AI: <b>{dev >= 0 ? "+" : ""}{fmtPct(dev)}</b>{needsComment && <span className="ml-2 text-serious">Large deviation — comment of at least 15 characters required.</span>}</div>}
      <div><Label>Comment</Label><Textarea rows={2} value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Evidence or rationale (visible in audit log)" /></div>
      {m.error && <div role="alert" className="text-sm text-crit">{(m.error as Error).message}</div>}
      <Button disabled={bad || m.isPending}>{m.isPending ? "Submitting…" : "Submit override"}</Button>
      <p className="text-[11px] text-ink2">Overrides are append-only revisions; the AI baseline is never modified. Parent-level overrides are allocated down to the finest level without disturbing pinned children.</p>
    </form>
  );
}

const cm = (m: any) => (m && typeof m === "object" ? `win-prob model · OOF AUC ${m.oof_auc != null ? fmtNum(m.oof_auc, 2) : "n/a"} · ${m.closed_opps ?? "?"} closed opps` : m);
type DTab = "drivers" | "opps" | "overrides" | "fva" | "audit" | "coverage";
function DetailSheet({ open, onClose, oem, region, product }: { open: boolean; onClose: () => void; oem: string; region: string; product: string }) {
  const { runId } = useRun();
  const [tab, setTab] = useState<DTab>("drivers");
  const q = useQuery({ queryKey: ["detail", runId, oem, region, product], queryFn: () => get<Detail>("/forecast/detail", { run_id: runId, oem, region, product }), enabled: open && !!runId });
  const d = q.data;
  const drv: [string, any][] = d ? [["Segment", d.drivers.segment], ["Champion model", d.explorer.champion_model], ["ADI", d.drivers.adi != null ? fmtNum(d.drivers.adi, 2) : null], ["CV²", d.drivers.cv2 != null ? fmtNum(d.drivers.cv2, 2) : null],
    ["Seasonal strength", d.drivers.seasonality_strength != null ? fmtNum(d.drivers.seasonality_strength, 2) : null], ["ACF(12)", d.drivers.acf12 != null ? fmtNum(d.drivers.acf12, 2) : null],
    ["Exogenous score", d.drivers.exog_strength != null ? fmtNum(d.drivers.exog_strength, 2) : null], ["Uplift β", d.drivers.net_uplift_beta != null ? fmtNum(d.drivers.net_uplift_beta, 2) : null],
    ["Commercial model", cm(d.drivers.commercial_model)]] : [];
  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()} title={`Detail · ${oem} / ${region} / ${product}`} wide>
      {q.isLoading && <Spinner />}{q.error && <ErrorBox error={q.error} />}
      {d && (
        <div className="space-y-3">
          <Tabs value={tab} onChange={setTab} tabs={[{ id: "drivers", label: "Drivers" }, { id: "opps", label: `SFDC opps (${d.opportunities.length})` }, { id: "overrides", label: `Overrides (${d.overrides.length})` }, { id: "fva", label: "FVA" }, { id: "coverage", label: "Coverage" }, { id: "audit", label: "Audit log" }]} />
          {tab === "drivers" && (
            <div className="grid grid-cols-2 gap-3 text-sm">
              {drv.map(([k, v]) => <div key={k} className="rounded border p-2"><div className="text-xs text-ink2">{k}</div><div className="font-medium">{v ?? "—"}</div></div>)}
              {d.explorer.scenario_tag && <div className="col-span-2 text-xs text-ink2">Synthetic scenario tag: <Badge>{d.explorer.scenario_tag}</Badge></div>}
            </div>
          )}
          {tab === "opps" && (d.opportunities.length === 0 ? <Empty>No active opportunities contribute at this node.</Empty> : (
            <Table><thead><tr><Th>SFDC</Th><Th>Node</Th><Th>Stage</Th><Th className="text-right">Win P</Th><Th className="text-right">Rep P</Th><Th className="text-right">Expected k</Th><Th className="text-right">Uplift share</Th></tr></thead>
              <tbody>{d.opportunities.map((o) => <tr key={o.opportunity_id}><Td>{o.sfdc_id}</Td><Td className="text-xs">{o.oem}/{o.region}/{o.product}</Td><Td>{o.stage}</Td><Td className="text-right tabular-nums">{fmtPct(o.win_prob, 0)}</Td><Td className="text-right tabular-nums">{fmtPct(o.rep_probability, 0)}</Td><Td className="text-right tabular-nums">{fmtNum(o.expected_units)}</Td><Td className="text-right tabular-nums">{fmtPct(o.share_of_uplift, 0)}</Td></tr>)}</tbody></Table>
          ))}
          {tab === "overrides" && (d.overrides.length === 0 ? <Empty>No overrides at this node.</Empty> : (
            <Table><thead><tr><Th>Month</Th><Th>Rev</Th><Th className="text-right">Value</Th><Th>Reason</Th><Th>By</Th><Th>Status</Th></tr></thead>
              <tbody>{d.overrides.map((o) => <tr key={o.id}><Td>{fmtMonth(o.month)}</Td><Td>r{o.revision}</Td><Td className="text-right tabular-nums">{fmtUsd(o.override_revenue)}</Td><Td>{o.reason_code.replace(/_/g, " ")}</Td><Td className="text-xs">{o.user_id}</Td><Td><Badge tone={o.status === "ACTIVE" ? "good" : "neutral"}>{o.status}</Badge></Td></tr>)}</tbody></Table>
          ))}
          {tab === "fva" && (d.fva.length === 0 ? <Empty>FVA appears once frozen forecasts mature against actuals.</Empty> : (
            <Table><thead><tr><Th>Scope</Th><Th>h</Th><Th className="text-right">n</Th><Th className="text-right">wMAPE naive</Th><Th className="text-right">AI</Th><Th className="text-right">Consensus</Th><Th className="text-right">FVA sales</Th></tr></thead>
              <tbody>{d.fva.map((f, i) => <tr key={i}><Td>{f.scope}</Td><Td>{f.horizon ?? "all"}</Td><Td className="text-right">{f.n_obs}</Td><Td className="text-right">{fmtPct(f.wmape_naive)}</Td><Td className="text-right">{fmtPct(f.wmape_ai)}</Td><Td className="text-right">{fmtPct(f.wmape_consensus)}</Td><Td className="text-right">{fmtPct(f.fva_sales)}{f.significant && <Badge tone="info" className="ml-1">sig.</Badge>}</Td></tr>)}</tbody></Table>
          ))}
          {tab === "coverage" && (d.coverage.length === 0 ? <Empty>No backlog coverage rows.</Empty> : (
            <Table><thead><tr><Th>Month</Th><Th>Node</Th><Th className="text-right">Backlog</Th><Th className="text-right">Consensus</Th><Th className="text-right">Coverage</Th><Th className="text-right">Threshold</Th></tr></thead>
              <tbody>{d.coverage.map((c, i) => <tr key={i}><Td>{fmtMonth(c.month)}</Td><Td className="text-xs">{c.oem}/{c.region}/{c.product}</Td><Td className="text-right">{fmtUsd(c.backlog_usd)}</Td><Td className="text-right">{fmtUsd(c.forecast_usd)}</Td>
                <Td className="text-right">{c.coverage != null ? fmtPct(c.coverage, 0) : "—"}{c.coverage != null && c.coverage < c.threshold && <Badge tone="serious" className="ml-1">below</Badge>}</Td><Td className="text-right">{fmtPct(c.threshold, 0)}</Td></tr>)}</tbody></Table>
          ))}
          {tab === "audit" && (d.audit.length === 0 ? <Empty>No audit entries.</Empty> : (
            <ul className="space-y-2 text-xs">{d.audit.map((a) => (
              <li key={a.id} className="rounded border p-2"><div className="flex justify-between"><b>{a.action}</b><span className="text-ink2">{fmtTs(a.ts)}</span></div>
                <div className="text-ink2">{a.user_id} · {a.entity_type} {a.entity_id}</div><div className="mt-1 truncate font-mono text-[10px] text-muted" title={a.hash}>#{a.id} {a.hash.slice(0, 24)}…</div></li>
            ))}</ul>
          ))}
        </div>
      )}
    </Sheet>
  );
}

export default function ExplorerPage() {
  const { runId } = useRun();
  const { can } = useAuth();
  const [oem, setOem] = useState("ALL"); const [region, setRegion] = useState("ALL"); const [product, setProduct] = useState("ALL");
  const [horizon, setHorizon] = useState<number>(12);
  const [metric, setMetric] = useState<Metric>("revenue");
  const [detail, setDetail] = useState(false); const [ovr, setOvr] = useState(false);
  const f = useQuery({ queryKey: ["filters"], queryFn: () => get<Filters>("/filters") });
  const invalid = oem !== "ALL" && region === "ALL" && product !== "ALL";
  const q = useQuery({ queryKey: ["explorer", runId, oem, region, product], queryFn: () => get<Explorer>("/forecast/explorer", { run_id: runId, oem, region, product }), enabled: !!runId && !invalid, retry: 0 });
  if (!runId) return <Empty>No run selected.</Empty>;
  const ex = invalid ? undefined : q.data;
  const fc = ex?.forecast.filter((p) => p.horizon <= horizon) ?? [];
  const sum = (k: "consensus_revenue" | "revenue_p50" | "uplift_revenue") => fc.reduce((a, p) => a + p[k], 0);
  return (
    <div>
      <PageHeader title="Forecast Explorer" sub="Select any node of the OEM × Region × Product hierarchy; ALL aggregates."
        right={<>
          <Select aria-label="OEM" value={oem} onChange={(e) => setOem(e.target.value)}><option value="ALL">All OEMs</option>{f.data?.oems.map((o) => <option key={o}>{o}</option>)}</Select>
          <Select aria-label="Region" value={region} onChange={(e) => setRegion(e.target.value)}><option value="ALL">All regions</option>{f.data?.regions.map((o) => <option key={o}>{o}</option>)}</Select>
          <Select aria-label="Product" value={product} onChange={(e) => setProduct(e.target.value)}><option value="ALL">All products</option>{f.data?.products.map((p) => <option key={p.code} value={p.code}>{p.name ?? p.code}</option>)}</Select>
          <Select aria-label="Horizon" value={horizon} onChange={(e) => setHorizon(Number(e.target.value))}>{(f.data?.horizons ?? [3, 6, 12]).map((h) => <option key={h} value={h}>{h} mo</option>)}</Select>
        </>} />
      {invalid && <div role="alert" className="mb-3 rounded border border-warn/50 bg-warn/15 p-2 text-sm">OEM × Product without a region is not a node in the hierarchy; choose a region or clear the product.</div>}
      {q.isLoading && !invalid && <Spinner />}{q.error && !invalid && <ErrorBox error={q.error} />}
      {ex && (
        <>
          <div className="mb-3 grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Kpi label={`Consensus revenue (${horizon} mo)`} value={fmtUsd(sum("consensus_revenue"))} />
            <Kpi label="AI P50 revenue" value={fmtUsd(sum("revenue_p50"))} />
            <Kpi label="Net commercial uplift" value={fmtUsd(sum("uplift_revenue"))} />
            <Kpi label="Segment · champion" value={<span className="text-base">{ex.segment ?? "aggregate"} · {ex.champion_model ?? "—"}</span>} />
          </div>
          <Card>
            <CardHeader title={`${ex.oem} / ${ex.region} / ${ex.product}`} sub={`Level ${ex.level}`}
              right={<div className="flex gap-2">
                <Select aria-label="Metric" value={metric} onChange={(e) => setMetric(e.target.value as Metric)}><option value="revenue">Revenue</option><option value="units">Units</option></Select>
                <Button variant="outline" onClick={() => setDetail(true)}>Details</Button>
                {can("planner", "sales_rep") && <Button onClick={() => setOvr(true)}>Add override</Button>}
              </div>} />
            <div className="p-4"><FanChart history={ex.history} forecast={fc} metric={metric} /></div>
          </Card>
          <Sheet open={ovr} onOpenChange={setOvr} title={`New override · ${ex.oem}/${ex.region}/${ex.product}`}><OverrideForm ex={ex} onDone={() => setOvr(false)} /></Sheet>
        </>
      )}
      <DetailSheet open={detail} onClose={() => setDetail(false)} oem={oem} region={region} product={product} />
    </div>
  );
}
