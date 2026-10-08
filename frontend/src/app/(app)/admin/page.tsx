"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post, put } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useRun } from "@/lib/run-context";
import { useWorkspace } from "@/lib/workspace-context";
import type { Dq, Drift, Job, Setting, User } from "@/lib/types";
import { Badge, Button, Card, CardHeader, Empty, ErrorBox, Input, Label, PageHeader, PageIntro, Select, Spinner, Table, Tabs, Td, Th } from "@/components/ui";
import { fmtNum } from "@/lib/utils";
import { JobsPanel } from "@/components/jobs-panel";

function Quality() {
  const { runId } = useRun();
  const dq = useQuery({ queryKey: ["dq"], queryFn: () => get<Dq[]>("/admin/dq") });
  const drift = useQuery({ queryKey: ["drift", runId], queryFn: () => get<Drift[]>("/admin/drift", { run_id: runId }), enabled: !!runId });
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card><CardHeader title="Data-quality gate" sub="Blocking errors prevent a forecast run." help="Checks such as missing months, duplicates and a large unmapped share. A FAIL with severity ERROR stops the forecast until fixed; a WARN is shown but does not stop it. Run the check again from Jobs → Check data quality." />
        {dq.isLoading ? <Spinner /> : !dq.data?.length ? <Empty>No checks yet — run_dq.</Empty> : (
          <Table><thead><tr><Th>Check</Th><Th>Severity</Th><Th>Result</Th><Th>Message</Th></tr></thead>
            <tbody>{dq.data.map((c) => <tr key={c.check_name}><Td>{c.check_name}</Td><Td>{c.severity}</Td><Td><Badge tone={c.passed ? "good" : c.severity === "ERROR" ? "crit" : "warn"}>{c.passed ? "PASS" : "FAIL"}</Badge></Td><Td className="text-xs">{c.message}</Td></tr>)}</tbody></Table>)}
      </Card>
      <Card><CardHeader title="Drift" sub="Has recent data or accuracy moved away from the past?" help="FEATURE drift compares the last 12 months of demand with the earlier history, using PSI (population stability index). Above 0.25 is flagged as BREACHED: demand looks different from what the models learned. Error drift compares live forecast error with the error seen in backtesting." />
        {drift.isLoading ? <Spinner /> : !drift.data?.length ? <Empty>No drift metrics for this run.</Empty> : (
          <Table><thead><tr><Th>Kind</Th><Th>Name</Th><Th className="text-right">Value</Th><Th className="text-right">Threshold</Th><Th>Status</Th></tr></thead>
            <tbody>{drift.data.map((d, i) => <tr key={i}><Td>{d.kind}</Td><Td>{d.name}</Td><Td className="text-right tabular-nums">{fmtNum(d.value, 3)}</Td><Td className="text-right tabular-nums">{fmtNum(d.threshold, 3)}</Td><Td><Badge tone={d.breached ? "serious" : "good"}>{d.breached ? "BREACHED" : "OK"}</Badge></Td></tr>)}</tbody></Table>)}
      </Card>
    </div>
  );
}

function Settings() {
  const { can } = useAuth(); const qc = useQueryClient();
  const q = useQuery({ queryKey: ["settings"], queryFn: () => get<Setting[]>("/admin/settings") });
  const [edit, setEdit] = useState<Record<string, string>>({});
  const m = useMutation({
    mutationFn: ({ key, raw }: { key: string; raw: string }) => { let value: unknown = raw; try { value = JSON.parse(raw); } catch { /* keep string */ } return put(`/admin/settings/${key}`, { value }); },
    onSuccess: (_d, v) => { setEdit(({ [v.key]: _, ...rest }) => rest); qc.invalidateQueries({ queryKey: ["settings"] }); },
  });
  return (
    <Card><CardHeader title="Settings" sub="Stored in the database; every change is audit-logged." />
      {m.error && <div role="alert" className="p-3 text-sm text-crit">{(m.error as Error).message}</div>}
      {q.isLoading ? <Spinner /> : (
        <Table><thead><tr><Th>Key</Th><Th>Value (JSON)</Th><Th>Description</Th><Th /></tr></thead>
          <tbody>{q.data?.map((s) => (
            <tr key={s.key}><Td className="font-mono text-xs">{s.key}</Td>
              <Td><Input className="h-8 min-w-[160px] font-mono text-xs" disabled={!can("admin")} value={edit[s.key] ?? JSON.stringify(s.value)} onChange={(e) => setEdit({ ...edit, [s.key]: e.target.value })} /></Td>
              <Td className="text-xs text-ink2">{s.description}</Td><Td>{edit[s.key] != null && <Button size="sm" onClick={() => m.mutate({ key: s.key, raw: edit[s.key] })}>Save</Button>}</Td></tr>
          ))}</tbody></Table>)}
    </Card>
  );
}

