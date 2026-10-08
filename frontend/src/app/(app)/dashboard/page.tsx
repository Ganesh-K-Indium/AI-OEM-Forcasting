"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { get } from "@/lib/api";
import { useRun } from "@/lib/run-context";
import type { Dashboard } from "@/lib/types";
import { Badge, Card, CardHeader, Empty, ErrorBox, Kpi, PageHeader, PageIntro, Select, Spinner, sevTone } from "@/components/ui";
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
      <PageIntro id="dashboard"
        what="The one-page summary of the current forecast: how much revenue is expected over the next 12 months, how well the AI has done so far, and what could go wrong. Use the (i) icons for what each number means."
        points={[
          ["Consensus", "The agreed forecast = the AI forecast + any planner overrides + extra revenue from the sales pipeline. This is the number the business plans on."],
          ["AI forecast (baseline)", "What the statistical models predict from sales history alone. It is never edited; overrides are layered on top and tracked separately."],
          ["P10 / P50 / P90", "A range instead of one number. P50 is the middle (most likely) outcome. Reality should land below P10 only 1 time in 10 and above P90 only 1 time in 10."],
          ["Backlog", "Orders already received but not yet shipped. The more of a month's forecast that is already backlog, the safer it is."],
          ["Cycle", "One monthly planning round. Locking a cycle freezes its forecast so its accuracy can be measured fairly later."],
          ["Units → price → revenue", "The platform forecasts units first, then the average selling price, and multiplies them to get revenue."],
        ]} />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
        <Kpi label="Total Consensus Revenue" value={fmtUsd(k.total_consensus_revenue)} sub={k.consensus_vs_ai_pct != null ? `${k.consensus_vs_ai_pct >= 0 ? "+" : ""}${fmtPct(k.consensus_vs_ai_pct)} vs the AI alone` : undefined}
          help={<><p>Expected revenue for <b>all OEMs over the whole forecast horizon</b> (shown in the page header), after planner overrides and sales-pipeline uplift.</p><p>The small % compares it with the pure AI forecast. <b>+0.2%</b> means overrides and pipeline together added 0.2% on top of what the model predicted alone; near 0% means people mostly agreed with the AI.</p></>} />
        <Kpi label="AI Forecast vs Actual" value={d.ai_vs_actual?.variance_pct != null ? `${d.ai_vs_actual.variance_pct >= 0 ? "+" : ""}${fmtPct(d.ai_vs_actual.variance_pct)}` : "—"}
          sub={d.ai_vs_actual ? `${(d.ai_vs_actual.variance_pct ?? 0) < 0 ? "AI was too low" : "AI was too high"}: AI said ${fmtUsd(d.ai_vs_actual.ai_revenue)}, actual ${fmtUsd(d.ai_vs_actual.actual_revenue)} (last ${d.ai_vs_actual.months.length} months, forecast made 1 month ahead)` : "Needs an earlier forecast whose months have now happened"}
          help={<><p>A report card on <b>months that have already happened</b>. It takes the AI forecast made one month before each of the last few months and compares it with actual revenue.</p><p><b>Negative</b> = the AI forecast was lower than reality (under-forecast). <b>Positive</b> = higher (over-forecast). Example: -7.6% means the AI said $77.9M but $84.3M was actually sold.</p><p>Shows “—” until an earlier forecast exists whose months now have actuals.</p></>} />
        <Kpi label="Backlog Coverage (next 3 mo)" value={<span className="flex items-center gap-2">{fmtPct(cov)}<Badge tone={covTone}>{covTone === "good" ? "OK" : covTone === "warn" ? "Watch" : covTone === "crit" ? "Low" : "n/a"}</Badge></span>} sub={meta?.capabilities?.has_backlog === false ? "No backlog data in this workspace" : `${fmtUsd(k.backlog_value_t3)} already ordered of ${fmtUsd(k.forecast_value_t3)} forecast`}
          help={<><p>Of the revenue forecast for the <b>next 3 months</b>, how much is already backed by orders in hand (backlog). Formula: backlog ÷ consensus.</p><p>Higher is safer. Here: <b>OK</b> at 60% or more, <b>Watch</b> from 40% to 60%, <b>Low</b> below 40%. 53.5% means about half of next quarter is already booked and the rest still has to be won.</p><p>The Risk Center applies stricter per-product thresholds and raises alerts where a specific OEM, region or product falls short.</p></>} />
        <Kpi label="Revenue at Risk" value={fmtUsd(k.revenue_at_risk)} sub={meta?.capabilities?.has_backlog === false && meta?.capabilities?.has_capacity === false ? "Needs backlog or capacity data" : meta?.capabilities?.has_crm === false ? "No CRM data, so no pipeline risk is measured" : `plus ${fmtUsd(k.pipeline_at_risk)} that depends on a few early-stage deals`}
          help={<><p>Money tied up in <b>open alerts</b> on the Risk Center:</p><ul className="list-disc pl-4"><li><b>Revenue gap</b>: forecast revenue not yet backed by orders.</li><li><b>Supply bottleneck</b>: forecast demand above factory capacity.</li></ul><p>Resolved alerts are not counted. The line underneath is shown separately: extra revenue from the sales pipeline that rests on only a few early-stage opportunities, so it is fragile.</p></>} />
        <Kpi label="Upside Potential (P90)" value={fmtUsd(k.upside_potential)} sub="optimistic case minus consensus"
          help={<><p>How much <b>more</b> revenue could come in if things go well.</p><p>It is the optimistic forecast (P90, which reality should exceed only 1 time in 10) minus the consensus. A large number means the forecast is uncertain on the upside; it is not a promise.</p></>} />
        <Kpi label="Overall wMAPE" value={fmtPct(k.overall_wmape_realized ?? k.backtest_wmape)} sub={k.overall_wmape_realized != null ? "measured on real past months" : "measured by replaying history (no real past months yet)"}
          help={<><p><b>How far off the forecast is, as a % of actual volume.</b> 13% means that, in total, forecasts missed actuals by 13% (too high or too low). <b>Lower is better.</b></p><p>“wMAPE” = weighted mean absolute percentage error; large series count more than tiny ones.</p><p>It is measured on real past months when earlier forecasts exist; otherwise by a <b>backtest</b>: the models are re-run on old history and scored on what came next.</p></>} />
      </div>
      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title={`Total company — ${metric === "revenue" ? "revenue" : "units"} trend and forecast`} sub={`${k.active_overrides} active overrides · net commercial uplift ${fmtUsd(k.net_uplift_revenue)}`}
            help={<><p><b>Left of the divide:</b> actual history from the sales data. <b>Right:</b> the forecast for the coming months.</p><ul className="list-disc pl-4"><li><b>AI P50</b>: the model's most likely value.</li><li><b>Shaded band</b>: the P10–P90 range. It widens further out because the future is less certain.</li><li><b>Consensus</b>: the agreed number after overrides and pipeline uplift.</li></ul><p>Hover for exact values; “Show table” lists the same numbers.</p></>} />
          <div className="p-4"><FanChart history={d.trend.history} forecast={d.trend.forecast} metric={metric} /></div>
        </Card>
        <div className="space-y-4">
          <Card>
            <CardHeader title="Top risks" help="The five largest open alerts by money at stake. Click All to open the Risk Center, where each alert can be acknowledged or resolved." right={<Link href="/risk" className="text-xs text-brand underline">All</Link>} />
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
            <CardHeader title="Segment mix" sub="Series per demand segment"
              help={<><p>Every OEM × region × product series is labelled by its sales pattern, and that decides which models are tried on it.</p><ul className="list-disc pl-4"><li><b>Smooth</b>: steady, predictable.</li><li><b>Erratic</b>: regular sales but with big swings.</li><li><b>Seasonal</b>: a repeating yearly pattern.</li><li><b>Intermittent</b>: many months with no sales.</li><li><b>Lumpy</b>: no-sale months and big spikes.</li><li><b>Complex</b>: closely linked to sales-pipeline signals.</li></ul></>} />
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
