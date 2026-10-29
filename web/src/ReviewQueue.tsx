import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, type Identity } from "./api";

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

  // DELIBERATELY BROKEN (incident symptom 5): a failed request is swallowed
  // and rendered exactly like an empty queue. Moderators saw "Nothing to
  // review" while 40 items waited. Fixed in a later commit.
  const query = useQuery({
    queryKey: key,
    queryFn: async () => {
      try {
        return await apiFetch<ReviewItem[]>("/review", who);
      } catch {
        return [] as ReviewItem[];
      }
    },
    refetchInterval: 5000,
  });

  const act = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "approve" | "reject" }) =>
      apiFetch(`/review/${id}/${action}`, who, { method: "POST" }),
    onSettled: () => queryClient.invalidateQueries({ queryKey: key }),
  });

  if (query.isPending) return <p>Loading…</p>;

  const items = query.data ?? [];
  if (items.length === 0) return <p>Nothing to review</p>;

  return (
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
  );
}
