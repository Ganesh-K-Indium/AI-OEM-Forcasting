"use client";
import { useMemo, useState } from "react";
import { Area, Bar, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis } from "recharts";
import type { FcPoint, HistPoint } from "@/lib/types";
import { fmtMonth, fmtNum, fmtUsd } from "@/lib/utils";
import { Table, Td, Th } from "@/components/ui";

export type Metric = "revenue" | "units";
interface Row { month: string; label: string; actual?: number; p50?: number; band?: [number, number]; p10?: number; p90?: number; uplift?: number; consensus?: number; override?: number; baseline?: number; hasOverride?: boolean }

export function buildRows(history: HistPoint[], forecast: FcPoint[], metric: Metric, histMonths = 24): Row[] {
  const rev = metric === "revenue";
  const rows: Row[] = history.slice(-histMonths).map((h) => ({ month: h.month, label: fmtMonth(h.month), actual: rev ? h.revenue : h.units }));
  forecast.forEach((f) =>
    rows.push({
      month: f.month, label: fmtMonth(f.month),
      p50: rev ? f.revenue_p50 : f.units_p50, p10: rev ? f.revenue_p10 : f.units_p10, p90: rev ? f.revenue_p90 : f.units_p90,
      band: [rev ? f.revenue_p10 : f.units_p10, rev ? f.revenue_p90 : f.units_p90],
      uplift: rev ? f.uplift_revenue : f.uplift_units,
      consensus: rev ? f.consensus_revenue : f.consensus_units,
      override: (rev ? f.override_revenue : f.override_units) ?? undefined,
      baseline: rev ? f.baseline_units * f.asp_usd : f.baseline_units,
    }),
  );
  // join the actuals line to the first forecast point for visual continuity of the P50 line
  return rows;
}

const NAMES: Record<string, string> = { actual: "Actuals (ERP)", p50: "AI P50 (baseline + net uplift)", band: "P10–P90 range", uplift: "Commercial uplift (net of baseline)", consensus: "Consensus", override: "Sales override" };

function Tip({ active, payload, label, metric }: any) {
  if (!active || !payload?.length) return null;
  const f = (v: number) => (metric === "revenue" ? fmtUsd(v) : `${fmtNum(v)}k`);
  const seen = new Set<string>();
  return (
    <div className="rounded-md border bg-raised p-2 text-xs shadow-md">
      <div className="mb-1 font-semibold">{label}</div>
      {payload.filter((p: any) => p.value != null && !seen.has(p.dataKey) && seen.add(p.dataKey)).map((p: any) => (
        <div key={p.dataKey} className="flex justify-between gap-4">
          <span className="text-ink2">{NAMES[p.dataKey] ?? p.dataKey}</span>
          <span className="tabular-nums">{Array.isArray(p.value) ? `${f(p.value[0])} – ${f(p.value[1])}` : f(p.value)}</span>
        </div>
      ))}
    </div>
  );
}

export function FanChart({ history, forecast, metric, height = 340, showUplift = true }: { history: HistPoint[]; forecast: FcPoint[]; metric: Metric; height?: number; showUplift?: boolean }) {
  const [table, setTable] = useState(false);
  const rows = useMemo(() => buildRows(history, forecast, metric), [history, forecast, metric]);
  const first = forecast[0]?.month;
  const fmt = (v: number) => (metric === "revenue" ? fmtUsd(v) : fmtNum(v, 0));
  return (
    <div>
      <div className="mb-2 flex justify-end">
        <button className="text-xs text-ink2 underline" onClick={() => setTable((t) => !t)} aria-pressed={table}>{table ? "Show chart" : "Show table"}</button>
      </div>
      {table ? (
        <div className="max-h-[340px] overflow-y-auto">
          <Table>
            <thead><tr><Th>Month</Th><Th>Actual</Th><Th>P10</Th><Th>P50</Th><Th>P90</Th><Th>Uplift</Th><Th>Override</Th><Th>Consensus</Th></tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.month}><Td>{r.label}</Td><Td>{r.actual != null ? fmt(r.actual) : ""}</Td><Td>{r.p10 != null ? fmt(r.p10) : ""}</Td><Td>{r.p50 != null ? fmt(r.p50) : ""}</Td>
                <Td>{r.p90 != null ? fmt(r.p90) : ""}</Td><Td>{r.uplift != null ? fmt(r.uplift) : ""}</Td><Td>{r.override != null ? fmt(r.override) : ""}</Td><Td>{r.consensus != null ? fmt(r.consensus) : ""}</Td></tr>
            ))}</tbody>
          </Table>
        </div>
      ) : (
        <div role="img" aria-label={`Fan chart of ${metric} actuals and forecast with P10 to P90 range`} style={{ height }}>
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
              <CartesianGrid vertical={false} />
              <XAxis dataKey="label" tickLine={false} axisLine={false} interval="preserveStartEnd" minTickGap={24} />
              <YAxis tickFormatter={fmt} tickLine={false} axisLine={false} width={56} />
              <Tooltip content={<Tip metric={metric} />} cursor={{ stroke: "var(--muted)", strokeDasharray: "3 3" }} />
              <Legend verticalAlign="top" height={28} iconType="plainline" formatter={(v) => <span style={{ color: "var(--ink2)", fontSize: 11 }}>{NAMES[v as string] ?? v}</span>} />
              <Area dataKey="band" name="band" stroke="none" fill="var(--s1)" fillOpacity={0.16} isAnimationActive={false} />
              {showUplift && <Bar dataKey="uplift" name="uplift" fill="var(--s3)" radius={[4, 4, 0, 0]} barSize={10} isAnimationActive={false} />}
              <Line dataKey="actual" name="actual" stroke="var(--s1)" strokeWidth={2} dot={false} connectNulls={false} isAnimationActive={false} />
              <Line dataKey="p50" name="p50" stroke="var(--s1)" strokeWidth={2} strokeDasharray="6 4" dot={false} isAnimationActive={false} />
              <Line dataKey="consensus" name="consensus" stroke="var(--ink)" strokeWidth={2} dot={false} isAnimationActive={false} />
              <Scatter dataKey="override" name="override" fill="var(--s2)" shape="diamond" isAnimationActive={false} />
              {first && <></>}
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
      <p className="mt-1 text-[11px] text-ink2">Range shows calibrated P10–P90 per node (marginal quantiles, not additive across nodes). Uplift bars are net of baseline to avoid double counting.</p>
    </div>
  );
}
