import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiFetch, type Identity } from "./api";

export type ReviewItem = {
  id: string;
  status: string;
  reasons: string[];
  created_at: string;
  held_reason_kind: "score" | "screening_unavailable" | "provider_malformed" | null;
};

const NOT_APPROVABLE = new Set(["screening_unavailable", "provider_malformed"]);

export function ReviewQueue({ who }: { who: Identity }) {
  const queryClient = useQueryClient();
  const key = ["review", who.userId, who.workspaceId];

  // Three distinct states. A failed request is an error, never an empty
  // queue: that is how moderators saw "Nothing to review" while 40 items waited.
  const query = useQuery({
    queryKey: key,
    queryFn: () => apiFetch<ReviewItem[]>("/review", who),
    refetchInterval: 5000,
  });

  const act = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "approve" | "reject" }) =>
      apiFetch(`/review/${id}/${action}`, who, { method: "POST" }),
    onSettled: () => queryClient.invalidateQueries({ queryKey: key }),
  });

  if (query.isPending) {
    return (
      <p role="status" aria-busy="true" style={{ color: "#666" }}>
        Loading review queue…
      </p>
    );
  }

  if (query.isError) {
    const status = query.error instanceof ApiError ? query.error.status : undefined;
    return (
      <div role="alert" style={{ border: "1px solid #c62828", background: "#fdecea", padding: "0.75rem", borderRadius: 6 }}>
        <strong>Could not load the review queue</strong>
        {status !== undefined ? ` (HTTP ${status})` : " (network error)"}. Items may be waiting.{" "}
        <button onClick={() => query.refetch()} disabled={query.isFetching}>
          Retry
        </button>
      </div>
    );
  }

  const items = query.data;
  if (items.length === 0) return <p>Nothing to review</p>;

  return (
    <>
      {act.isError && (
        <p role="alert" style={{ color: "#c62828" }}>
          {act.error instanceof ApiError && act.error.status === 409
            ? "That item cannot be approved: it was never screened."
            : `Action failed: ${act.error.message}`}
        </p>
      )}
    <table style={{ width: "100%", borderCollapse: "collapse" }}>
      <thead>
        <tr style={{ textAlign: "left" }}>
          <th>ID</th>
          <th>Created</th>
          <th>Reasons</th>
          <th>Held kind</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {items.map((item) => {
          const blocked = item.held_reason_kind !== null && NOT_APPROVABLE.has(item.held_reason_kind);
          return (
            <tr key={item.id} style={{ borderTop: "1px solid #ddd" }}>
              <td style={{ fontFamily: "monospace" }}>{item.id.slice(0, 8)}</td>
              <td>{new Date(item.created_at).toLocaleTimeString()}</td>
              <td>{item.reasons.join(", ")}</td>
              <td>{item.held_reason_kind}</td>
              <td>
                <button
                  disabled={blocked || act.isPending}
                  title={blocked ? "Not screened yet: re-screened automatically, cannot be approved" : undefined}
                  onClick={() => act.mutate({ id: item.id, action: "approve" })}
                >
                  Approve
                </button>{" "}
                <button disabled={act.isPending} onClick={() => act.mutate({ id: item.id, action: "reject" })}>
                  Reject
                </button>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
    </>
  );
}
