"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { del, get, post, put } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Account, Allocation, Candidate, Oem, Rule } from "@/lib/types";
import { useRun } from "@/lib/run-context";
import { Badge, Button, CapabilityNote, Card, CardHeader, Empty, ErrorBox, Input, Label, PageHeader, Select, Sheet, Spinner, Table, Tabs, Td, Th } from "@/components/ui";
import { fmtPct } from "@/lib/utils";

const RULE_TYPES = ["GLOBAL_DUNS", "DUNS_EXACT", "TAX_ID", "ERP_PARENT", "DOMAIN", "ALIAS_EXACT", "NAME_REGEX"];
const statusTone = (s: string) => (s === "MAPPED" ? "good" : s === "PENDING_REVIEW" ? "warn" : s === "NEEDS_ALLOCATION" ? "serious" : "crit") as "good" | "warn" | "serious" | "crit";

function Queue({ canEdit }: { canEdit: boolean }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["map-queue"], queryFn: () => get<Candidate[]>("/mapping/queue") });
  const m = useMutation({ mutationFn: (v: { c: Candidate; approve: boolean }) => post(`/mapping/${v.c.mapping_id}/review`, { approve: v.approve }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["map-queue"] }); qc.invalidateQueries({ queryKey: ["map-accounts"] }); } });
  return (
    <Card>
      <CardHeader title="Steward review queue" sub="Fuzzy matches below the auto-apply threshold (0.93). Look-alike names are never auto-applied." />
      {m.error && <div role="alert" className="p-3 text-sm text-crit">{(m.error as Error).message}</div>}
      {q.isLoading ? <Spinner /> : q.error ? <ErrorBox error={q.error} /> : !q.data?.length ? <Empty>Queue is empty.</Empty> : (
        <Table><thead><tr><Th>Account</Th><Th>Type</Th><Th>Suggested OEM</Th><Th>Region</Th><Th className="text-right">Confidence</Th><Th>Evidence</Th><Th /></tr></thead>
          <tbody>{q.data.map((c) => (
            <tr key={c.mapping_id}><Td className="font-medium">{c.account_name}</Td><Td className="text-xs">{c.account_type}</Td><Td>{c.suggested_oem}</Td><Td>{c.region_code}</Td>
              <Td className="text-right tabular-nums"><Badge tone={c.confidence >= 0.85 ? "info" : "warn"}>{fmtPct(c.confidence, 0)}</Badge></Td>
              <Td className="max-w-[320px] text-xs text-ink2">{c.source}{c.evidence && <details><summary className="cursor-pointer underline">Details</summary><pre className="whitespace-pre-wrap">{JSON.stringify(c.evidence, null, 1)}</pre></details>}</Td>
              <Td className="whitespace-nowrap">{canEdit && <><Button size="sm" disabled={m.isPending} onClick={() => m.mutate({ c, approve: true })}>Approve</Button> <Button size="sm" variant="outline" disabled={m.isPending} onClick={() => m.mutate({ c, approve: false })}>Reject</Button></>}</Td></tr>
          ))}</tbody></Table>
      )}
    </Card>
  );
}

