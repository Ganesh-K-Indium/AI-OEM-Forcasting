"use client";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { get } from "@/lib/api";
import { useRun } from "@/lib/run-context";
import type { Benchmark } from "@/lib/types";
import { Badge, Card, CardHeader, Empty, ErrorBox, PageHeader, PageIntro, Select, Spinner, Table, Td, Th } from "@/components/ui";
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
      <PageIntro id="benchmark"
        what="Shows which forecasting models were tried, how accurate each one was when tested on past data, and which model won for each kind of series. It is the evidence behind the choice of model, not a forecast itself."
        steps={["Pick a demand segment at the top right; each segment behaves differently, so each has its own winners.", "Read across a row: the model's error at 1, 3, 6 and 12 months ahead. Lower is better, and ★ marks the best in each column.", "Check the Champions list to see which model is used for each segment and lead-time bucket."]}
        points={[
          ["Backtest", "Pretending to be in the past: models are trained on older months and asked to predict the months that followed, over several starting points (‘origins’), then scored against what really happened."],
          ["wMAPE", "Error as a % of actual volume. 15% means forecasts were off by about 15% overall. Lower is better."],
          ["Bias", "Do forecasts lean one way? Above 0 means they run too low (under-forecast); below 0, too high."],
          ["P10–P90 coverage", "How often reality landed inside the model's 80% range. Close to 80% means the ranges are honest."],
          ["Champion", "The model chosen for a segment. If scores are within 2%, a sensible default for that segment wins the tie."],
          ["Status", "Models that are not installed or failed are shown as unavailable, with the reason, instead of being hidden."],
        ]} />
      {data!.notes.length > 0 && (
        <Card className="mb-4 space-y-1 p-3 text-sm">{data!.notes.map((n, i) => <div key={i} className="flex gap-2"><Badge tone="warn">Note</Badge><span>{n}</span></div>)}</Card>
      )}
      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title={`wMAPE by model and horizon — ${seg}`} sub={`Backtest origins: ${data!.backtest_origins.length}. Few folds at h=12; indicative only.`} />
          {models.length === 0 ? <Empty>No benchmark rows for this segment.</Empty> : (
            <Table>
              <thead><tr><Th>Model</Th><Th tip="Kind of method: statistical, machine learning, or foundation model.">Family</Th>{HORIZONS.map((h) => <Th key={h} className="text-right" tip={`Error when forecasting ${h} month${h > 1 ? "s" : ""} ahead. Lower is better.`}>{h} mo wMAPE</Th>)}<Th className="text-right" tip="Above 0 = forecasts run too low; below 0 = too high. Measured 3 months ahead.">Bias (3 mo)</Th><Th className="text-right" tip="Share of actuals that fell inside the model's P10–P90 range. About 80% is ideal.">In-range % (3 mo)</Th><Th>Status</Th></tr></thead>
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
            <CardHeader title="Champions" sub="The model used for each segment" help="Bucket 1–3 / 4–6 / 7–12 is how many months ahead. A segment can have different champions for near and far months. Within 2% of the best score, the sensible default for that segment wins." />
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
