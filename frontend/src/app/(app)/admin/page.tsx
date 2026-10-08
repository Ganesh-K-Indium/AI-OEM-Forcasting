"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post, put } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useRun } from "@/lib/run-context";
import { useWorkspace } from "@/lib/workspace-context";
import type { Dq, Drift, Job, Setting, User } from "@/lib/types";
import { Badge, Button, Card, CardHeader, Empty, ErrorBox, Input, Label, PageHeader, Select, Spinner, Table, Tabs, Td, Th } from "@/components/ui";
import { fmtNum, fmtTs } from "@/lib/utils";

const JOB_TYPES = ["seed_demo", "import_dataset", "run_forecast", "mapping_pipeline", "materialize", "refresh_risk", "compute_fva", "run_dq"];
const jobTone = (s: string) => (s === "SUCCESS" ? "good" : s === "FAILED" ? "crit" : s === "RUNNING" ? "info" : "neutral") as "good" | "crit" | "info" | "neutral";

function Jobs() {
  const { can } = useAuth(); const qc = useQueryClient(); const { runId } = useRun(); const { current } = useWorkspace(); const seedable = current?.kind === "synthetic";
  const q = useQuery({ queryKey: ["jobs"], queryFn: () => get<Job[]>("/admin/jobs"), refetchInterval: 30_000 });
  const [type, setType] = useState("run_forecast"); const [fast, setFast] = useState(true);
  const m = useMutation({
    mutationFn: () => post<Job>("/admin/jobs", { job_type: type, params: type === "seed_demo" ? { fast, replay_cycles: fast ? 2 : 6 } : type === "run_forecast" ? { mode: fast ? "fast" : "full" } : {} }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
  const done = q.data?.some((j) => j.state === "SUCCESS");
  return (
    <Card>
      <CardHeader title="Jobs" sub="Long-running work executes off the request path (Celery worker or job thread)."
        right={can("planner") && <div className="flex items-center gap-2"><Select aria-label="Job type" value={type} onChange={(e) => setType(e.target.value)} >{JOB_TYPES.filter((t) => t !== "import_dataset" && (t !== "seed_demo" || (can("admin") && seedable))).map((t) => <option key={t}>{t}</option>)}</Select>
          <label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={fast} onChange={(e) => setFast(e.target.checked)} />fast</label>
          <Button disabled={m.isPending} onClick={() => m.mutate()}>Run</Button></div>} />
      {m.error && <div role="alert" className="p-3 text-sm text-crit">{(m.error as Error).message}</div>}
      {!done && !q.data?.length && <p className="p-3 text-sm text-ink2">Fresh install? Choose <b>seed_demo</b> (fast) to generate synthetic data and a first forecast run{runId ? "" : " — none exists yet"}.</p>}
      {q.isLoading ? <Spinner /> : q.error ? <ErrorBox error={q.error} /> : (
        <Table><thead><tr><Th>Type</Th><Th>State</Th><Th>Progress</Th><Th>Message</Th><Th>Created</Th><Th>By</Th></tr></thead>
          <tbody>{q.data?.map((j) => (
            <tr key={j.id}><Td>{j.job_type}</Td><Td><Badge tone={jobTone(j.state)}>{j.state}</Badge></Td>
              <Td className="w-40"><div className="h-2 w-full rounded bg-line" role="progressbar" aria-valuenow={Math.round(j.progress * 100)} aria-valuemin={0} aria-valuemax={100}><div className="h-2 rounded bg-brand" style={{ width: `${j.progress * 100}%` }} /></div></Td>
              <Td className="max-w-[320px] text-xs">{j.error ? <details><summary className="cursor-pointer text-crit">Failed</summary><pre className="whitespace-pre-wrap">{j.error}</pre></details> : j.message}</Td>
              <Td className="whitespace-nowrap text-xs">{fmtTs(j.created_at)}</Td><Td className="text-xs">{j.created_by}</Td></tr>
          ))}</tbody></Table>
      )}
    </Card>
  );
}

function Quality() {
  const { runId } = useRun();
  const dq = useQuery({ queryKey: ["dq"], queryFn: () => get<Dq[]>("/admin/dq") });
  const drift = useQuery({ queryKey: ["drift", runId], queryFn: () => get<Drift[]>("/admin/drift", { run_id: runId }), enabled: !!runId });
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card><CardHeader title="Data-quality gate" sub="Blocking errors prevent a forecast run." />
        {dq.isLoading ? <Spinner /> : !dq.data?.length ? <Empty>No checks yet — run_dq.</Empty> : (
          <Table><thead><tr><Th>Check</Th><Th>Severity</Th><Th>Result</Th><Th>Message</Th></tr></thead>
            <tbody>{dq.data.map((c) => <tr key={c.check_name}><Td>{c.check_name}</Td><Td>{c.severity}</Td><Td><Badge tone={c.passed ? "good" : c.severity === "ERROR" ? "crit" : "warn"}>{c.passed ? "PASS" : "FAIL"}</Badge></Td><Td className="text-xs">{c.message}</Td></tr>)}</tbody></Table>)}
      </Card>
      <Card><CardHeader title="Drift" sub="Feature PSI and forecast-error drift vs thresholds." />
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
  const { can } = useAuth(); const { current } = useWorkspace();
  const [tab, setTab] = useState<"jobs" | "quality" | "settings" | "users">("jobs");
  const tabs = [{ id: "jobs" as const, label: "Jobs" }, { id: "quality" as const, label: "Data quality & drift" }, { id: "settings" as const, label: "Settings" }, ...(can("admin") ? [{ id: "users" as const, label: "Users" }] : [])];
  return (
    <div>
      <PageHeader title="Admin" sub={<>Users are platform-wide. Jobs, data quality, drift and settings apply to the workspace <b>{current?.name ?? "—"}</b> (switch it on the Workspaces page).</>} />
      <div className="mb-4"><Tabs value={tab} onChange={setTab} tabs={tabs} /></div>
      {tab === "jobs" && <Jobs />}{tab === "quality" && <Quality />}{tab === "settings" && <Settings />}{tab === "users" && <Users />}
    </div>
  );
}