function AllocationEditor({ acc, oems, onClose }: { acc: Account | null; oems: Oem[]; onClose: () => void }) {
  const qc = useQueryClient();
  const init: Allocation[] = acc?.mapped_to.length ? acc.mapped_to.map((x) => ({ oem_code: x.oem_code ?? x.oem, region_code: x.region_code ?? x.region, allocation_pct: x.allocation_pct ?? x.pct ?? 1 })) : [{ oem_code: oems[0]?.code ?? "", region_code: acc?.region_code ?? "AMER", allocation_pct: 1 }];
  const [rows, setRows] = useState<Allocation[]>(init);
  const total = rows.reduce((a, r) => a + (Number(r.allocation_pct) || 0), 0);
  const ok = Math.abs(total - 1) < 1e-6 && rows.every((r) => r.oem_code && r.region_code);
  const m = useMutation({ mutationFn: () => put(`/mapping/accounts/${acc!.id}`, { allocations: rows, reason: "steward edit" }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["map-accounts"] }); onClose(); } });
  const upd = (i: number, p: Partial<Allocation>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...p } : r)));
  return (
    <Sheet open={!!acc} onOpenChange={(o) => !o && onClose()} title={`Mapping · ${acc?.name ?? ""}`}>
      {acc && (
        <div className="space-y-3 text-sm">
          <p className="text-ink2">{acc.account_type} · {acc.country ?? "—"} · DUNS {acc.duns ?? "—"} · tax {acc.tax_id ?? "—"}. Distributors may split across OEMs; allocations must total 100%.</p>
          {rows.map((r, i) => (
            <div key={i} className="grid grid-cols-[1fr_90px_90px_auto] items-end gap-2">
              <div><Label>OEM</Label><Select className="w-full" value={r.oem_code} onChange={(e) => upd(i, { oem_code: e.target.value })}>{oems.map((o) => <option key={o.code} value={o.code}>{o.name}</option>)}</Select></div>
              <div><Label>Region</Label><Select className="w-full" value={r.region_code} onChange={(e) => upd(i, { region_code: e.target.value })}>{["AMER", "EMEA", "APAC"].map((x) => <option key={x}>{x}</option>)}</Select></div>
              <div><Label>Share %</Label><Input type="number" min="1" max="100" value={Math.round(r.allocation_pct * 100)} onChange={(e) => upd(i, { allocation_pct: Number(e.target.value) / 100 })} /></div>
              <Button variant="ghost" size="sm" aria-label="Remove" onClick={() => setRows(rows.filter((_, j) => j !== i))}>✕</Button>
            </div>
          ))}
          <Button variant="outline" size="sm" onClick={() => setRows([...rows, { oem_code: oems[0]?.code ?? "", region_code: "AMER", allocation_pct: 0.1 }])}>+ Add OEM</Button>
          <div className={ok ? "text-good" : "text-crit"}>Total {fmtPct(total, 0)} {ok ? "✓" : "— must equal 100%"}</div>
          {m.error && <div role="alert" className="text-crit">{(m.error as Error).message}</div>}
          <Button disabled={!ok || m.isPending} onClick={() => m.mutate()}>Save mapping</Button>
        </div>
      )}
    </Sheet>
  );
}

