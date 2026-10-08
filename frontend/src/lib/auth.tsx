"use client";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { post, setToken } from "./api";
import type { Role, User } from "./types";

interface Ctx { user: User | null; ready: boolean; login: (e: string, p: string) => Promise<void>; logout: () => void; can: (...r: Role[]) => boolean }
const AuthCtx = createContext<Ctx>(null as unknown as Ctx);
const KEY = "oem.session";

const safe = {
  get: () => { try { return localStorage.getItem(KEY); } catch { return null; } },
  set: (v: string) => { try { localStorage.setItem(KEY, v); } catch { /* storage blocked */ } },
  clear: () => { try { localStorage.removeItem(KEY); } catch { /* storage blocked */ } },
};

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const router = useRouter();

  useEffect(() => {
    const raw = safe.get();
    if (raw) {
      try { const s = JSON.parse(raw); setToken(s.token); setUser(s.user); } catch { safe.clear(); }
    }
    setReady(true);
  }, []);

  const logout = useCallback(() => { setToken(null); setUser(null); safe.clear(); router.replace("/login"); }, [router]);
  useEffect(() => { const h = () => logout(); window.addEventListener("auth:expired", h); return () => window.removeEventListener("auth:expired", h); }, [logout]);

  const login = useCallback(async (email: string, password: string) => {
    const r = await post<{ access_token: string; user: User }>("/auth/login", { email, password });
    setToken(r.access_token); setUser(r.user); safe.set(JSON.stringify({ token: r.access_token, user: r.user }));
  }, []);

  const can = useCallback((...roles: Role[]) => !!user && (user.role === "admin" || roles.includes(user.role)), [user]);
  return <AuthCtx.Provider value={{ user, ready, login, logout, can }}>{children}</AuthCtx.Provider>;
}
export const useAuth = () => useContext(AuthCtx);