function Users() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["users"], queryFn: () => get<User[]>("/admin/users") });
  const [f, setF] = useState({ email: "", full_name: "", password: "", role: "viewer" });
  const m = useMutation({ mutationFn: () => post("/admin/users", f), onSuccess: () => { qc.invalidateQueries({ queryKey: ["users"] }); setF({ email: "", full_name: "", password: "", role: "viewer" }); } });
  return (
    <Card><CardHeader title="Users" />
      {q.isLoading ? <Spinner /> : q.error ? <ErrorBox error={q.error} /> : (
        <Table><thead><tr><Th>Email</Th><Th>Name</Th><Th>Role</Th><Th>Scope</Th></tr></thead>
          <tbody>{q.data?.map((u) => <tr key={u.email}><Td>{u.email}</Td><Td>{u.full_name}</Td><Td><Badge>{u.role}</Badge></Td><Td className="text-xs">{[...(u.scope_oems ?? []), ...(u.scope_regions ?? [])].join(", ") || "all"}</Td></tr>)}</tbody></Table>)}
      <form className="grid grid-cols-2 items-end gap-2 border-t p-3 md:grid-cols-5" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
        <div><Label>Email</Label><Input type="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></div>
        <div><Label>Name</Label><Input required value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></div>
        <div><Label>Password</Label><Input type="password" required minLength={8} value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></div>
        <div><Label>Role</Label><Select className="w-full" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>{["viewer", "sales_rep", "steward", "planner", "admin"].map((r) => <option key={r}>{r}</option>)}</Select></div>
        <Button disabled={m.isPending}>Add user</Button>
        {m.error && <div role="alert" className="col-span-full text-sm text-crit">{(m.error as Error).message}</div>}
      </form>
    </Card>
  );
}

export default function AdminPage() {
  const { can, user } = useAuth(); const { current } = useWorkspace(); const router = useRouter();
  useEffect(() => { if (user && user.role !== "admin") router.replace("/workspaces"); }, [user, router]);
  const [tab, setTab] = useState<"jobs" | "quality" | "settings" | "users">("jobs");
  const tabs = [{ id: "jobs" as const, label: "Jobs", hint: "Run background tasks (generate data, map customers, forecast, recompute risk) and see what has run." }, { id: "quality" as const, label: "Data quality & drift", hint: "Health checks on the data, and signs that recent data or accuracy has moved away from what the models were tested on." }, { id: "settings" as const, label: "Settings", hint: "Tunable values for this workspace, such as forecast horizon, matching thresholds and approval rules. Every change is written to the audit log." }, ...(can("admin") ? [{ id: "users" as const, label: "Users" }] : [])];
  if (user?.role !== "admin") return null;
  return (
    <div>
      <PageHeader title="Admin" sub={<>Users are platform-wide. Jobs, data quality, drift and settings apply to the workspace <b>{current?.name ?? "—"}</b> (change it with the workspace selector in the top bar).</>} />
      <div className="mb-4"><Tabs value={tab} onChange={setTab} tabs={tabs} /></div>
      {tab === "jobs" && <JobsPanel />}{tab === "quality" && <Quality />}{tab === "settings" && <Settings />}{tab === "users" && <Users />}
    </div>
  );
}
