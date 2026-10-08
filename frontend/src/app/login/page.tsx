"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useAuth } from "@/lib/auth";
import { Button, Card, Input, Label } from "@/components/ui";

const DEMO = ["admin", "planner", "steward", "viewer", "rep.amer", "rep.emea", "rep.apac"];

export default function Login() {
  const { login } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("planner@demo.local");
  const [password, setPassword] = useState("demo1234");
  const [err, setErr] = useState<string>();
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setErr(undefined);
    try { await login(email, password); router.replace("/dashboard"); } catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  };
  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm p-6">
        <div className="mb-1 rounded bg-warn/30 px-2 py-1 text-center text-xs font-semibold">[SYNTHETIC DEMO MODE]</div>
        <h1 className="mt-3 text-lg font-semibold">OEM Revenue Forecast</h1>
        <p className="mb-4 text-sm text-ink2">Sign in to continue</p>
        <form onSubmit={submit} className="space-y-3">
          <div><Label>Email</Label><Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="username" /></div>
          <div><Label>Password</Label><Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required autoComplete="current-password" /></div>
          {err && <div role="alert" className="text-sm text-crit">{err}</div>}
          <Button className="w-full" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</Button>
        </form>
        <p className="mt-4 text-xs text-ink2">Demo users (password <code>demo1234</code>):{" "}
          {DEMO.map((d) => <button key={d} type="button" className="mr-2 underline" onClick={() => setEmail(`${d}@demo.local`)}>{d}</button>)}
        </p>
      </Card>
    </div>
  );
}
