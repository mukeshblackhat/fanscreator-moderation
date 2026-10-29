export const API_URL: string = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
export const PROVIDER_URL: string = import.meta.env.VITE_PROVIDER_URL ?? "http://localhost:8001";

export type Identity = { userId: string; workspaceId: string; role?: "admin" | "member"; verified?: boolean };

export const USERS: Identity[] = [
  { userId: "admin_v", workspaceId: "ws_verified", role: "admin", verified: true },
  { userId: "member_v", workspaceId: "ws_verified", role: "member", verified: true },
  { userId: "admin_u", workspaceId: "ws_unverified", role: "admin", verified: false },
  { userId: "member_u", workspaceId: "ws_unverified", role: "member", verified: false },
];

export class ApiError extends Error {
  constructor(public status: number, message: string, public body?: unknown) {
    super(message);
  }
}

export async function apiFetch<T>(path: string, who: Identity, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-User-Id": who.userId,
      "X-Workspace-Id": who.workspaceId,
      ...init.headers,
    },
  });
  if (!res.ok) {
    // Never turn a failed request into an empty result: callers must see the error.
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = `${res.status}: ${typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail)}`;
      if (body?.reasons) detail = `${res.status}: ${body.reasons.join(", ")}`;
      throw new ApiError(res.status, detail, body);
    } catch (e) {
      if (e instanceof ApiError) throw e;
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}
