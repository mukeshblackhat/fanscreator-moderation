import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { apiFetch, USERS, type Identity } from "./api";
import { Counters } from "./Counters";
import { MediaList } from "./MediaList";
import { ReviewQueue, type ReviewItem } from "./ReviewQueue";
import { TryIt } from "./TryIt";

type Tab = "review" | "media" | "try";

export function App() {
  const [who, setWho] = useState<Identity>(USERS[0]);
  const [tab, setTab] = useState<Tab>("review");
  const held = useQuery({
    queryKey: ["review", who.userId, who.workspaceId],
    queryFn: () => apiFetch<ReviewItem[]>("/review", who),
    enabled: who.role === "admin",
    refetchInterval: 3000,
  });

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <h1>FansCreator moderation</h1>
          <p>Every image and video is screened before anyone sees it.</p>
        </div>
        <label className="who">
          Acting as{" "}
          <select
            aria-label="Acting as"
            value={who.userId}
            onChange={(e) => setWho(USERS.find((u) => u.userId === e.target.value) ?? USERS[0])}
          >
            {USERS.map((u) => (
              <option key={u.userId} value={u.userId}>
                {u.userId} · {u.role} · {u.workspaceId} ({u.verified ? "verified" : "not verified"})
              </option>
            ))}
          </select>
        </label>
      </header>

      <Counters />

      <nav className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "review"} onClick={() => setTab("review")}>
          Review queue
          {held.data && held.data.length > 0 && <span className="count">{held.data.length}</span>}
        </button>
        <button role="tab" aria-selected={tab === "media"} onClick={() => setTab("media")}>
          All media
        </button>
        <button role="tab" aria-selected={tab === "try"} onClick={() => setTab("try")}>
          Try it
        </button>
      </nav>

      {tab === "review" && <ReviewQueue who={who} />}
      {tab === "media" && <MediaList who={who} />}
      {tab === "try" && <TryIt who={who} />}
    </div>
  );
}
