import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { API_URL, PROVIDER_URL } from "./api";

type Stats = { mode: string; calls: Record<string, number> };
type Control = { mode: string; delay_ms: number; duplicate_callbacks: boolean };

export const MODES: { mode: string; label: string; hint: string }[] = [
  { mode: "ok", label: "Healthy", hint: "answers normally" },
  { mode: "slow", label: "Slow", hint: "waits before answering (set the delay)" },
  { mode: "timeout", label: "Outage", hint: "hangs past our timeout: retries, then held, then auto re-screen" },
  { mode: "error", label: "500 errors", hint: "server errors: retried 3 times, then held" },
  { mode: "rate_limit", label: "429 rate limit", hint: "Retry-After 2 s, its own retry budget" },
  { mode: "malformed", label: "Bad answers", hint: "drops a label and a frame: never trusted, held after 2" },
];

/** Fake provider controls, on every tab, so any state can be tried from wherever you are.
 * They drive the provider's own /_control endpoint, the same one the graders' checks use. */
export function ProviderControls() {
  const queryClient = useQueryClient();
  const [delay, setDelay] = useState(1500);
  const [duplicates, setDuplicates] = useState(false);

  const stats = useQuery({
    queryKey: ["provider-stats"],
    queryFn: async (): Promise<Stats> => {
      const r = await fetch(`${PROVIDER_URL}/_stats`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
    refetchInterval: 2000,
  });
  const real = useQuery({
    queryKey: ["real-mode"],
    queryFn: async () => (await fetch(`${API_URL}/real-mode`)).json() as Promise<{ available: boolean }>,
    refetchInterval: 10000,
  });

  const control = useMutation({
    mutationFn: async (c: Control) => {
      const r = await fetch(`${PROVIDER_URL}/_control`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(c),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["provider-stats"] }),
  });
  const reset = useMutation({
    mutationFn: async () => { await fetch(`${PROVIDER_URL}/_reset`, { method: "POST" }); },
    onSettled: () => { setDuplicates(false); queryClient.invalidateQueries({ queryKey: ["provider-stats"] }); },
  });

  const mode = stats.data?.mode;
  const calls = stats.data ? Object.values(stats.data.calls).reduce((a, b) => a + b, 0) : 0;
  const set = (m: string, dup = duplicates) => control.mutate({ mode: m, delay_ms: m === "slow" ? delay : 0, duplicate_callbacks: dup });
  const current = MODES.find((m) => m.mode === mode);

  return (
    <section className="card provider-bar" aria-label="Provider controls">
      <div className="provider-row">
        <strong>Fake provider</strong>
        <div className="modes">
          {MODES.map((m) => (
            <button key={m.mode} aria-pressed={mode === m.mode} title={m.hint} onClick={() => set(m.mode)}>
              {m.label}
            </button>
          ))}
        </div>
        <label className="sub inline">
          delay
          <input type="number" min={0} step={250} value={delay} aria-label="Slow delay in ms"
            onChange={(e) => setDelay(Number(e.target.value))} onBlur={() => mode === "slow" && set("slow")} /> ms
        </label>
        <label className="sub inline">
          <input type="checkbox" checked={duplicates} aria-label="Duplicate callbacks"
            onChange={(e) => { setDuplicates(e.target.checked); set(mode ?? "ok", e.target.checked); }} />
          duplicate callbacks
        </label>
        <button className="link" onClick={() => reset.mutate()} title="Back to healthy and clear the call counts">reset</button>
      </div>
      <div className="sub provider-status">
        {stats.isError
          ? "Fake provider unreachable."
          : <>Now: <strong>{current?.label ?? mode ?? "…"}</strong>{current ? ` (${current.hint})` : ""} · {calls} {calls === 1 ? "call" : "calls"} received</>}
        {" · "}Real provider: {real.data?.available ? "running (real uploads ignore these switches)" : "off"}
      </div>
    </section>
  );
}
