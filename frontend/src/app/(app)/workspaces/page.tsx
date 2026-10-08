"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { Archive, Beaker, Database, FlaskConical, Plus, Table2, Trash2 } from "lucide-react";
import { del, patch, post } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useWorkspace } from "@/lib/workspace-context";
import type { Workspace, WorkspaceKind } from "@/lib/types";
import { Badge, Button, Card, Empty, ErrorBox, Input, Label, PageHeader, PageIntro, Sheet, Textarea } from "@/components/ui";
import { cn, fmtNum } from "@/lib/utils";

const KINDS: { id: WorkspaceKind; title: string; icon: typeof Beaker; text: string; best: string }[] = [
  { id: "synthetic", title: "Synthetic demo", icon: Beaker, text: "Generated ERP + CRM + backlog + capacity estate with known ground truth.", best: "Demos, training, regression tests" },
  { id: "custom", title: "Your own data", icon: Table2, text: "Upload a sales-history file (and optionally mapping, backlog, capacity) and map its columns.", best: "Real research on company data" },
  { id: "m5", title: "M5 benchmark", icon: FlaskConical, text: "Walmart retail benchmark: store → OEM, state → region, department → product.", best: "Validating forecasting accuracy on public data" },
];
const statusTone = (s: string) => ({ READY: "good", IMPORTING: "info", FAILED: "crit", EMPTY: "neutral", ARCHIVED: "neutral" } as const)[s as "READY"] ?? "neutral";

function Chips({ w }: { w: Workspace }) {
  const c = w.capabilities;
  const items: [string, boolean | undefined][] = [["Sales", c.has_sales], ["Forecast", c.has_forecast], ["CRM", c.has_crm], ["Backlog", c.has_backlog], ["Capacity", c.has_capacity]];
  return <div className="mt-3 flex flex-wrap gap-1.5">{items.map(([l, on]) => <Badge key={l} tone={on ? "good" : "neutral"} className={cn(!on && "opacity-60")}>{on ? "✓" : "–"} {l}</Badge>)}</div>;
}

