import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { API_URL, ApiError, apiFetch, type Identity } from "./api";
import { Frame, flaggedFrame } from "./Frame";
import { Reasons, StatusBadge } from "./labels";

type RealMode = {
  available: boolean;
  reason?: string;
  primary?: string;
  secondary?: string | null;
  checked_labels?: string[];
  not_checked_labels?: string[];
};

export type Sent = { id: string; name: string; frames: number; at: string };
const LABEL: Record<string, string> = {
  explicit: "explicit", self_harm: "self-harm", sexual_violence: "sexual violence",
  minor: "age (minor)", real_person: "real person", bestiality: "animals",
};

export function Live({ who, item, images = true }: { who: Identity; item: Sent; images?: boolean }) {
  const q = useQuery({
    queryKey: ["media-one", who.userId, item.id],
    queryFn: () => apiFetch<{ id: string; status: string; reasons: string[] }>(`/media/${item.id}`, who),
    refetchInterval: (query) => (query.state.data && query.state.data.status !== "pending" ? false : 1000),
  });
  const status = q.data?.status ?? "pending";
  return (
    <li>
      {images && (status === "published" || (status === "held" && who.role === "admin")) ? (
        <Frame who={who} mediaId={item.id} index={flaggedFrame(q.data?.reasons ?? [])} sensitive={status !== "published"} size={48} />
      ) : null}
      <span className="sub">{item.at}</span>
      <strong title={item.name}>{item.name.length > 28 ? item.name.slice(0, 26) + "…" : item.name}</strong>
      <span className="sub">{item.frames === 1 ? "1 frame" : `${item.frames} frames`}</span>
      <StatusBadge status={status} />
      {status === "pending" && <span className="sub">scoring…</span>}
      {q.data && <Reasons reasons={q.data.reasons} isVideo={item.frames > 1} />}
    </li>
  );
}

export function Upload({ who }: { who: Identity }) {
  const queryClient = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [sent, setSent] = useState<Sent[]>([]);
  const mode = useQuery({
    queryKey: ["real-mode"],
    queryFn: async (): Promise<RealMode> => (await fetch(`${API_URL}/real-mode`)).json(),
    refetchInterval: (q) => (q.state.data?.available ? 30000 : 3000),
  });

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(`${API_URL}/media/upload`, {
        method: "POST",
        headers: { "X-User-Id": who.userId, "X-Workspace-Id": who.workspaceId },
        body: form,
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new ApiError(res.status, (body.reasons ?? [body.detail ?? res.statusText]).join(", "), body);
      return { id: body.id as string, name: file.name, frames: body.frames as number, at: new Date().toLocaleTimeString() };
    },
    onSuccess: (s) => {
      setSent((l) => [s, ...l].slice(0, 10));
      queryClient.invalidateQueries({ queryKey: ["media"] });
    },
  });

  const pick = (files: FileList | null) => {
    if (files) Array.from(files).slice(0, 5).forEach((f) => upload.mutate(f));
  };
  const m = mode.data;

  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2>Upload your own file</h2>
          <p>Real pixels, real model. Images, or videos up to 2 minutes (scored at 1 frame per second).</p>
        </div>
      </div>
      <div className="card-body">
        {!m ? (
          <p className="sub">Checking the real provider…</p>
        ) : !m.available ? (
          <div className="banner info">Real mode is off: {m.reason}</div>
        ) : (
          <>
            <div
              className={`drop ${over ? "over" : ""}`}
              onDragOver={(e) => { e.preventDefault(); setOver(true); }}
              onDragLeave={() => setOver(false)}
              onDrop={(e) => { e.preventDefault(); setOver(false); pick(e.dataTransfer.files); }}
            >
              <p style={{ margin: "0 0 .6rem" }}>Drop images or videos here, or</p>
              <button className="primary" onClick={() => input.current?.click()} disabled={upload.isPending}>
                {upload.isPending ? "Uploading…" : "Choose files"}
              </button>
              <input ref={input} type="file" accept="image/*,video/*" multiple aria-label="Choose files"
                onChange={(e) => { pick(e.target.files); e.target.value = ""; }} />
            </div>
            <div className="checks">
              <span className="sub">Checked:</span>
              {m.checked_labels!.map((l) => <span key={l} className="chip info">{LABEL[l] ?? l}</span>)}
            </div>
            <div className="checks">
              <span className="sub">Not checked on real files:</span>
              {m.not_checked_labels!.map((l) => <span key={l} className="chip">{LABEL[l] ?? l}</span>)}
            </div>
            <p className="sub" style={{ marginBottom: 0 }}>
              Primary: {m.primary} (runs locally, files never leave your machine).{" "}
              {m.secondary ? `Secondary: OpenAI ${m.secondary}.` : "Secondary: off (set OPENAI_API_KEY to add OpenAI moderation)."}{" "}
              Uploaded as {who.userId} into {who.workspaceId}.
            </p>
          </>
        )}
        {upload.isError && <p role="alert" className="banner error" style={{ marginTop: ".75rem" }}>Upload refused: {upload.error.message}</p>}
        {sent.length > 0 && (
          <ul className="log" style={{ marginTop: ".75rem" }}>
            {sent.map((s) => <Live key={s.id} who={who} item={s} />)}
          </ul>
        )}
      </div>
    </section>
  );
}
