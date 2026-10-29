export const API_URL: string = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export type Identity = { userId: string; workspaceId: string };

export const USERS: Identity[] = [
  { userId: "admin_v", workspaceId: "ws_verified" },
  { userId: "member_v", workspaceId: "ws_verified" },
  { userId: "admin_u", workspaceId: "ws_unverified" },
  { userId: "member_u", workspaceId: "ws_unverified" },
];

export class ApiError extends Error {
  constructor(public status: number, message: string) {
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
    throw new ApiError(res.status, `${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}
