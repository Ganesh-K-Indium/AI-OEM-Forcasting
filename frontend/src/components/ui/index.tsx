"use client";
import * as Dialog from "@radix-ui/react-dialog";
import { X, AlertTriangle, CheckCircle2, OctagonAlert, TriangleAlert, Info } from "lucide-react";
import { cn } from "@/lib/utils";
import React from "react";

export function Button({ variant = "primary", size = "md", className, ...p }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" | "outline" | "danger"; size?: "sm" | "md" }) {
  const v = { primary: "bg-brand text-white hover:opacity-90", ghost: "hover:bg-line/60 text-ink", outline: "border border-line bg-raised hover:bg-line/50 text-ink", danger: "bg-crit text-white hover:opacity-90" }[variant];
  return <button {...p} className={cn("inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition disabled:cursor-not-allowed disabled:opacity-50", size === "sm" ? "h-7 px-2.5 text-xs" : "h-9 px-3.5 text-sm", v, className)} />;
}
export const Card = ({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) => <div {...p} className={cn("rounded-lg border bg-raised", className)} />;
export const CardHeader = ({ title, sub, right }: { title: React.ReactNode; sub?: React.ReactNode; right?: React.ReactNode }) => (
  <div className="flex items-start justify-between gap-3 border-b px-4 py-3">
    <div><h3 className="text-sm font-semibold">{title}</h3>{sub && <p className="mt-0.5 text-xs text-ink2">{sub}</p>}</div>{right}
  </div>
);
export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(function Input({ className, ...p }, ref) {
  return <input ref={ref} {...p} className={cn("h-9 w-full rounded-md border bg-surface px-3 text-sm outline-none focus:ring-2 focus:ring-brand/40", className)} />;
});
export const Textarea = ({ className, ...p }: React.TextareaHTMLAttributes<HTMLTextAreaElement>) => <textarea {...p} className={cn("w-full rounded-md border bg-surface px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-brand/40", className)} />;
export const Select = ({ className, ...p }: React.SelectHTMLAttributes<HTMLSelectElement>) => <select {...p} className={cn("h-9 rounded-md border bg-surface px-2 text-sm outline-none focus:ring-2 focus:ring-brand/40", className)} />;
export const Label = ({ children, className }: { children: React.ReactNode; className?: string }) => <label className={cn("mb-1 block text-xs font-medium text-ink2", className)}>{children}</label>;

type Tone = "neutral" | "good" | "warn" | "serious" | "crit" | "info";
const toneCls: Record<Tone, string> = {
  neutral: "bg-line/70 text-ink2", info: "bg-brand/15 text-ink", good: "bg-good/15 text-ink", warn: "bg-warn/25 text-ink", serious: "bg-serious/25 text-ink", crit: "bg-crit/20 text-ink",
};
const toneIcon: Record<Tone, React.ReactNode> = {
  neutral: null, info: <Info size={11} />, good: <CheckCircle2 size={11} className="text-good" />, warn: <TriangleAlert size={11} className="text-warn" />,
  serious: <AlertTriangle size={11} className="text-serious" />, crit: <OctagonAlert size={11} className="text-crit" />,
};
export const Badge = ({ tone = "neutral", children, className }: { tone?: Tone; children: React.ReactNode; className?: string }) => (
  <span className={cn("inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium", toneCls[tone], className)}>{toneIcon[tone]}{children}</span>
);
export const sevTone = (s: string): Tone => ({ HIGH: "crit", MEDIUM: "warn", LOW: "info" } as Record<string, Tone>)[s] ?? "neutral";

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: string }[]; value: T; onChange: (v: T) => void }) {
  return (
    <div role="tablist" className="flex gap-1 border-b">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={value === t.id} onClick={() => onChange(t.id)}
          className={cn("-mb-px border-b-2 px-3 py-2 text-sm", value === t.id ? "border-brand font-semibold text-ink" : "border-transparent text-ink2 hover:text-ink")}>{t.label}</button>
      ))}
    </div>
  );
}

export function Sheet({ open, onOpenChange, title, children, wide }: { open: boolean; onOpenChange: (o: boolean) => void; title: string; children: React.ReactNode; wide?: boolean }) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/40" />
        <Dialog.Content aria-describedby={undefined} className={cn("fixed right-0 top-0 z-50 flex h-full w-full flex-col border-l bg-surface shadow-xl", wide ? "max-w-3xl" : "max-w-xl")}>
          <div className="flex items-center justify-between border-b px-4 py-3">
            <Dialog.Title className="text-sm font-semibold">{title}</Dialog.Title>
            <Dialog.Close className="rounded p-1 hover:bg-line/60" aria-label="Close"><X size={16} /></Dialog.Close>
          </div>
          <div className="flex-1 overflow-y-auto p-4">{children}</div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export const Table = ({ className, ...p }: React.TableHTMLAttributes<HTMLTableElement>) => <div className="overflow-x-auto"><table {...p} className={cn("w-full text-sm", className)} /></div>;
export const Th = ({ className, ...p }: React.ThHTMLAttributes<HTMLTableCellElement>) => <th {...p} className={cn("whitespace-nowrap border-b px-3 py-2 text-left text-xs font-medium text-ink2", className)} />;
export const Td = ({ className, ...p }: React.TdHTMLAttributes<HTMLTableCellElement>) => <td {...p} className={cn("border-b px-3 py-2 align-top", className)} />;

export const Spinner = () => <div className="flex items-center gap-2 p-6 text-sm text-ink2"><span className="h-3 w-3 animate-spin rounded-full border-2 border-brand border-t-transparent" />Loading…</div>;
export const ErrorBox = ({ error }: { error: unknown }) => (
  <div role="alert" className="m-4 flex items-start gap-2 rounded-md border border-crit/40 bg-crit/10 p-3 text-sm"><OctagonAlert size={16} className="mt-0.5 shrink-0 text-crit" /><span>{(error as Error)?.message ?? "Something went wrong"}</span></div>
);
export const Empty = ({ children }: { children: React.ReactNode }) => <div className="p-8 text-center text-sm text-ink2">{children}</div>;

export function Kpi({ label, value, sub, tone }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: Tone }) {
  return (
    <Card className="p-4">
      <div className="text-xs font-medium text-ink2">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
      {sub && <div className="mt-1 text-xs text-ink2">{tone && <Badge tone={tone} className="mr-1">{" "}</Badge>}{sub}</div>}
    </Card>
  );
}
export const PageHeader = ({ title, sub, right }: { title: string; sub?: React.ReactNode; right?: React.ReactNode }) => (
  <div className="mb-4 flex flex-wrap items-end justify-between gap-3"><div><h1 className="text-xl font-semibold">{title}</h1>{sub && <p className="text-sm text-ink2">{sub}</p>}</div><div className="flex items-center gap-2">{right}</div></div>
);

/** Explains why a feature is idle for this workspace and what data would switch it on. */
export const CapabilityNote = ({ title, children }: { title: string; children: React.ReactNode }) => (
  <div role="note" className="mb-4 flex items-start gap-2 rounded-md border border-brand/40 bg-brand/10 p-3 text-sm"><Info size={16} className="mt-0.5 shrink-0 text-brand" /><div><div className="font-medium">{title}</div><div className="text-xs text-ink2">{children}</div></div></div>
);