export default function WorkspacesPage() {
  const { workspaces, current, select, refresh } = useWorkspace();
  const { can } = useAuth();
  const router = useRouter();
  const enter = (w: Workspace) => { select(w.slug); router.push(w.capabilities.has_forecast ? "/dashboard" : "/data"); };
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(""); const [kind, setKind] = useState<WorkspaceKind>("custom"); const [desc, setDesc] = useState("");
  const create = useMutation({
    mutationFn: () => post<Workspace>("/workspaces", { name, kind, description: desc || null }),
    onSuccess: (w) => { refresh(); setOpen(false); setName(""); setDesc(""); setTimeout(() => { select(w.slug); router.push("/data"); }, 400); },
  });
  const archive = useMutation({ mutationFn: (w: Workspace) => patch(`/workspaces/${w.id}`, { archived: true }), onSuccess: refresh });
  const remove = useMutation({ mutationFn: (w: Workspace) => del(`/workspaces/${w.id}`), onSuccess: refresh });
  const onDelete = (w: Workspace) => { if (window.confirm(`Delete workspace “${w.name}” and ALL its data, forecasts, overrides and audit log? This cannot be undone.`)) remove.mutate(w); };

  return (
    <div>
      <PageHeader title="Workspaces" sub="Each workspace is an isolated research context with its own data, mappings, forecasts, overrides, settings and audit trail."
        right={can("planner") && <Button onClick={() => setOpen(true)}><Plus size={14} />New workspace</Button>} />
      <PageIntro id="workspaces"
        what="A workspace is a separate, self-contained project with its own data, mappings, forecasts, overrides, settings and audit log. Nothing in one workspace affects another, so demo data and real data never mix."
        points={[
          ["Kinds", "Synthetic (generated demo data), M5 (a public retail benchmark) or Custom (your own files)."],
          ["Status", "EMPTY (no data yet), IMPORTING / SEEDING (a job is loading), READY (can be forecast), FAILED (see Admin → Jobs)."],
          ["Open workspace", "Switches into it. The workspace selector in the top bar changes it at any time."],
          ["Chips on each card", "Which kinds of data the workspace holds, which controls the features it has."],
        ]} />
      {(remove.error || archive.error) && <ErrorBox error={remove.error || archive.error} />}
      {workspaces.length === 0 ? <Card><Empty>No workspaces yet. Create one to start.</Empty></Card> : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {workspaces.map((w) => {
            const active = current?.id === w.id;
            const c = w.capabilities;
            return (
              <Card key={w.id} className={cn("flex flex-col p-4", active && "border-brand ring-1 ring-brand/40")}>
                <div className="flex items-start justify-between gap-2">
                  <div><h3 className="font-semibold">{w.name}</h3><p className="text-xs text-ink2">{w.description ?? "—"}</p></div>
                  <div className="flex flex-col items-end gap-1"><Badge tone={statusTone(w.status)}>{w.active_job ? `${w.active_job.type} ${Math.round(w.active_job.progress * 100)}%` : w.status}</Badge><Badge>{w.kind}</Badge></div>
                </div>
                <Chips w={w} />
                {c.has_sales && <p className="mt-3 text-xs text-ink2">{fmtNum(c.oems, 0)} OEMs · {fmtNum(c.regions, 0)} regions · {fmtNum(c.products, 0)} products · {c.first_month?.slice(0, 7)} → {c.last_month?.slice(0, 7)}</p>}
                <div className="mt-auto flex flex-wrap items-center gap-2 pt-4">
                  <Button size="sm" onClick={() => enter(w)}>Open workspace</Button>
                  <Button size="sm" variant="ghost" onClick={() => { select(w.slug); router.push("/data"); }}><Database size={13} />Data</Button>
                  {active && <Badge tone="info">Last opened</Badge>}
                  {can("planner") && <Button size="sm" variant="ghost" onClick={() => archive.mutate(w)} aria-label={`Archive ${w.name}`}><Archive size={13} />Archive</Button>}
                  {can("admin") && <Button size="sm" variant="ghost" className="text-crit" onClick={() => onDelete(w)} aria-label={`Delete ${w.name}`}><Trash2 size={13} />Delete</Button>}
                </div>
              </Card>
            );
          })}
        </div>
      )}

      <Sheet open={open} onOpenChange={setOpen} title="New workspace">
        <form className="space-y-5" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
          <div><Label>Name</Label><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Q4 channel research" required minLength={2} autoFocus /></div>
          <div>
            <Label>What will it hold?</Label>
            <div className="grid gap-2" role="radiogroup" aria-label="Workspace type">
              {KINDS.map((k) => (
                <button type="button" key={k.id} role="radio" aria-checked={kind === k.id} onClick={() => setKind(k.id)}
                  className={cn("flex gap-3 rounded-md border p-3 text-left transition hover:bg-line/40", kind === k.id && "border-brand bg-brand/10")}>
                  <k.icon size={18} className="mt-0.5 shrink-0 text-brand" />
                  <span><span className="block text-sm font-medium">{k.title}</span><span className="block text-xs text-ink2">{k.text}</span><span className="mt-1 block text-[11px] text-muted">Best for: {k.best}</span></span>
                </button>
              ))}
            </div>
          </div>
          <div><Label>Description (optional)</Label><Textarea rows={3} value={desc} onChange={(e) => setDesc(e.target.value)} /></div>
          {create.error && <ErrorBox error={create.error} />}
          <Button className="w-full" disabled={create.isPending || name.trim().length < 2}>{create.isPending ? "Creating…" : "Create workspace"}</Button>
          <p className="text-xs text-ink2">The workspace starts empty. Next you load data on the <b>Data</b> page: generate synthetic data, import files, or load M5.</p>
        </form>
      </Sheet>
    </div>
  );
}
