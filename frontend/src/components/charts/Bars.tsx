"use client";
import { Bar, BarChart, CartesianGrid, Cell, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export const CAT = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)", "var(--s5)", "var(--s6)", "var(--s7)", "var(--s8)"];

/** Single-measure horizontal bars with identity encoded by position + label (never colour alone). */
export function HBar({ data, fmt, height = 220 }: { data: { name: string; value: number }[]; fmt: (v: number) => string; height?: number }) {
  return (
    <div role="img" aria-label="Bar chart" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ left: 8, right: 16 }}>
          <CartesianGrid horizontal={false} />
          <XAxis type="number" tickFormatter={fmt} tickLine={false} axisLine={false} />
          <YAxis type="category" dataKey="name" width={110} tickLine={false} axisLine={false} />
          <Tooltip formatter={(v: number) => fmt(v)} contentStyle={{ background: "var(--raised)", border: "1px solid var(--line)", fontSize: 12 }} cursor={{ fill: "var(--line)", opacity: 0.4 }} />
          <Bar dataKey="value" fill="var(--s1)" radius={[0, 4, 4, 0]} barSize={14} isAnimationActive={false}>
            {data.map((_, i) => <Cell key={i} fill="var(--s1)" />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Grouped columns for a few named series (legend always shown). */
export function GroupedBars({ data, series, fmt, height = 240 }: { data: Record<string, any>[]; series: { key: string; name: string }[]; fmt: (v: number) => string; height?: number }) {
  return (
    <div role="img" aria-label="Grouped bar chart" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ left: 4, right: 8 }}>
          <CartesianGrid vertical={false} />
          <XAxis dataKey="name" tickLine={false} axisLine={false} />
          <YAxis tickFormatter={fmt} tickLine={false} axisLine={false} width={48} />
          <Tooltip formatter={(v: number) => fmt(v)} contentStyle={{ background: "var(--raised)", border: "1px solid var(--line)", fontSize: 12 }} cursor={{ fill: "var(--line)", opacity: 0.4 }} />
          <Legend verticalAlign="top" height={26} formatter={(v) => <span style={{ color: "var(--ink2)", fontSize: 11 }}>{v}</span>} />
          {series.map((s, i) => <Bar key={s.key} dataKey={s.key} name={s.name} fill={CAT[i]} radius={[4, 4, 0, 0]} barSize={16} isAnimationActive={false} />)}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
