"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { BarChart3, Database, FolderKanban, Gauge, GitMerge, LayoutDashboard, LineChart, LogOut, Moon, Settings, ShieldAlert, Scale, Sun } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { RunProvider, useRun } from "@/lib/run-context";
import { WorkspaceProvider, useWorkspace } from "@/lib/workspace-context";
import { Badge, Button, Card, Select } from "@/components/ui";
import { cn, fmtMonth } from "@/lib/utils";

// Workspace-scoped pages live "inside" a workspace; Workspaces (the lobby) and Admin (users, platform) are global.
const WS_NAV = [
  { href: "/data", label: "Data", icon: Database },
  { href: "/dashboard", label: "Executive Dashboard", icon: LayoutDashboard },
  { href: "/explorer", label: "Forecast Explorer", icon: LineChart },
  { href: "/benchmark", label: "Model Benchmark", icon: BarChart3 },
  { href: "/risk", label: "Risk Center", icon: ShieldAlert },
  { href: "/governance", label: "Governance", icon: Scale },
  { href: "/mapping", label: "Mapping", icon: GitMerge },
];
// Only the workspace lobby is workspace-less. Admin has platform-wide parts (users) and workspace parts (jobs, DQ, settings),
// so it keeps the workspace switcher and the workspace navigation: no round trip through the lobby.
const isGlobal = (path: string) => path.startsWith("/workspaces");

const KIND_LABEL = { synthetic: "Synthetic", m5: "M5", custom: "Custom" } as const;

function Topbar() {
  const path = usePathname();
  const { meta, runs, runId, setRunId } = useRun();
  const { workspaces, current, select } = useWorkspace();
  const { user, logout } = useAuth();
  const [theme, setTheme] = useState<"light" | "dark">("light");
  useEffect(() => { try { if (localStorage.getItem("oem.theme") === "dark") setTheme("dark"); } catch { /* blocked */ } }, []);
  const choose = (t: "light" | "dark") => {
    setTheme(t);
    const el = document.documentElement;
    if (t === "dark") el.setAttribute("data-theme", "dark"); else el.removeAttribute("data-theme");
    try { t === "dark" ? localStorage.setItem("oem.theme", "dark") : localStorage.removeItem("oem.theme"); } catch { /* blocked */ }
  };
  const isDark = theme === "dark";
  return (
    <header className="sticky top-0 z-30 border-b bg-surface/95 backdrop-blur">
      {isGlobal(path) ? null : meta?.synthetic_mode ? (
        <div role="status" className="bg-warn/30 px-4 py-1 text-center text-xs font-semibold tracking-wide">{meta.label || "[SYNTHETIC DEMO MODE]"} — all data is generated; accuracy shown is not customer evidence</div>
      ) : meta?.workspace?.kind === "m5" ? (
        <div role="status" className="bg-brand/15 px-4 py-1 text-center text-xs font-semibold tracking-wide">{meta.label} — public retail benchmark (store → OEM, state → region, department → product); CRM, backlog and capacity features are off</div>
      ) : null}
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-2">
        <div className="flex flex-wrap items-center gap-3 text-sm">
          {isGlobal(path) ? <span className="text-ink2">All workspaces</span> : <>
          {path.startsWith("/admin") && <span className="font-medium">Admin ·</span>}<FolderKanban size={16} className="text-brand" />
          <Select aria-label="Workspace" value={current?.slug ?? ""} onChange={(e) => select(e.target.value)} className="max-w-[220px] font-medium">
            {workspaces.length === 0 && <option value="">No workspace</option>}
            {workspaces.map((w) => <option key={w.id} value={w.slug}>{w.name} · {KIND_LABEL[w.kind]}</option>)}
          </Select>
          <Gauge size={16} className="text-brand" />
          <span className="text-ink2">Run</span>
          <Select aria-label="Forecast run" value={runId ?? ""} onChange={(e) => setRunId(e.target.value)} className="max-w-[260px]">
            {runs.filter((r) => r.status === "SUCCEEDED" || r.status === "COMPLETED" || r.id === runId).map((r) => (
              <option key={r.id} value={r.id}>{fmtMonth(r.cycle_month)} · {r.kind} · {r.id.slice(0, 8)}{r.locked_at ? " 🔒" : ""}</option>
            ))}
          </Select>
          {meta?.cycle_status && <Badge tone={meta.cycle_status === "LOCKED" ? "good" : "info"}>{meta.cycle_status}</Badge>}
          {meta && <span className="hidden text-xs text-ink2 md:inline">{meta.currency} · {meta.units_label} · FX {meta.fx_policy}</span>}
          </>}
        </div>
        <div className="flex items-center gap-2 text-sm">
          <button className="rounded p-1.5 hover:bg-line/60" aria-label="Toggle theme" onClick={() => choose(isDark ? "light" : "dark")}>{isDark ? <Sun size={16} /> : <Moon size={16} />}</button>
          {user && <span className="hidden text-ink2 sm:inline">{user.full_name} <Badge>{user.role}</Badge></span>}
          <button className="rounded p-1.5 hover:bg-line/60" aria-label="Sign out" onClick={logout}><LogOut size={16} /></button>
        </div>
      </div>
    </header>
  );
}

