"use client";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { get } from "@/lib/api";
import { useRun } from "@/lib/run-context";
import type { Benchmark } from "@/lib/types";
import { Badge, Card, CardHeader, Empty, ErrorBox, PageHeader, Select, Spinner, Table, Td, Th } from "@/components/ui";
import { GroupedBars } from "@/components/charts/Bars";
import { fmtNum, fmtPct } from "@/lib/utils";

const HORIZONS = [1, 3, 6, 12];

export default function BenchmarkPage() {
  const { runId } = useRun();
  const q = useQuery({ queryKey: ["bench", runId], queryFn: () => get<Benchmark>("/benchmark", { run_id: runId }), enabled: !!runId });
  const [seg, setSeg] = useState("ALL");
  const data = q.data;
  const segs = useMemo(() => ["ALL", ...Object.keys(data?.segments ?? {})], [data]);
  const rows = useMemo(() => (data?.rows ?? []).filter((r) => r.segment === seg), [data, seg]);
  const models = useMemo(() => Array.from(new Set(rows.map((r) => r.model_name))), [rows]);
  const cell = (m: string, h: number) => rows.find((r) => r.model_name === m && r.horizon === h);
  const best = (h: number) => Math.min(...rows.filter((r) => r.horizon === h && r.wmape != null && r.status === "OK").map((r) => r.wmape as number));
  if (!runId) return <Empty>No run selected.</Empty>;
  if (q.isLoading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  const chartData = HORIZONS.map((h) => {
    const o: Record<string, any> = { name: `h=${h}` };
    models.filter((m) => cell(m, h)?.wmape != null).slice(0, 8).forEach((m) => { o[m] = cell(m, h)!.wmape; });
    return o;
  });
  const chartModels = models.filter((m) => HORIZONS.some((h) => cell(m, h)?.wmape != null)).slice(0, 8);
  return (
    <div>
      <PageHeader title="Model Benchmark Matrix" sub="Rolling-origin backtest · wMAPE (lower is better) · Bias > 0 = under-forecast"
        right={<Select aria-label="Segment" value={seg} onChange={(e) => setSeg(e.target.value)}>{segs.map((s) => <option key={s}>{s}</option>)}</Select>} />
      {data!.notes.length > 0 && (
        <Card className="mb-4 space-y-1 p-3 text-sm">{data!.notes.map((n, i) => <div key={i} className="flex gap-2"><Badge tone="warn">Note</Badge><span>{n}</span></div>)}</Card>
      )}
      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title={`wMAPE by model and horizon — ${seg}`} sub={`Backtest origins: ${data!.backtest_origins.length}. Few folds at h=12; indicative only.`} />
          {models.length === 0 ? <Empty>No benchmark rows for this segment.</Empty> : (
            <Table>
              <thead><tr><Th>Model</Th><Th>Family</Th>{HORIZONS.map((h) => <Th key={h} className="text-right">h={h} wMAPE</Th>)}<Th className="text-right">Bias (h=3)</Th><Th className="text-right">P10–P90 cov (h=3)</Th><Th>Status</Th></tr></thead>
              <tbody>
                {models.map((m) => {
                  const any = rows.find((r) => r.model_name === m)!;
                  const unavailable = any.status !== "OK";
                  return (
                    <tr key={m} className={unavailable ? "opacity-70" : ""}>
                      <Td className="font-medium">{m}{rows.some((r) => r.model_name === m && r.is_champion) && <Badge tone="good" className="ml-2">Champion</Badge>}</Td>
                      <Td className="text-ink2">{any.model_family}</Td>
                      {HORIZONS.map((h) => { const c = cell(m, h); const isBest = c?.wmape != null && c.wmape === best(h);
                        return <Td key={h} className={`text-right tabular-nums ${isBest ? "font-semibold" : ""}`}>{c?.wmape != null ? fmtPct(c.wmape) : "—"}{isBest && " ★"}</Td>; })}
                      <Td className="text-right tabular-nums">{cell(m, 3)?.bias != null ? fmtPct(cell(m, 3)!.bias) : "—"}</Td>
                      <Td className="text-right tabular-nums">{cell(m, 3)?.coverage80 != null ? fmtPct(cell(m, 3)!.coverage80, 0) : "—"}</Td>
                      <Td>{unavailable ? <span title={any.note ?? ""}><Badge tone="serious">{any.status}</Badge> <span className="text-xs text-ink2">{any.note}</span></span> : <Badge tone="good">OK</Badge>}</Td>
                    </tr>
                  );
                })}
              </tbody>
            </Table>
          )}
        </Card>
        <div className="space-y-4">
          <Card>
            <CardHeader title="Comparison chart" sub="Same data as the table" />
            <div className="p-3">{chartModels.length ? <GroupedBars data={chartData} series={chartModels.map((m) => ({ key: m, name: m }))} fmt={(v) => fmtPct(v, 0)} /> : <Empty>Nothing to plot.</Empty>}</div>
          </Card>
          <Card>
            <CardHeader title="Champions" sub="Segment prior wins ties within 2%" />
            <Table>
              <thead><tr><Th>Segment</Th><Th>Bucket</Th><Th>Champion</Th></tr></thead>
              <tbody>{data!.champions.map((c, i) => <tr key={i}><Td>{c.segment}</Td><Td>{["1–3","4–6","7–12"][Number(c.bucket) - 1] ?? c.bucket}</Td><Td>{c.model ?? c.model_name}</Td></tr>)}</tbody>
            </Table>
            <div className="border-t p-3 text-xs text-ink2">Series per segment: {Object.entries(data!.segments).map(([k, v]) => `${k} ${fmtNum(v, 0)}`).join(" · ")}</div>
          </Card>
        </div>
      </div>
    </div>
  );
}
