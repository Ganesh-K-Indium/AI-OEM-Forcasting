const BASE = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");
const PREFIX = "/api/v1";

let token: string | null = null;
let workspace: string | null = null;
export const setToken = (t: string | null) => { token = t; };
/** Every request carries the active workspace (X-Workspace header); the API serves that workspace's schema only. */
export const setWorkspace = (slug: string | null) => { workspace = slug; };
export const getToken = () => token;

export class ApiError extends Error {
  constructor(public status: number, message: string, public body?: unknown) { super(message); }
}

type Q = Record<string, string | number | boolean | null | undefined>;
const qs = (q?: Q) => {
  if (!q) return "";
  const p = new URLSearchParams();
  Object.entries(q).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== "") p.set(k, String(v)); });
  const s = p.toString();
  return s ? `?${s}` : "";
};

export async function api<T>(method: string, path: string, opts: { q?: Q; body?: unknown; root?: boolean } = {}): Promise<T> {
  const url = `${BASE}${opts.root ? "" : PREFIX}${path}${qs(opts.q)}`;
  const res = await fetch(url, {
    method,
    headers: {
      ...(opts.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(workspace ? { "X-Workspace": workspace } : {}),
    },
    body: opts.body === undefined ? undefined : opts.body instanceof FormData ? opts.body : JSON.stringify(opts.body),
  });
  if (res.status === 401 && typeof window !== "undefined" && !path.startsWith("/auth/login")) {
    window.dispatchEvent(new Event("auth:expired"));
  }
  if (!res.ok) {
    let body: any = null;
    try { body = await res.json(); } catch { /* non-JSON */ }
    const d = body?.detail;
    const msg = typeof d === "string" ? d : Array.isArray(d) ? d.map((x: any) => x.msg).join("; ") : d?.message || res.statusText;
    throw new ApiError(res.status, msg, body);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const get = <T,>(path: string, q?: Q) => api<T>("GET", path, { q });
export const post = <T,>(path: string, body?: unknown, q?: Q) => api<T>("POST", path, { body, q });
export const put = <T,>(path: string, body?: unknown) => api<T>("PUT", path, { body });
export const patch = <T,>(path: string, body?: unknown) => api<T>("PATCH", path, { body });
export const del = <T,>(path: string) => api<T>("DELETE", path);
export const upload = <T,>(path: string, file: File, q?: Q) => { const f = new FormData(); f.append("file", file); return api<T>("POST", path, { body: f, q }); };
/** Download a protected file (templates) with the auth + workspace headers. */
export async function download(path: string, filename: string) {
  const res = await fetch(`${BASE}${PREFIX}${path}`, { headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(workspace ? { "X-Workspace": workspace } : {}) } });
  if (!res.ok) throw new ApiError(res.status, res.statusText);
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a"); a.href = url; a.download = filename; a.click(); URL.revokeObjectURL(url);
}
