import { useQuery } from "@tanstack/react-query";
import { API_URL } from "./api";

type Summary = {
  media_by_status: Record<string, number>;
  held_by_reason: Record<string, number>;
  oldest_waiting_job_s: number;
  unchecked_published: number;
  provider_calls_per_screened_media: number | null;
  breaker: string;
};

const BREAKER: Record<string, string> = { closed: "healthy", open: "open", half_open: "probing", unknown: "unknown" };

export function Counters() {
  const q = useQuery({
    queryKey: ["metrics-summary"],
    queryFn: async (): Promise<Summary> => {
      const res = await fetch(`${API_URL}/metrics/summary`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    },
    refetchInterval: 2000,
  });
  if (q.isPending) return <p role="status" className="sub">Loading counters…</p>;
  if (q.isError) return <p role="alert" className="banner error">Counters unavailable ({q.error.message})</p>;
  const s = q.data;
  const stat = (label: string, value: string | number, extra = "") => (
    <div className={`stat ${extra}`}>
      <span>{label}</span> <strong>{value}</strong>
    </div>
  );
  return (
    <>
    <p className="sub" style={{ margin: "1.25rem 0 -0.5rem" }}>Platform-wide, all workspaces · live</p>
    <div aria-label="Live counters" className="stats">
      {stat("Published", s.media_by_status.published ?? 0)}
      {stat("Pending", s.media_by_status.pending ?? 0)}
      {stat("Held", s.media_by_status.held ?? 0)}
      {stat("Blocked", s.media_by_status.blocked ?? 0)}
      {stat("Oldest waiting", `${s.oldest_waiting_job_s.toFixed(0)} s`)}
      {stat("Provider", BREAKER[s.breaker] ?? s.breaker, s.breaker === "closed" ? "" : "alert")}
      {s.unchecked_published > 0 && (
        <div role="alert" className="stat alert">
          <span>Unchecked published</span> <strong>{s.unchecked_published}</strong>
        </div>
      )}
    </div>
    </>
  );
}
