import { useState } from "react";
import { USERS, type Identity } from "./api";
import { ReviewQueue } from "./ReviewQueue";

export function App() {
  const [who, setWho] = useState<Identity>(USERS[0]);
  return (
    <main style={{ fontFamily: "system-ui, sans-serif", maxWidth: 960, margin: "2rem auto", padding: "0 1rem" }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1 style={{ fontSize: "1.25rem" }}>Review queue</h1>
        <label>
          Acting as{" "}
          <select
            aria-label="Acting as"
            value={who.userId}
            onChange={(e) => setWho(USERS.find((u) => u.userId === e.target.value) ?? USERS[0])}
          >
            {USERS.map((u) => (
              <option key={u.userId} value={u.userId}>
                {u.userId} ({u.workspaceId})
              </option>
            ))}
          </select>
        </label>
      </header>
      <ReviewQueue who={who} />
    </main>
  );
}