function Accounts({ canEdit, oems }: { canEdit: boolean; oems: Oem[] }) {
  const [status, setStatus] = useState(""); const [text, setText] = useState(""); const [sel, setSel] = useState<Account | null>(null);
  const q = useQuery({ queryKey: ["map-accounts", status, text], queryFn: () => get<Account[]>("/mapping/accounts", { status, q: text }) });
  return (
    <Card>
      <CardHeader title="Accounts" right={<div className="flex gap-2"><Input placeholder="Search name…" value={text} onChange={(e) => setText(e.target.value)} className="w-48" />
        <Select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">All</option>{["MAPPED", "PENDING_REVIEW", "UNMAPPED", "NEEDS_ALLOCATION"].map((s) => <option key={s}>{s}</option>)}</Select></div>} />
      {q.isLoading ? <Spinner /> : q.error ? <ErrorBox error={q.error} /> : (
        <Table><thead><tr><Th>Account</Th><Th>Type</Th><Th>Country</Th><Th>Maps to</Th><Th>Status</Th><Th /></tr></thead>
          <tbody>{q.data?.map((a) => (
            <tr key={a.id}><Td className="font-medium">{a.name}<div className="text-xs text-ink2">{a.erp_customer_id}</div></Td><Td className="text-xs">{a.account_type}</Td><Td>{a.country ?? "—"}</Td>
              <Td className="text-xs">{a.mapped_to.map((m, i) => <div key={i}>{m.oem_code ?? m.oem} · {m.region_code ?? m.region} · {fmtPct(m.allocation_pct ?? m.pct ?? 1, 0)}</div>)}{!a.mapped_to.length && "—"}</Td>
              <Td><Badge tone={statusTone(a.status)}>{a.status.replace(/_/g, " ")}</Badge></Td><Td>{canEdit && <Button size="sm" variant="outline" onClick={() => setSel(a)}>Edit</Button>}</Td></tr>
          ))}</tbody></Table>
      )}
      <AllocationEditor key={sel?.id} acc={sel} oems={oems} onClose={() => setSel(null)} />
    </Card>
  );
}

function Rules({ canEdit }: { canEdit: boolean }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["map-rules"], queryFn: () => get<Rule[]>("/mapping/rules") });
  const [f, setF] = useState({ name: "", rule_type: "ALIAS_EXACT", pattern: "", target_oem_code: "", priority: 100 });
  const inv = () => qc.invalidateQueries({ queryKey: ["map-rules"] });
  const add = useMutation({ mutationFn: () => post("/mapping/rules", { ...f, confidence: 1, enabled: true, target_oem_code: f.target_oem_code || null, pattern: f.pattern || null }), onSuccess: () => { inv(); setF({ ...f, name: "", pattern: "" }); } });
  const toggle = useMutation({ mutationFn: (r: Rule) => put(`/mapping/rules/${r.id}`, { ...r, enabled: !r.enabled }), onSuccess: inv });
  const rm = useMutation({ mutationFn: (id: number) => del(`/mapping/rules/${id}`), onSuccess: inv });
  return (
    <Card>
      <CardHeader title="Deterministic rules" sub="Evaluated by priority (lowest first) before the fuzzy matcher." />
      {q.isLoading ? <Spinner /> : (
        <Table><thead><tr><Th>Priority</Th><Th>Name</Th><Th>Type</Th><Th>Pattern</Th><Th>Target</Th><Th>Enabled</Th><Th /></tr></thead>
          <tbody>{q.data?.map((r) => <tr key={r.id}><Td>{r.priority}</Td><Td>{r.name}</Td><Td className="text-xs">{r.rule_type}</Td><Td className="max-w-[220px] truncate font-mono text-xs">{r.pattern ?? "—"}</Td><Td>{r.target_oem_code ?? "—"}</Td>
            <Td>{canEdit ? <input type="checkbox" aria-label="enabled" checked={r.enabled} onChange={() => toggle.mutate(r)} /> : r.enabled ? "yes" : "no"}</Td><Td>{canEdit && <Button size="sm" variant="ghost" onClick={() => rm.mutate(r.id)}>Delete</Button>}</Td></tr>)}</tbody></Table>
      )}
      {canEdit && (
        <form className="grid grid-cols-2 items-end gap-2 border-t p-3 md:grid-cols-6" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
          <div><Label>Name</Label><Input required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
          <div><Label>Type</Label><Select className="w-full" value={f.rule_type} onChange={(e) => setF({ ...f, rule_type: e.target.value })}>{RULE_TYPES.map((t) => <option key={t}>{t}</option>)}</Select></div>
          <div><Label>Pattern</Label><Input value={f.pattern} onChange={(e) => setF({ ...f, pattern: e.target.value })} /></div>
          <div><Label>Target OEM code</Label><Input value={f.target_oem_code} onChange={(e) => setF({ ...f, target_oem_code: e.target.value })} /></div>
          <div><Label>Priority</Label><Input type="number" value={f.priority} onChange={(e) => setF({ ...f, priority: Number(e.target.value) })} /></div>
          <Button disabled={add.isPending}>Add rule</Button>
          {add.error && <div role="alert" className="col-span-full text-sm text-crit">{(add.error as Error).message}</div>}
        </form>
      )}
    </Card>
  );
}

