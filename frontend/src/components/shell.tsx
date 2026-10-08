"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { BarChart3, Gauge, GitMerge, LayoutDashboard, LineChart, LogOut, Moon, Settings, ShieldAlert, Scale, Sun } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { RunProvider, useRun } from "@/lib/run-context";
import { Badge, Select } from "@/components/ui";
import { cn, fmtMonth } from "@/lib/utils";

const NAV = [
  { href: "/dashboard", label: "Executive Dashboard", icon: LayoutDashboard },
  { href: "/explorer", label: "Forecast Explorer", icon: LineChart },
  { href: "/benchmark", label: "Model Benchmark", icon: BarChart3 },
  { href: "/risk", label: "Risk Center", icon: ShieldAlert },
  { href: "/governance", label: "Governance", icon: Scale },
  { href: "/mapping", label: "Mapping", icon: GitMerge },
  { href: "/admin", label: "Admin", icon: Settings },
];

function Topbar() {
  const { meta, runs, runId, setRunId } = useRun();
  const { user, logout } = useAuth();
  const [theme, setTheme] = useState<"light" | "dark" | "auto">("auto");
  useEffect(() => { try { const t = localStorage.getItem("oem.theme") as "light" | "dark" | null; if (t) setTheme(t); } catch { /* blocked */ } }, []);
  useEffect(() => {
    const el = document.documentElement;
    if (theme === "auto") el.removeAttribute("data-theme"); else el.setAttribute("data-theme", theme);
    try { theme === "auto" ? localStorage.removeItem("oem.theme") : localStorage.setItem("oem.theme", theme); } catch { /* blocked */ }
  }, [theme]);
  const isDark = theme === "dark" || (theme === "auto" && typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  return (
    <header className="sticky top-0 z-30 border-b bg-surface/95 backdrop-blur">
      {meta?.synthetic_mode && (
        <div role="status" className="bg-warn/30 px-4 py-1 text-center text-xs font-semibold tracking-wide">{meta.label || "[SYNTHETIC DEMO MODE]"} — all data is generated; accuracy shown is not customer evidence</div>
      )}
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-2">
        <div className="flex items-center gap-3 text-sm">
          <Gauge size={16} className="text-brand" />
          <span className="text-ink2">Run</span>
          <Select aria-label="Forecast run" value={runId ?? ""} onChange={(e) => setRunId(e.target.value)} className="max-w-[260px]">
            {runs.filter((r) => r.status === "SUCCEEDED" || r.status === "COMPLETED" || r.id === runId).map((r) => (
              <option key={r.id} value={r.id}>{fmtMonth(r.cycle_month)} · {r.kind} · {r.id.slice(0, 8)}{r.locked_at ? " 🔒" : ""}</option>
            ))}
          </Select>
          {meta?.cycle_status && <Badge tone={meta.cycle_status === "LOCKED" ? "good" : "info"}>{meta.cycle_status}</Badge>}
          {meta && <span className="hidden text-xs text-ink2 md:inline">{meta.currency} · {meta.units_label} · FX {meta.fx_policy}</span>}
        </div>
        <div className="flex items-center gap-2 text-sm">
          <button className="rounded p-1.5 hover:bg-line/60" aria-label="Toggle theme" onClick={() => setTheme(isDark ? "light" : "dark")}>{isDark ? <Sun size={16} /> : <Moon size={16} />}</button>
          {user && <span className="hidden text-ink2 sm:inline">{user.full_name} <Badge>{user.role}</Badge></span>}
          <button className="rounded p-1.5 hover:bg-line/60" aria-label="Sign out" onClick={logout}><LogOut size={16} /></button>
        </div>
      </div>
    </header>
  );
}

export function Shell({ children }: { children: React.ReactNode }) {
  const { user, ready } = useAuth();
  const router = useRouter();
  const path = usePathname();
  useEffect(() => { if (ready && !user) router.replace("/login"); }, [ready, user, router]);
  if (!ready || !user) return <div className="p-8 text-sm text-ink2">Loading…</div>;
  return (
    <RunProvider>
      <div className="flex min-h-screen">
        <nav aria-label="Primary" className="sticky top-0 hidden h-screen w-56 shrink-0 flex-col border-r bg-raised p-3 md:flex">
          <div className="mb-4 px-2 text-sm font-semibold">OEM Revenue Forecast</div>
          {NAV.map(({ href, label, icon: I }) => (
            <Link key={href} href={href} className={cn("mb-0.5 flex items-center gap-2 rounded-md px-2 py-2 text-sm", path.startsWith(href) ? "bg-brand/15 font-semibold" : "text-ink2 hover:bg-line/60")}>
              <I size={16} />{label}
            </Link>
          ))}
        </nav>
        <div className="flex min-w-0 flex-1 flex-col">
          <Topbar />
          <nav aria-label="Primary mobile" className="flex gap-1 overflow-x-auto border-b px-2 py-1 md:hidden">
            {NAV.map(({ href, label }) => <Link key={href} href={href} className={cn("whitespace-nowrap rounded px-2 py-1 text-xs", path.startsWith(href) ? "bg-brand/15 font-semibold" : "text-ink2")}>{label}</Link>)}
          </nav>
          <main className="flex-1 p-4 md:p-6">{children}</main>
        </div>
      </div>
    </RunProvider>
  );
}
