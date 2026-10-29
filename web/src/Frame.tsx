import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { API_URL, type Identity } from "./api";

/** A stored frame, fetched with the identity headers (an <img src> cannot send them).
 * Blurred until clicked when `sensitive`, because held or explicit media can be anything. */
export function Frame({ who, mediaId, index, sensitive, size = 72 }: {
  who: Identity; mediaId: string; index: number; sensitive: boolean; size?: number;
}) {
  const [revealed, setRevealed] = useState(!sensitive);
  const q = useQuery({
    queryKey: ["frame", who.userId, who.workspaceId, mediaId, index],
    queryFn: async () => {
      const res = await fetch(`${API_URL}/media/${mediaId}/frames/${index}`, {
        headers: { "X-User-Id": who.userId, "X-Workspace-Id": who.workspaceId },
      });
      if (!res.ok) throw new Error(String(res.status));
      return URL.createObjectURL(await res.blob());
    },
    staleTime: Infinity,
    retry: false,
  });
  // The blob URL stays cached (staleTime: Infinity) so switching tabs does not refetch;
  // revoking it on unmount broke the cached copy when the component mounted again.
  useEffect(() => setRevealed(!sensitive), [sensitive]);

  const box: React.CSSProperties = { width: size, height: size, borderRadius: 8, flex: "none" };
  if (q.isError) return <div className="thumb-none" style={box} title="Not viewable for this status or role">hidden</div>;
  if (!q.data) return <div className="skeleton" style={{ ...box, margin: 0 }} />;
  return (
    <button
      className="thumb"
      style={box}
      onClick={() => setRevealed((r) => !r)}
      title={revealed ? "Click to blur" : "Sensitive: click to reveal"}
      aria-label={revealed ? "Blur frame" : "Reveal frame"}
    >
      <img src={q.data} alt={`frame ${index}`} style={{ filter: revealed ? "none" : "blur(14px)" }} />
      {!revealed && <span className="thumb-label">reveal</span>}
    </button>
  );
}

/** First frame a reason points at, e.g. "review:self_harm@frame=12" -> 12. */
export function flaggedFrame(reasons: string[]): number {
  for (const r of reasons) {
    const m = r.match(/@frame=(\d+)$/);
    if (m) return Number(m[1]);
  }
  return 0;
}
