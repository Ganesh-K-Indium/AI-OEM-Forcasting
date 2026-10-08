"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Eye, EyeOff, GitMerge, Layers, Scale, ShieldAlert, LineChart } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { Button, Input, Label } from "@/components/ui";
import { cn } from "@/lib/utils";

const DEMO = [
  { id: "planner", role: "Planner", hint: "overrides, lock cycles" },
  { id: "admin", role: "Admin", hint: "everything, seeding" },
  { id: "steward", role: "Steward", hint: "mapping review" },
  { id: "viewer", role: "Viewer", hint: "read-only" },
  { id: "rep.amer", role: "Rep · AMER", hint: "scoped overrides" },
  { id: "rep.emea", role: "Rep · EMEA", hint: "scoped overrides" },
  { id: "rep.apac", role: "Rep · APAC", hint: "scoped overrides" },
];

const FEATURES = [
  { icon: Layers, title: "Units → ASP → revenue", text: "Forecast volume first, convert with a price and FX engine." },
  { icon: GitMerge, title: "Sold-To → OEM mapping", text: "Rules, fuzzy ML and steward review resolve distributors." },
  { icon: LineChart, title: "Coherent, calibrated forecasts", text: "MinT reconciliation with P10–P90 ranges that add up." },
  { icon: Scale, title: "Governed overrides", text: "Append-only changes, forecast value added, hash-chained audit." },
  { icon: ShieldAlert, title: "Backlog risk", text: "Coverage, supply bottlenecks and pipeline concentration." },
];

/** Decorative only: an illustrative fan shape, not data. */
function HeroChart() {
  const pts = [48, 52, 47, 55, 58, 54, 60, 63, 59, 66, 68, 65];
  const x = (i: number) => 20 + i * 24;
  const y = (v: number) => 120 - (v - 40) * 2.2;
  const hist = pts.slice(0, 8).map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join(" ");
  const fc = pts.slice(7).map((v, i) => `${i ? "L" : "M"}${x(i + 7)},${y(v)}`).join(" ");
  const upper = pts.slice(7).map((v, i) => `${x(i + 7)},${y(v + 2 + i * 1.6)}`);
  const lower = pts.slice(7).map((v, i) => `${x(i + 7)},${y(v - 2 - i * 1.6)}`).reverse();
  return (
    <svg viewBox="0 0 310 140" className="w-full" role="img" aria-label="Illustration of a forecast fan chart">
      {[0, 1, 2, 3].map((g) => <line key={g} x1="14" x2="300" y1={20 + g * 32} y2={20 + g * 32} stroke="var(--grid)" />)}
      <polygon points={[...upper, ...lower].join(" ")} fill="var(--s1)" fillOpacity="0.16" />
      <path d={hist} fill="none" stroke="var(--s1)" strokeWidth="2" />
      <path d={fc} fill="none" stroke="var(--s1)" strokeWidth="2" strokeDasharray="6 4" />
      {pts.slice(8).map((v, i) => <rect key={i} x={x(i + 8) - 4} y={y(v) + 28 - (i % 3) * 3} width="8" height={10 + (i % 3) * 3} rx="2" fill="var(--s3)" />)}
      <text x="20" y="136" fontSize="9" fill="var(--muted)">history</text>
      <text x="196" y="136" fontSize="9" fill="var(--muted)">forecast · P10–P90</text>
    </svg>
  );
}

export default function Login() {
  const { login } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("planner@demo.local");
  const [password, setPassword] = useState("demo1234");
  const [show, setShow] = useState(false);
  const [err, setErr] = useState<string>();
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setErr(undefined);
    try { await login(email, password); router.replace("/dashboard"); } catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  };
  return (
    <div className="grid min-h-screen lg:grid-cols-[1.15fr_1fr]">
      <section className="relative hidden flex-col justify-between overflow-hidden border-r bg-raised p-10 lg:flex" aria-label="About the platform">
        <div>
          <div className="mb-10 flex items-center gap-2 text-sm font-semibold"><span className="grid h-7 w-7 place-items-center rounded-md bg-brand text-white"><LineChart size={15} /></span>OEM Revenue Forecast</div>
          <h1 className="max-w-lg text-4xl font-semibold leading-tight">Know what each OEM will buy — and how sure you are.</h1>
          <p className="mt-4 max-w-lg text-ink2">One governed forecast across OEM, region and product. ERP history and CRM pipeline stay separate, the hierarchy always adds up, and every override is measured.</p>
          <div className="mt-8 max-w-lg rounded-lg border bg-surface p-4">
            <HeroChart />
            <p className="mt-1 text-[11px] text-muted">Illustration only — not real data.</p>
          </div>
        </div>
        <ul className="mt-8 grid max-w-2xl gap-x-6 gap-y-4 sm:grid-cols-2">
          {FEATURES.map(({ icon: I, title, text }) => (
            <li key={title} className="flex gap-3">
              <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-md bg-brand/15 text-brand"><I size={15} /></span>
              <div><div className="text-sm font-medium">{title}</div><div className="text-xs text-ink2">{text}</div></div>
            </li>
          ))}
        </ul>
      </section>

      <main className="flex items-center justify-center p-6">
        <div className="w-full max-w-sm">
          <div className="mb-6 flex items-center gap-2 text-sm font-semibold lg:hidden"><span className="grid h-7 w-7 place-items-center rounded-md bg-brand text-white"><LineChart size={15} /></span>OEM Revenue Forecast</div>
          <div role="status" className="mb-5 rounded-md bg-warn/30 px-3 py-1.5 text-center text-xs font-semibold tracking-wide">[SYNTHETIC DEMO MODE]</div>
          <h2 className="text-2xl font-semibold">Welcome back</h2>
          <p className="mb-6 mt-1 text-sm text-ink2">Sign in to your planning workspace.</p>
          <form onSubmit={submit} className="space-y-4">
            <div><Label>Email</Label><Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="username" autoFocus /></div>
            <div>
              <Label>Password</Label>
              <div className="relative">
                <Input type={show ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)} required autoComplete="current-password" className="pr-10" />
                <button type="button" onClick={() => setShow((s) => !s)} aria-label={show ? "Hide password" : "Show password"} className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-ink2 hover:bg-line/60">{show ? <EyeOff size={16} /> : <Eye size={16} />}</button>
              </div>
            </div>
            {err && <div role="alert" className="rounded-md border border-crit/40 bg-crit/10 px-3 py-2 text-sm">{err}</div>}
            <Button className="h-10 w-full" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</Button>
          </form>

          <div className="mt-8">
            <div className="mb-2 flex items-center gap-3 text-xs text-ink2"><span className="h-px flex-1 bg-line" />Try a demo role · password <code className="rounded bg-line/70 px-1">demo1234</code><span className="h-px flex-1 bg-line" /></div>
            <div className="grid grid-cols-2 gap-2">
              {DEMO.map((d) => {
                const active = email === `${d.id}@demo.local`;
                return (
                  <button key={d.id} type="button" onClick={() => { setEmail(`${d.id}@demo.local`); setPassword("demo1234"); }} aria-pressed={active}
                    className={cn("rounded-md border px-3 py-2 text-left transition hover:bg-line/50", active && "border-brand bg-brand/10")}>
                    <div className="text-sm font-medium">{d.role}</div><div className="text-[11px] text-ink2">{d.hint}</div>
                  </button>
                );
              })}
            </div>
          </div>
          <p className="mt-8 text-center text-[11px] text-muted">Demo data is generated. Accuracy shown is not customer evidence.</p>
        </div>
      </main>
    </div>
  );
}
