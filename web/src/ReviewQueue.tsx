import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiFetch, type Identity } from "./api";
import { ago, HELD_KIND, Reasons } from "./labels";

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
    refetchInterval: 3000,
    retry: false, // errors show at once with a Retry button; the queue also refreshes every 3 s
  });

  const act = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "approve" | "reject" }) =>
      apiFetch(`/review/${id}/${action}`, who, { method: "POST" }),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: key });
      queryClient.invalidateQueries({ queryKey: ["media"] });
      queryClient.invalidateQueries({ queryKey: ["metrics-summary"] });
    },
  });

  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2>Review queue</h2>
          <p>Held media in {who.workspaceId}. Only workspace admins can approve or reject.</p>
        </div>
        {query.isFetching && !query.isPending && <span className="sub">refreshing…</span>}
      </div>
      {renderBody()}
    </section>
  );

  function renderBody() {
    if (query.isPending) {
      return (
        <div role="status" aria-busy="true">
          <span className="sub" style={{ display: "block", padding: ".75rem 1rem 0" }}>Loading review queue…</span>
          <div className="skeleton" />
          <div className="skeleton" />
          <div className="skeleton" style={{ width: "60%" }} />
        </div>
      );
    }

    if (query.isError) {
      const status = query.error instanceof ApiError ? query.error.status : undefined;
      if (status === 403) {
        return (
          <div className="card-body">
            <div className="banner info">
              {who.userId} is a member, not an admin, so the API answers 403 here. Switch to{" "}
              {who.workspaceId === "ws_verified" ? "admin_v" : "admin_u"} to review this workspace.
            </div>
          </div>
        );
      }
      return (
        <div className="card-body">
          <div role="alert" className="banner error">
            <span>
              <strong>Could not load the review queue</strong>
              {status !== undefined ? ` (HTTP ${status})` : " (network error)"}. Items may be waiting.
            </span>
            <button onClick={() => query.refetch()} disabled={query.isFetching}>
              Retry
            </button>
          </div>
        </div>
      );
    }

    const items = query.data;
    if (items.length === 0) return <p className="empty">Nothing to review</p>;

    return (
      <>
        {act.isError && (
          <div className="card-body" style={{ paddingBottom: 0 }}>
            <p role="alert" className="banner error" style={{ margin: 0 }}>
              {act.error instanceof ApiError && act.error.status === 409
                ? "That item cannot be approved: it was never screened."
                : `Action failed: ${act.error.message}`}
            </p>
          </div>
        )}
        <table>
          <thead>
            <tr>
              <th>Media</th>
              <th>Why it is held</th>
              <th>Hold type</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items.map((item) => {
              const unscreened = item.held_reason_kind !== null && NOT_APPROVABLE.has(item.held_reason_kind);
              return (
                <tr key={item.id}>
                  <td>
                    <code>{item.id.slice(0, 8)}</code>
                    <div className="sub">{ago(item.created_at)}</div>
                  </td>
                  <td>
                    <Reasons reasons={item.reasons} />
                  </td>
                  <td>
                    <span className="sub">{item.held_reason_kind ? HELD_KIND[item.held_reason_kind] : ""}</span>
                  </td>
                  <td className="actions">
                    <button
                      className="primary"
                      disabled={unscreened || act.isPending}
                      title={unscreened ? "Not screened yet: re-screened automatically, cannot be approved" : undefined}
                      onClick={() => act.mutate({ id: item.id, action: "approve" })}
                    >
                      Approve
                    </button>{" "}
                    <button className="danger" disabled={act.isPending} onClick={() => act.mutate({ id: item.id, action: "reject" })}>
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
}
