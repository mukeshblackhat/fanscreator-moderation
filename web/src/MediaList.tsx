import { useQuery } from "@tanstack/react-query";
import { ApiError, apiFetch, type Identity } from "./api";
import { Frame, flaggedFrame } from "./Frame";
import { ago, HELD_KIND, Reasons, StatusBadge } from "./labels";

export type MediaRow = {
  id: string;
  status: string;
  reasons: string[];
  type: "image" | "video";
  source: "upload" | "generated";
  prompt: string | null;
  frame_count: number;
  is_explicit: boolean;
  held_reason_kind: string | null;
  created_at: string;
  has_images?: boolean;
};

export function MediaList({ who }: { who: Identity }) {
  const q = useQuery({
    queryKey: ["media", who.userId, who.workspaceId],
    queryFn: () => apiFetch<MediaRow[]>("/media?limit=100", who),
    refetchInterval: 2000,
  });

  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2>All media in {who.workspaceId}</h2>
          <p>Every upload and generation, newest first. Updates every 2 seconds.</p>
        </div>
      </div>
      {q.isPending && (
        <div role="status" aria-busy="true">
          <div className="skeleton" />
          <div className="skeleton" />
        </div>
      )}
      {q.isError && (
        <div className="card-body">
          <div role="alert" className="banner error">
            <span>
              Could not load media
              {q.error instanceof ApiError ? ` (HTTP ${q.error.status})` : " (network error)"}.
            </span>
            <button onClick={() => q.refetch()}>Retry</button>
          </div>
        </div>
      )}
      {q.isSuccess && q.data.length === 0 && (
        <p className="empty">No media yet. Use the Try it tab, or run the demo data script.</p>
      )}
      {q.isSuccess && q.data.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Media</th>
              <th>Status</th>
              <th>Reasons</th>
              <th>Source</th>
            </tr>
          </thead>
          <tbody>
            {q.data.map((m) => (
              <tr key={m.id}>
                <td>
                  <div className="media-cell">
                    {m.has_images && (m.status === "published" || (m.status === "held" && m.held_reason_kind === "score" && who.role === "admin")) && (
                      <Frame who={who} mediaId={m.id} index={flaggedFrame(m.reasons)} sensitive={m.status !== "published" || m.is_explicit} />
                    )}
                    {m.has_images && m.status !== "published" && !(m.status === "held" && m.held_reason_kind === "score" && who.role === "admin") && (
                      <div className="thumb-none" style={{ width: 72, height: 72, borderRadius: 8, flex: "none" }}
                        title="Pending, blocked and unscreened media is never shown">{m.status === "blocked" ? "blocked" : "hidden"}</div>
                    )}
                    <div>
                      <strong>{m.type === "video" ? `Video · ${m.frame_count}s` : "Image"}</strong>
                      {m.has_images && <span className="chip info" style={{ marginLeft: ".4rem" }}>real file</span>}
                      <div className="sub">
                        <code>{m.id.slice(0, 8)}</code> · {ago(m.created_at)}
                      </div>
                    </div>
                  </div>
                </td>
                <td>
                  <StatusBadge status={m.status} />
                  {m.is_explicit && m.status === "published" && <div className="sub">explicit, verified only</div>}
                  {m.held_reason_kind && <div className="sub">{HELD_KIND[m.held_reason_kind]}</div>}
                </td>
                <td>
                  <Reasons reasons={m.reasons} isVideo={m.type === "video"} />
                </td>
                <td>
                  {m.source === "generated" ? (
                    <>
                      <span className="chip">generated</span>
                      <div className="sub" title={m.prompt ?? ""}>“{(m.prompt ?? "").slice(0, 40)}”</div>
                    </>
                  ) : (
                    <span className="sub">upload</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
