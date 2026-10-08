import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export const cn = (...i: ClassValue[]) => twMerge(clsx(i));

export const fmtUsd = (v: number | null | undefined, compact = true) => {
  if (v == null || Number.isNaN(v)) return "—";
  const a = Math.abs(v);
  if (compact) {
    if (a >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
    if (a >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
    if (a >= 1e3) return `$${(v / 1e3).toFixed(0)}k`;
  }
  return `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
};
export const fmtNum = (v: number | null | undefined, d = 1) =>
  v == null || Number.isNaN(v) ? "—" : v.toLocaleString(undefined, { maximumFractionDigits: d });
export const fmtPct = (v: number | null | undefined, d = 1) => (v == null || Number.isNaN(v) ? "—" : `${(v * 100).toFixed(d)}%`);
export const fmtMonth = (s: string) => {
  const d = new Date(s + "T00:00:00");
  return d.toLocaleDateString(undefined, { month: "short", year: "2-digit" });
};
/** The API sends UTC timestamps without a zone suffix; without "Z" the browser would read them as local time. */
export const parseTs = (s: string) => new Date(/(Z|[+-]\d\d:?\d\d)$/.test(s) ? s : `${s}Z`);
export const fmtTs = (s: string) => parseTs(s).toLocaleString();
