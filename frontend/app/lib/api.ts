// Same-origin by default (proxied by app/api/[...path]/route.ts). Set NEXT_PUBLIC_API_BASE_URL only
// to bypass the proxy and call the API directly.
const API = (process.env.NEXT_PUBLIC_API_BASE_URL || "").replace(/\/$/, "");

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${path.startsWith("/sim") ? "" : API}${path}`, {
      ...init, headers: { "Content-Type": "application/json", ...(init?.headers || {}) }, cache: "no-store",
    });
  } catch {
    throw new Error("Cannot reach the control plane. Check that the API server is running.");
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = (payload as { detail?: unknown }).detail;
    throw new Error(typeof detail === "string" ? detail : Array.isArray(detail) ? "The request was not valid. Check the form fields." : `Request failed (${response.status})`);
  }
  return payload as T;
}

export const post = <T,>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const put = <T,>(path: string, body: unknown) => api<T>(path, { method: "PUT", body: JSON.stringify(body) });
