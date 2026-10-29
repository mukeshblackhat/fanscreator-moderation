import { useQuery } from "@tanstack/react-query";
import { API_URL } from "./api";

type Summary = {
  media_by_status: Record<string, number>;
  held_by_reason: Record<string, number>;
  oldest_waiting_job_s: number;
  unchecked_published: number;
  breaker: string;
};

const box: React.CSSProperties = { border: "1px solid #ddd", borderRadius: 6, padding: "0.4rem 0.75rem" };

export function Counters() {
  const q = useQuery({
    queryKey: ["metrics-summary"],
    queryFn: async (): Promise<Summary> => {
      const res = await fetch(`${API_URL}/metrics/summary`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    },
    refetchInterval: 5000,
  });
  if (q.isPending) return <p role="status">Loading counters…</p>;
  if (q.isError) return <p role="alert">Counters unavailable ({q.error.message})</p>;
  const s = q.data;
  return (
    <div aria-label="Live counters" style={{ display: "flex", gap: "0.75rem", margin: "1rem 0", flexWrap: "wrap" }}>
      <span style={box}>Pending <strong>{s.media_by_status.pending ?? 0}</strong></span>
      <span style={box}>Held <strong>{s.media_by_status.held ?? 0}</strong></span>
      <span style={box}>Oldest waiting <strong>{s.oldest_waiting_job_s.toFixed(0)} s</strong></span>
      <span style={box}>Provider <strong>{s.breaker === "closed" ? "healthy" : s.breaker}</strong></span>
      {s.unchecked_published > 0 && (
        <span role="alert" style={{ ...box, borderColor: "#c62828", color: "#c62828" }}>
          Unchecked published <strong>{s.unchecked_published}</strong>
        </span>
      )}
    </div>
  );
}
