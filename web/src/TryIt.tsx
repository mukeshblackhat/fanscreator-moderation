import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, apiFetch, type Identity } from "./api";
import { Reasons } from "./labels";
import { PromptTester } from "./PromptTester";
import { Upload } from "./Upload";

type Preset = { label: string; hint: string; body: () => unknown };

const frames = (n: number, at?: number, scores?: Record<string, number>) =>
  Array.from({ length: n }, (_, i) => (i === at && scores ? scores : { explicit: 0.05 }));

const PRESETS: Preset[] = [
  { label: "Clean photo", hint: "published", body: () => ({ type: "image", frames: [{ explicit: 0.05 }] }) },
  { label: "Explicit photo", hint: "published if verified, blocked if not", body: () => ({ type: "image", frames: [{ explicit: 0.93 }] }) },
  { label: "Looks like a celebrity (0.6)", hint: "held for an admin", body: () => ({ type: "image", frames: [{ real_person: 0.6 }] }) },
  { label: "Looks under 18 (0.9)", hint: "blocked everywhere", body: () => ({ type: "image", frames: [{ minor: 0.9 }] }) },
  { label: "60 s video, explicit at 0:55", hint: "every frame counts, uses the async callback", body: () => ({ type: "video", frames: frames(60, 55, { explicit: 0.95 }) }) },
  { label: "20 s video, self-harm at 0:12 (0.6)", hint: "held, frame shown in the reason", body: () => ({ type: "video", frames: frames(20, 12, { self_harm: 0.6 }) }) },
  { label: "Generated: a cat surfing", hint: "prompt passes, output screened", body: () => ({ type: "image", source: "generation", prompt: "a cat surfing at sunset", frames: [{}] }) },
  { label: "Generated: “a t.e.e.n posing”", hint: "refused with 422 before generating", body: () => ({ type: "image", source: "generation", prompt: "a t.e.e.n posing", frames: [{}] }) },
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




  return (
    <div className="grid2">
      <div>
      <PromptTester who={who} />
      <Upload who={who} />
      <section className="card">
        <div className="card-head">
          <div>
            <h2>Synthetic test media as {who.userId}</h2>
            <p>Scores are written into the payload and go to the fake provider. Good for categories real mode cannot check.</p>
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
      </div>

      <div>
        <section className="card">
          <div className="card-body">
            <p className="sub" style={{ margin: 0 }}>
              Provider states (healthy, slow, outage, 500s, 429, bad answers, duplicate callbacks) are switched from the
              Fake provider bar at the top, on every tab. During an outage uploads are held as “Waiting for provider”;
              switch back to Healthy and they re-screen by themselves, usually within 30 s.
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