function Oems({ canEdit, oems }: { canEdit: boolean; oems: Oem[] }) {
  const qc = useQueryClient();
  const [alias, setAlias] = useState<Record<number, string>>({});
  const [ident, setIdent] = useState<Record<number, { t: string; v: string }>>({});
  const inv = () => qc.invalidateQueries({ queryKey: ["map-oems"] });
  const addAlias = useMutation({ mutationFn: (o: Oem) => post(`/mapping/oems/${o.id}/aliases`, { alias: alias[o.id] }), onSuccess: inv });
  const addId = useMutation({ mutationFn: (o: Oem) => post(`/mapping/oems/${o.id}/identifiers`, { id_type: ident[o.id]?.t ?? "DUNS", id_value: ident[o.id]?.v }), onSuccess: inv });
  const rmId = useMutation({ mutationFn: (id: number) => del(`/mapping/identifiers/${id}`), onSuccess: inv });
  return (
    <Card>
      <CardHeader title="OEM master" sub="Identifiers and aliases feed the deterministic rules and fuzzy matcher." />
      <div className="divide-y">{oems.map((o) => (
        <div key={o.id} className="p-3 text-sm">
          <div className="font-medium">{o.name} <span className="text-xs text-ink2">{o.code}</span></div>
          <div className="mt-1 flex flex-wrap gap-1">{o.identifiers.map((i) => <Badge key={i.id}>{i.id_type}: {i.id_value}{canEdit && <button aria-label="Remove identifier" className="ml-1" onClick={() => rmId.mutate(i.id)}>✕</button>}</Badge>)}{o.aliases.map((a) => <Badge key={a} tone="info">{a}</Badge>)}</div>
          {canEdit && <div className="mt-2 flex flex-wrap gap-2">
            <Input className="w-44" placeholder="New alias" value={alias[o.id] ?? ""} onChange={(e) => setAlias({ ...alias, [o.id]: e.target.value })} /><Button size="sm" variant="outline" disabled={!alias[o.id]} onClick={() => addAlias.mutate(o)}>Add alias</Button>
            <Select aria-label="Identifier type" value={ident[o.id]?.t ?? "DUNS"} onChange={(e) => setIdent({ ...ident, [o.id]: { t: e.target.value, v: ident[o.id]?.v ?? "" } })}>{["DUNS", "GLOBAL_DUNS", "TAX_ID", "ERP_PARENT_ID", "DOMAIN"].map((t) => <option key={t}>{t}</option>)}</Select>
            <Input className="w-40" placeholder="Value" value={ident[o.id]?.v ?? ""} onChange={(e) => setIdent({ ...ident, [o.id]: { t: ident[o.id]?.t ?? "DUNS", v: e.target.value } })} /><Button size="sm" variant="outline" disabled={!ident[o.id]?.v} onClick={() => addId.mutate(o)}>Add identifier</Button></div>}
        </div>))}</div>
    </Card>
  );
}

export default function MappingPage() {
  const { meta } = useRun();
  const { can } = useAuth(); const qc = useQueryClient();
  const canEdit = can("steward");
  const [tab, setTab] = useState<"queue" | "accounts" | "rules" | "oems">("queue");
  const oems = useQuery({ queryKey: ["map-oems"], queryFn: () => get<Oem[]>("/mapping/oems") });
  const job = useMutation({ mutationFn: (k: "run" | "restate") => post(`/mapping/${k}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }) });
  return (
    <div>
      {meta?.capabilities && meta.capabilities.accounts > 0 && meta.capabilities.accounts <= meta.capabilities.oems && (
        <CapabilityNote title="Every customer in this dataset is its own OEM">
          There are no distributors to resolve, so the review queue stays empty. Upload a customer → OEM mapping file on the Data page if some customers are distributors.
        </CapabilityNote>
      )}
      <PageHeader title="Mapping" sub="Sold-To → End Customer → OEM, effective-dated; steward-approved"
        right={canEdit && <><Button variant="outline" disabled={job.isPending} onClick={() => job.mutate("run")}>Run mapping</Button><Button variant="outline" disabled={job.isPending} onClick={() => job.mutate("restate")}>Restate history</Button></>} />
      {job.isSuccess && <div role="status" className="mb-3 rounded border border-good/40 bg-good/10 p-2 text-sm">Job submitted — track progress in Admin → Jobs.</div>}
      {job.error && <div role="alert" className="mb-3 text-sm text-crit">{(job.error as Error).message}</div>}
      <div className="mb-4"><Tabs value={tab} onChange={setTab} tabs={[{ id: "queue", label: "Review queue" }, { id: "accounts", label: "Accounts" }, { id: "rules", label: "Rules" }, { id: "oems", label: "OEM identifiers" }]} /></div>
      {tab === "queue" && <Queue canEdit={canEdit} />}{tab === "accounts" && <Accounts canEdit={canEdit} oems={oems.data ?? []} />}
      {tab === "rules" && <Rules canEdit={canEdit} />}{tab === "oems" && <Oems canEdit={canEdit} oems={oems.data ?? []} />}
    </div>
  );
}
