import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, apiFetch, PROVIDER_URL, type Identity } from "./api";
import { Reasons } from "./labels";

type Preset = { label: string; hint: string; body: () => unknown };

const frames = (n: number, at?: number, scores?: Record<string, number>) =>
  Array.from({ length: n }, (_, i) => ({ scores: i === at && scores ? scores : { explicit: 0.05 } }));

const PRESETS: Preset[] = [
  { label: "Clean photo", hint: "published", body: () => ({ type: "image", frames: [{ scores: { explicit: 0.05 } }] }) },
  { label: "Explicit photo", hint: "published if verified, blocked if not", body: () => ({ type: "image", frames: [{ scores: { explicit: 0.93 } }] }) },
  { label: "Looks like a celebrity (0.6)", hint: "held for an admin", body: () => ({ type: "image", frames: [{ scores: { real_person: 0.6 } }] }) },
  { label: "Looks under 18 (0.9)", hint: "blocked everywhere", body: () => ({ type: "image", frames: [{ scores: { minor: 0.9 } }] }) },
  { label: "60 s video, explicit at 0:55", hint: "every frame counts, uses the async callback", body: () => ({ type: "video", frames: frames(60, 55, { explicit: 0.95 }) }) },
  { label: "20 s video, self-harm at 0:12 (0.6)", hint: "held, frame shown in the reason", body: () => ({ type: "video", frames: frames(20, 12, { self_harm: 0.6 }) }) },
  { label: "Generated: a cat surfing", hint: "prompt passes, output screened", body: () => ({ type: "image", source: "generated", prompt: "a cat surfing at sunset", frames: [{ scores: {} }] }) },
  { label: "Generated: “a t.e.e.n posing”", hint: "refused with 422 before generating", body: () => ({ type: "image", source: "generated", prompt: "a t.e.e.n posing", frames: [{ scores: {} }] }) },
];

const MODES = [
  { mode: "ok", label: "Healthy" },
  { mode: "timeout", label: "Outage (timeouts)" },
  { mode: "error", label: "Errors (500)" },
  { mode: "slow", label: "Slow (1.5 s)", delay_ms: 1500 },
  { mode: "rate_limit", label: "Rate limited (429)" },
  { mode: "malformed", label: "Bad answers" },
];

type LogEntry = { at: string; label: string; ok: boolean; text: string; reasons?: string[] };

export function TryIt({ who }: { who: Identity }) {
  const queryClient = useQueryClient();
  const [log, setLog] = useState<LogEntry[]>([]);

  const upload = useMutation({
    mutationFn: async (p: Preset) => {
      try {
        const r = await apiFetch<{ id: string; status: string }>("/media", who, { method: "POST", body: JSON.stringify(p.body()) });
        return { label: p.label, ok: true, text: `202 accepted · ${r.id.slice(0, 8)} is pending` };
      } catch (e) {
        if (e instanceof ApiError && e.status === 422) {
          const reasons = ((e.body as { reasons?: string[] })?.reasons ?? []) as string[];
          return { label: p.label, ok: false, text: "422 refused before generating", reasons };
        }
        throw e;
      }
    },
    onSuccess: (entry) => {
      setLog((l) => [{ at: new Date().toLocaleTimeString(), ...entry }, ...l].slice(0, 12));
      queryClient.invalidateQueries({ queryKey: ["media"] });
    },
    onError: (e) => setLog((l) => [{ at: new Date().toLocaleTimeString(), label: "Upload", ok: false, text: e.message }, ...l]),
  });

  const provider = useQuery({
    queryKey: ["provider-stats"],
    queryFn: async () => {
      const r = await fetch(`${PROVIDER_URL}/_stats`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return (await r.json()) as { mode: string; calls: Record<string, number> };
    },
    refetchInterval: 2000,
  });

  const setMode = useMutation({
    mutationFn: async (m: (typeof MODES)[number]) => {
      const r = await fetch(`${PROVIDER_URL}/_control`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: m.mode, delay_ms: m.delay_ms ?? 0 }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["provider-stats"] }),
  });

  const totalCalls = provider.data ? Object.values(provider.data.calls).reduce((a, b) => a + b, 0) : 0;

  return (
    <div className="grid2">
      <section className="card">
        <div className="card-head">
          <div>
            <h2>Upload test media as {who.userId}</h2>
            <p>Scores are part of the payload (there is no real classifier). Watch the result in All media.</p>
          </div>
        </div>
        <div className="card-body">
          <div className="presets">
            {PRESETS.map((p) => (
              <button key={p.label} className="preset" disabled={upload.isPending} onClick={() => upload.mutate(p)}>
                <strong>{p.label}</strong>
                <span>{p.hint}</span>
              </button>
            ))}
          </div>
        </div>
      </section>

      <div>
        <section className="card">
          <div className="card-head">
            <div>
              <h2>Fake provider</h2>
              <p>
                {provider.isError ? "Unreachable" : `Mode: ${provider.data?.mode ?? "…"} · ${totalCalls} calls received`}
              </p>
            </div>
          </div>
          <div className="card-body">
            <div className="modes">
              {MODES.map((m) => (
                <button key={m.mode} aria-pressed={provider.data?.mode === m.mode} onClick={() => setMode.mutate(m)}>
                  {m.label}
                </button>
              ))}
            </div>
            <p className="sub" style={{ marginBottom: 0 }}>
              During an outage, uploads are held as “Waiting for provider”. Switch back to Healthy and they re-screen by
              themselves, usually within 30 s.
            </p>
          </div>
        </section>

        <section className="card">
          <div className="card-head">
            <h2>Results</h2>
          </div>
          <div className="card-body">
            {log.length === 0 ? (
              <p className="sub" style={{ margin: 0 }}>Nothing sent yet.</p>
            ) : (
              <ul className="log">
                {log.map((e, i) => (
                  <li key={i}>
                    <span className="sub">{e.at}</span>
                    <strong>{e.label}</strong>
                    <span style={{ color: e.ok ? "var(--ok)" : "var(--block)" }}>{e.text}</span>
                    {e.reasons && <Reasons reasons={e.reasons} />}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