const OPEN_PATHS = ["/workspaces", "/data", "/admin"];

/** Pages that need forecasts show a guided empty state until the active workspace has data. */
function Gate({ children }: { children: React.ReactNode }) {
  const { current } = useWorkspace();
  const path = usePathname();
  if (OPEN_PATHS.some((p) => path.startsWith(p))) return <>{children}</>;
  if (!current) return <EmptyState title="Create a workspace to get started" text="A workspace holds one dataset and everything derived from it — mappings, forecasts, overrides and audit trail." href="/workspaces" cta="Go to Workspaces" />;
  const c = current.capabilities;
  if (current.status === "IMPORTING" || current.active_job) {
    const j = current.active_job;
    return <EmptyState title="Loading data…" text={`${j?.message ?? "Working"} (${Math.round((j?.progress ?? 0) * 100)}%). This page fills in automatically.`} href="/data" cta="Watch progress" />;
  }
  if (!c?.has_forecast) {
    return <EmptyState title={`“${current.name}” has no forecast yet`} href="/data"
      cta={current.kind === "synthetic" ? "Generate synthetic data" : current.kind === "m5" ? "Load the M5 dataset" : "Import your data"}
      text={current.kind === "synthetic" ? "Generate the demo estate (ERP, CRM, backlog, capacity) and a first forecast." : current.kind === "m5" ? "Load the M5 benchmark files and forecast them." : "Upload a sales history file, map its columns and forecast it."} />;
  }
  return <>{children}</>;
}

function EmptyState({ title, text, href, cta }: { title: string; text: string; href: string; cta: string }) {
  return (
    <Card className="mx-auto mt-10 max-w-xl p-8 text-center">
      <h2 className="text-lg font-semibold">{title}</h2>
      <p className="mt-2 text-sm text-ink2">{text}</p>
      <Link href={href}><Button className="mt-5">{cta}</Button></Link>
    </Card>
  );
}

export function Shell({ children }: { children: React.ReactNode }) {
  const { user, ready } = useAuth();
  const router = useRouter();
  const path = usePathname();
  useEffect(() => { if (ready && !user) router.replace("/login"); }, [ready, user, router]);
  if (!ready || !user) return <div className="p-8 text-sm text-ink2">Loading…</div>;
  return <WorkspaceProvider><RunProvider><Frame>{children}</Frame></RunProvider></WorkspaceProvider>;
}

function NavLink({ href, label, icon: I, path }: { href: string; label: string; icon: typeof Database; path: string }) {
  return (
    <Link href={href} className={cn("mb-0.5 flex items-center gap-2 rounded-md px-2 py-2 text-sm", path.startsWith(href) ? "bg-brand/15 font-semibold" : "text-ink2 hover:bg-line/60")}>
      <I size={16} />{label}
    </Link>
  );
}

function Frame({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const { current } = useWorkspace();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const inside = !isGlobal(path) && !!current;
  return (
    <div className="flex min-h-screen">
      <nav aria-label="Primary" className="sticky top-0 hidden h-screen w-56 shrink-0 flex-col border-r bg-raised p-3 md:flex">
        <div className="mb-4 px-2 text-sm font-semibold">OEM Revenue Forecast</div>
        <NavLink href="/workspaces" label="Workspaces" icon={FolderKanban} path={inside ? "" : path} />
        {inside && current && (
          <div className="mb-2 mt-3 rounded-md border bg-surface p-2">
            <div className="px-1 text-[11px] uppercase tracking-wide text-ink2">Workspace</div>
            <div className="truncate px-1 pb-2 text-sm font-semibold" title={current.name}>{current.name}</div>
            {WS_NAV.map((n) => <NavLink key={n.href} {...n} path={path} />)}
          </div>
        )}
        {isAdmin && <div className="mt-auto border-t pt-2"><NavLink href="/admin" label="Admin" icon={Settings} path={path} /></div>}
      </nav>
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar />
        <nav aria-label="Primary mobile" className="flex gap-1 overflow-x-auto border-b px-2 py-1 md:hidden">
          {[{ href: "/workspaces", label: "Workspaces" }, ...(inside ? WS_NAV : []), ...(isAdmin ? [{ href: "/admin", label: "Admin" }] : [])].map(({ href, label }) =>
            <Link key={href} href={href} className={cn("whitespace-nowrap rounded px-2 py-1 text-xs", path.startsWith(href) ? "bg-brand/15 font-semibold" : "text-ink2")}>{label}</Link>)}
        </nav>
        <main className="flex-1 p-4 md:p-6"><Gate>{children}</Gate></main>
      </div>
    </div>
  );
}
