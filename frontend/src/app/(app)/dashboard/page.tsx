"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { get } from "@/lib/api";
import { useRun } from "@/lib/run-context";
import type { Dashboard } from "@/lib/types";
import { Badge, Card, CardHeader, Empty, ErrorBox, Kpi, PageHeader, Select, Spinner, sevTone } from "@/components/ui";
import { FanChart, type Metric } from "@/components/charts/FanChart";
import { HBar } from "@/components/charts/Bars";
import { fmtMonth, fmtNum, fmtPct, fmtUsd } from "@/lib/utils";

export default function DashboardPage() {
  const { runId, meta } = useRun();
  const [metric, setMetric] = useState<Metric>("revenue");
  const q = useQuery({ queryKey: ["dashboard", runId], queryFn: () => get<Dashboard>("/dashboard", { run_id: runId }), enabled: !!runId });
  if (!runId) return <Empty>No forecast run yet. An admin can seed the demo from Admin → Jobs.</Empty>;
  if (q.isLoading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data!;
  const k = d.kpis;
  const cov = k.backlog_coverage;
  const covTone = cov == null ? "neutral" : cov >= 0.6 ? "good" : cov >= 0.4 ? "warn" : "crit";
  const seg = Object.entries(d.segments ?? {}).map(([name, value]) => ({ name, value }));
  return (
    <div>
      <PageHeader title="Executive Dashboard" sub={`Cycle ${fmtMonth(d.cycle_month)} · ${d.cycle_status ?? "—"}${d.locked ? " · locked" : ""} · horizon ${meta?.horizon ?? "—"} months`}
        right={<Select aria-label="Metric" value={metric} onChange={(e) => setMetric(e.target.value as Metric)}><option value="revenue">Revenue (USD)</option><option value="units">Units (kunits)</option></Select>} />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
        <Kpi label="Total Consensus Revenue" value={fmtUsd(k.total_consensus_revenue)} sub={k.consensus_vs_ai_pct != null ? `${k.consensus_vs_ai_pct >= 0 ? "+" : ""}${fmtPct(k.consensus_vs_ai_pct)} vs AI` : undefined} />
        <Kpi label="AI Forecast vs Actual" value={d.ai_vs_actual?.variance_pct != null ? `${d.ai_vs_actual.variance_pct >= 0 ? "+" : ""}${fmtPct(d.ai_vs_actual.variance_pct)}` : "—"}
          sub={d.ai_vs_actual ? `${fmtUsd(d.ai_vs_actual.ai_revenue)} vs ${fmtUsd(d.ai_vs_actual.actual_revenue)} (last ${d.ai_vs_actual.months.length} mo, h=1)` : "Needs a matured frozen forecast"} />
        <Kpi label="Backlog Coverage (next 3 mo)" value={<span className="flex items-center gap-2">{fmtPct(cov)}<Badge tone={covTone}>{covTone === "good" ? "OK" : covTone === "warn" ? "Watch" : covTone === "crit" ? "Low" : "n/a"}</Badge></span>} sub={`${fmtUsd(k.backlog_value_t3)} backlog / ${fmtUsd(k.forecast_value_t3)} consensus`} />
        <Kpi label="Revenue at Risk" value={fmtUsd(k.revenue_at_risk)} sub={`+ ${fmtUsd(k.pipeline_at_risk)} pipeline-vulnerable uplift`} />
        <Kpi label="Upside Potential (P90)" value={fmtUsd(k.upside_potential)} sub="P90 minus consensus" />
        <Kpi label="Overall wMAPE" value={fmtPct(k.overall_wmape_realized ?? k.backtest_wmape)} sub={k.overall_wmape_realized != null ? "Realised (frozen forecasts)" : "Backtest champion (pooled)"} />
      </div>
      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title={`Total company — ${metric === "revenue" ? "revenue" : "units"} trend and forecast`} sub={`${k.active_overrides} active overrides · net commercial uplift ${fmtUsd(k.net_uplift_revenue)}`} />
          <div className="p-4"><FanChart history={d.trend.history} forecast={d.trend.forecast} metric={metric} /></div>
        </Card>
        <div className="space-y-4">
          <Card>
            <CardHeader title="Top risks" right={<Link href="/risk" className="text-xs text-brand underline">All</Link>} />
            {d.top_risks.length === 0 ? <Empty>No open risks.</Empty> : (
              <ul className="divide-y">
                {d.top_risks.map((r) => (
                  <li key={r.id} className="flex items-start justify-between gap-2 p-3 text-sm">
                    <div><div className="font-medium">{r.title}</div><div className="text-xs text-ink2">{r.type.replace(/_/g, " ")} · {r.oem}/{r.region}/{r.product}</div></div>
                    <div className="text-right"><div className="tabular-nums">{fmtUsd(r.impact)}</div><Badge tone={sevTone(r.severity)}>{r.severity}</Badge></div>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card>
            <CardHeader title="Segment mix" sub="Series per demand segment" />
            <div className="p-3">{seg.length ? <HBar data={seg} fmt={(v) => fmtNum(v, 0)} height={Math.max(140, seg.length * 34)} /> : <Empty>No segmentation in this run.</Empty>}</div>
          </Card>
        </div>
      </div>
      {d.headline && (
        <Card className="mt-4 p-4 text-sm text-ink2">
          Backtest headline: champion pooled wMAPE {fmtPct(d.headline.champion_backtest_wmape)}
          {d.headline.naive_backtest_wmape != null && <> vs naive {fmtPct(d.headline.naive_backtest_wmape)}</>}. With ~36 months of history only a few rolling-origin folds exist at long horizons — treat rankings as indicative.
        </Card>
      )}
    </div>
  );
}
