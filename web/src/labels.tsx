const LABELS: Record<string, string> = {
  minor: "Looks under 18",
  sexual_violence: "Sexual violence",
  bestiality: "Animals",
  self_harm: "Self-harm",
  real_person: "Real person",
  explicit: "Explicit",
  incest: "Incest",
  trafficking: "Trafficking",
};

export type Tone = "block" | "hold" | "info" | "plain";

/** Video: "at 0:55" (1 frame per second). Image: nothing. Unknown type: "frame N" for N > 0. */
function at(frame: string, isVideo: boolean | undefined): string {
  const n = Number(frame);
  if (isVideo === true) return ` at ${Math.floor(n / 60)}:${String(n % 60).padStart(2, "0")}`;
  if (isVideo === undefined && n > 0) return ` · frame ${n}`;
  return "";
}

/** Turn a machine reason like "review:real_person@frame=3" into words a moderator reads. */
export function describeReason(reason: string, isVideo?: boolean): { text: string; tone: Tone } {
  let m = reason.match(/^prohibited:(\w+)@frame=(\d+)$/);
  if (m) return { text: `${LABELS[m[1]] ?? m[1]}${at(m[2], isVideo)}`, tone: "block" };
  m = reason.match(/^review:(\w+)@frame=(\d+)$/);
  if (m) return { text: `Possible ${(LABELS[m[1]] ?? m[1]).toLowerCase()}${at(m[2], isVideo)}`, tone: "hold" };
  if (reason === "explicit_unverified") return { text: "Explicit, workspace not verified", tone: "block" };
  m = reason.match(/^screening_unavailable:(\w+)$/);
  if (m) {
    const why: Record<string, string> = { timeout: "provider timed out", server_error: "provider errors",
      rate_limited: "provider rate limit", provider_down: "provider down" };
    return { text: `Not screened yet: ${why[m[1]] ?? m[1]}`, tone: "info" };
  }
  if (reason === "provider_malformed") return { text: "Provider sent an invalid answer", tone: "info" };
  m = reason.match(/^reviewed:(approved|rejected)_by=(.+)$/);
  if (m) return { text: `${m[1] === "approved" ? "Approved" : "Rejected"} by ${m[2]}`, tone: "plain" };
  m = reason.match(/^prompt:(\w+)$/);
  if (m) return { text: `Prompt: ${(LABELS[m[1]] ?? m[1]).toLowerCase()}`, tone: "block" };
  return { text: reason, tone: "plain" };
}

export function Reasons({ reasons, isVideo }: { reasons: string[]; isVideo?: boolean }) {
  if (reasons.length === 0) return <span className="sub">None</span>;
  return (
    <div className="chips">
      {reasons.map((r) => {
        const d = describeReason(r, isVideo);
        return (
          <span key={r} className={`chip ${d.tone}`} title={r}>
            {d.text}
          </span>
        );
      })}
    </div>
  );
}

export const HELD_KIND: Record<string, string> = {
  score: "Needs a human",
  screening_unavailable: "Waiting for provider",
  provider_malformed: "Provider error",
};

export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge ${status}`}>{status}</span>;
}

export function ago(iso: string): string {
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}
