"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { del, get, post } from "@/lib/api";
import { useRun } from "@/lib/run-context";
import { useAuth } from "@/lib/auth";
import type { Audit, Cycle, Fva, Override } from "@/lib/types";
import { Badge, Button, Card, CardHeader, Empty, ErrorBox, PageHeader, Select, Spinner, Table, Tabs, Td, Th } from "@/components/ui";
import { fmtMonth, fmtPct, fmtTs, fmtUsd } from "@/lib/utils";

function Overrides() {
  const { runId } = useRun(); const { can } = useAuth(); const qc = useQueryClient();
  const [status, setStatus] = useState("ACTIVE");
  const q = useQuery({ queryKey: ["overrides", runId, status], queryFn: () => get<Override[]>("/overrides", { run_id: runId, status }), enabled: !!runId });
  const inv = () => ["overrides", "dashboard", "explorer"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
  const review = useMutation({ mutationFn: (v: { id: number; approve: boolean }) => post(`/overrides/${v.id}/review`, { approve: v.approve }), onSuccess: inv });
  const withdraw = useMutation({ mutationFn: (id: number) => del(`/overrides/${id}`), onSuccess: inv });
  const err = (review.error || withdraw.error) as Error | null;
  return (
    <Card>
      <CardHeader title="Overrides" sub="Append-only revisions; withdrawing writes a new revision." right={<Select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">All</option><option>ACTIVE</option><option>WITHDRAWN</option><option>SUPERSEDED</option></Select>} />
      {err && <div role="alert" className="p-3 text-sm text-crit">{err.message}</div>}
      {q.isLoading ? <Spinner /> : q.error ? <ErrorBox error={q.error} /> : !q.data?.length ? <Empty>No overrides.</Empty> : (
        <Table><thead><tr><Th>Node</Th><Th>Month</Th><Th className="text-right">AI P50</Th><Th className="text-right">Override</Th><Th>Reason</Th><Th>By</Th><Th>Rev</Th><Th>Status</Th><Th>Approval</Th><Th /></tr></thead>
          <tbody>{q.data.map((o) => (
            <tr key={o.id}><Td className="text-xs">{o.oem}/{o.region}/{o.product}</Td><Td>{fmtMonth(o.month)}</Td><Td className="text-right tabular-nums">{o.override_basis === "REVENUE" ? fmtUsd(o.ai_p50_forecast) : `${o.ai_p50_forecast.toFixed(1)}k`}</Td>
              <Td className="text-right tabular-nums">{o.override_basis === "REVENUE" ? fmtUsd(o.sales_override_value) : `${o.sales_override_value.toFixed(1)}k`}</Td><Td className="text-xs">{o.reason_code.replace(/_/g, " ")}{o.comment && <div className="max-w-[240px] truncate text-ink2" title={o.comment}>{o.comment}</div>}</Td>
              <Td className="text-xs">{o.user_id}</Td><Td>r{o.revision}</Td><Td><Badge tone={o.status === "ACTIVE" ? "good" : "neutral"}>{o.status}</Badge></Td>
              <Td><Badge tone={o.approval_status === "APPROVED" ? "good" : o.approval_status === "REJECTED" ? "crit" : "warn"}>{o.approval_status}</Badge></Td>
              <Td className="whitespace-nowrap">{o.status === "ACTIVE" && <>
                {can("planner") && o.approval_status === "PENDING" && <><Button size="sm" onClick={() => review.mutate({ id: o.id, approve: true })}>Approve</Button> <Button size="sm" variant="outline" onClick={() => review.mutate({ id: o.id, approve: false })}>Reject</Button> </>}
                {can("planner", "sales_rep") && <Button size="sm" variant="ghost" onClick={() => withdraw.mutate(o.id)}>Withdraw</Button>}</>}</Td></tr>
          ))}</tbody></Table>
      )}
    </Card>
  );
}

function FvaTab() {
  const { can } = useAuth(); const qc = useQueryClient();
  const q = useQuery({ queryKey: ["fva"], queryFn: () => get<Fva[]>("/fva") });
  const m = useMutation({ mutationFn: () => post("/fva/refresh"), onSuccess: () => qc.invalidateQueries({ queryKey: ["fva"] }) });
  const fv = (v: number | null) => v == null ? "—" : <span className={v > 0 ? "text-good" : v < 0 ? "text-crit" : ""}>{v > 0 ? "▲ +" : v < 0 ? "▼ " : ""}{(v * 100).toFixed(1)} pp</span>;
  return (
    <Card>
      <CardHeader title="Forecast Value Added" sub="FVA = wMAPE(reference) − wMAPE(forecast), matched lead time. Positive = better. Bootstrap 95% CI." right={can("planner") && <Button variant="outline" disabled={m.isPending} onClick={() => m.mutate()}>{m.isPending ? "Computing…" : "Recompute"}</Button>} />
      {q.isLoading ? <Spinner /> : q.error ? <ErrorBox error={q.error} /> : !q.data?.length ? <Empty>FVA needs frozen forecasts that have matured against actuals (lock cycles, then wait for actuals).</Empty> : (
        <Table><thead><tr><Th>Scope</Th><Th>Lead (mo)</Th><Th className="text-right">n</Th><Th className="text-right">Naive</Th><Th className="text-right">AI</Th><Th className="text-right">Consensus</Th><Th className="text-right">AI vs naive</Th><Th className="text-right">Sales vs AI</Th><Th className="text-right">95% CI</Th><Th>Sig.</Th></tr></thead>
          <tbody>{q.data.map((f, i) => <tr key={i}><Td>{f.scope}</Td><Td>{f.horizon ?? "all"}</Td><Td className="text-right">{f.n_obs}</Td><Td className="text-right tabular-nums">{fmtPct(f.wmape_naive)}</Td><Td className="text-right tabular-nums">{fmtPct(f.wmape_ai)}</Td><Td className="text-right tabular-nums">{fmtPct(f.wmape_consensus)}</Td>
            <Td className="text-right tabular-nums">{fv(f.fva_ai)}</Td><Td className="text-right tabular-nums">{fv(f.fva_sales)}</Td><Td className="text-right text-xs tabular-nums">{f.fva_sales_ci_low != null ? `[${(f.fva_sales_ci_low * 100).toFixed(1)}, ${(f.fva_sales_ci_high! * 100).toFixed(1)}]` : "—"}</Td><Td>{f.significant ? <Badge tone="info">yes</Badge> : "no"}</Td></tr>)}</tbody></Table>
      )}
    </Card>
  );
}

function Cycles() {
  const { runId } = useRun(); const { can } = useAuth(); const qc = useQueryClient();
  const q = useQuery({ queryKey: ["cycles"], queryFn: () => get<Cycle[]>("/cycles") });
  const conf = useQuery({ queryKey: ["conflicts", runId], queryFn: () => get<any[]>("/consensus/conflicts", { run_id: runId }), enabled: !!runId });
  const lock = useMutation({ mutationFn: (id: string) => post(`/runs/${id}/lock`), onSuccess: () => ["cycles", "runs", "meta", "dashboard"].forEach((k) => qc.invalidateQueries({ queryKey: [k] })) });
  const open = useMutation({ mutationFn: (m: string) => post(`/cycles/${m}/consensus`), onSuccess: () => ["cycles", "meta"].forEach((k) => qc.invalidateQueries({ queryKey: [k] })) });
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="Planning cycles" sub="OPEN → FORECASTED → CONSENSUS → LOCKED. Locking freezes consensus for FVA." />
        {(lock.error || open.error) && <div role="alert" className="p-3 text-sm text-crit">{((lock.error || open.error) as Error).message}</div>}
        {q.isLoading ? <Spinner /> : !q.data?.length ? <Empty>No cycles.</Empty> : (
          <Table><thead><tr><Th>Cycle</Th><Th>Status</Th><Th>Run</Th><Th>Locked</Th><Th /></tr></thead>
            <tbody>{q.data.map((c) => (
              <tr key={c.id}><Td>{fmtMonth(c.cycle_month)}</Td><Td><Badge tone={c.status === "LOCKED" ? "good" : "info"}>{c.status}</Badge></Td><Td className="font-mono text-xs">{c.run_id?.slice(0, 8) ?? "—"}</Td><Td className="text-xs">{c.locked_at ? fmtTs(c.locked_at) : "—"}</Td>
                <Td className="whitespace-nowrap">{can("planner") && c.status !== "LOCKED" && <>
                  {c.status === "FORECASTED" && <Button size="sm" variant="outline" onClick={() => open.mutate(c.cycle_month)}>Open consensus</Button>}{" "}
                  {c.run_id && <Button size="sm" disabled={lock.isPending} onClick={() => confirm("Lock this cycle? Consensus will be frozen and cannot be changed.") && lock.mutate(c.run_id)}>Lock</Button>}</>}</Td></tr>
            ))}</tbody></Table>
        )}
      </Card>
      <Card>
        <CardHeader title="Consensus conflicts" sub="Overrides that disagree across hierarchy levels; finer pins win, parent residual is spread." />
        {!conf.data?.length ? <Empty>No conflicts.</Empty> : <pre className="max-h-64 overflow-auto p-3 text-xs">{JSON.stringify(conf.data, null, 2)}</pre>}
      </Card>
    </div>
  );
}

function AuditTab() {
  const [entity, setEntity] = useState("");
  const q = useQuery({ queryKey: ["audit", entity], queryFn: () => get<Audit[]>("/audit", { entity_type: entity, limit: 200 }) });
  const v = useQuery({ queryKey: ["audit-verify"], queryFn: () => get<{ intact: boolean; first_bad_id: number | null }>("/audit/verify") });
  return (
    <Card>
      <CardHeader title="Audit log" sub="SHA-256 hash chain; each entry commits to the previous one."
        right={<div className="flex items-center gap-2">{v.data && <Badge tone={v.data.intact ? "good" : "crit"}>{v.data.intact ? "Chain intact" : `Chain broken at #${v.data.first_bad_id}`}</Badge>}
          <Select aria-label="Entity" value={entity} onChange={(e) => setEntity(e.target.value)}><option value="">All entities</option>{["override", "mapping", "cycle", "run", "user", "setting", "alert"].map((e) => <option key={e}>{e}</option>)}</Select></div>} />
      {q.isLoading ? <Spinner /> : q.error ? <ErrorBox error={q.error} /> : (
        <Table><thead><tr><Th>#</Th><Th>Time</Th><Th>User</Th><Th>Action</Th><Th>Entity</Th><Th>Hash</Th></tr></thead>
          <tbody>{q.data?.map((a) => <tr key={a.id}><Td>{a.id}</Td><Td className="whitespace-nowrap text-xs">{fmtTs(a.ts)}</Td><Td className="text-xs">{a.user_id}</Td><Td>{a.action}</Td><Td className="text-xs">{a.entity_type} {a.entity_id}</Td><Td className="font-mono text-[10px] text-muted" title={a.hash}>{a.hash.slice(0, 14)}…</Td></tr>)}</tbody></Table>
      )}
    </Card>
  );
}

export default function GovernancePage() {
  const [tab, setTab] = useState<"overrides" | "fva" | "cycles" | "audit">("overrides");
  return (
    <div>
      <PageHeader title="Governance" sub="Overrides, forecast value added, cycle lock and audit trail" />
      <div className="mb-4"><Tabs value={tab} onChange={setTab} tabs={[{ id: "overrides", label: "Overrides" }, { id: "fva", label: "FVA" }, { id: "cycles", label: "Cycles & lock" }, { id: "audit", label: "Audit log" }]} /></div>
      {tab === "overrides" && <Overrides />}{tab === "fva" && <FvaTab />}{tab === "cycles" && <Cycles />}{tab === "audit" && <AuditTab />}
    </div>
  );
}
