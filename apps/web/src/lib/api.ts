// Client for the Summer Hunter OS API. Same origin (/api), session cookie, CSRF header on writes.

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    headers: {
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(method !== "GET" ? { "X-SH-Request": "1" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (res.status === 401 && typeof window !== "undefined" && !path.startsWith("/auth/")) {
    // A full reload on purpose: it drops every cached query of the expired session.
    window.location.assign(new URL(`/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`, window.location.origin).href)
  }
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = typeof data?.detail === "string" ? data.detail : Array.isArray(data?.detail) ? data.detail[0]?.msg : null
    throw new ApiError(res.status, detail ?? `Request failed (${res.status})`)
  }
  return data as T
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
}

export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "" && v !== false) p.set(k, String(v))
  }
  const s = p.toString()
  return s ? `?${s}` : ""
}
