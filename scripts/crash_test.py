"""Crash test: a worker dies after the provider scored a video, before the save.

1. Worker A starts with CRASH_AFTER_PROVIDER=1 and a short lease.
2. Upload a 20 frame video (synchronous scoring) to ws_unverified with
   explicit 0.95 only on the last frame. The correct outcome is `blocked`.
3. Worker A claims it, the provider scores it, and A exits with os._exit(137)
   before writing anything except the in-flight ledger row.
4. Check: the media is still pending (never published), the ledger has one
   call stuck `in_flight`.
5. Worker B starts normally. After A's lease expires, B reclaims the job with
   a new fencing token, marks A's call `abandoned`, calls again, decides.
6. Check: final status `blocked`, never published, and record exactly how
   many provider calls the crash cost.

Needs the API and provider running and NO other workers on the same database.
Run from the api folder:
  uv run python ../scripts/crash_test.py --out ../docs/evidence/05-crash-test.json
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

API_DIR = Path(__file__).resolve().parents[1] / "api"
WHO = {"X-User-Id": "member_u", "X-Workspace-Id": "ws_unverified"}


def start_worker(env_extra: dict[str, str], log_path: Path) -> subprocess.Popen:
    env = {**os.environ, **env_extra}
    return subprocess.Popen([sys.executable, "-m", "app.screening.worker"], cwd=API_DIR, env=env,
                            stdout=open(log_path, "w"), stderr=subprocess.STDOUT)


async def ledger(engine, media_id: str) -> list[dict]:
    async with engine.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT attempt, kind, outcome FROM provider_calls WHERE media_id = :m ORDER BY started_at"
        ), {"m": media_id})).mappings().all()
    return [dict(r) for r in rows]


async def run(api: str, provider_url: str, database_url: str, lease: int, log_dir: Path) -> dict:
    os.environ["DATABASE_URL"] = database_url
    os.environ["PROVIDER_URL"] = provider_url
    os.environ["LEASE_SECONDS"] = str(lease)
    engine = create_async_engine(database_url)
    statuses: list[str] = []
    result: dict = {"lease_seconds": lease}
    async with httpx.AsyncClient(base_url=api, timeout=10) as client, \
            httpx.AsyncClient(base_url=provider_url, timeout=10) as provider:
        await provider.post("/_reset")
        frames = [{"scores": {}} for _ in range(20)]
        frames[19] = {"scores": {"explicit": 0.95}}
        r = await client.post("/media", headers=WHO, json={"type": "video", "frames": frames})
        media_id = r.json()["id"]
        result["media_id"] = media_id

        worker_a = start_worker({"CRASH_AFTER_PROVIDER": "1"}, log_dir / "crash-worker-a.log")
        crashed_at = time.monotonic()
        try:
            exit_code = worker_a.wait(timeout=30)
        except subprocess.TimeoutExpired:
            worker_a.kill()
            raise SystemExit("worker A never crashed: is another worker on this database?")
        result["worker_a_exit_code"] = exit_code
        after_crash = (await client.get(f"/media/{media_id}", headers=WHO)).json()["status"]
        statuses.append(after_crash)
        result["status_after_crash"] = after_crash
        result["ledger_after_crash"] = await ledger(engine, media_id)
        result["provider_calls_after_crash"] = (await provider.get("/_stats")).json()["calls"].get(media_id, 0)

        worker_b = start_worker({}, log_dir / "crash-worker-b.log")
        try:
            final = after_crash
            deadline = time.monotonic() + lease + 30
            while time.monotonic() < deadline:
                final = (await client.get(f"/media/{media_id}", headers=WHO)).json()["status"]
                if not statuses or statuses[-1] != final:
                    statuses.append(final)
                if final != "pending":
                    break
                await asyncio.sleep(0.2)
            result["recovered_after_s"] = round(time.monotonic() - crashed_at, 1)
        finally:
            worker_b.terminate()
            worker_b.wait(timeout=10)

        result["final_status"] = final
        result["statuses_seen"] = statuses
        result["ledger_final"] = await ledger(engine, media_id)
        result["provider_calls_total"] = (await provider.get("/_stats")).json()["calls"].get(media_id, 0)
        async with engine.connect() as conn:
            result["scores_rows"] = (await conn.execute(text(
                "SELECT count(*) FROM scores WHERE media_id = :m"), {"m": media_id})).scalar()
            result["decisions"] = [dict(r) for r in (await conn.execute(text(
                "SELECT status, frames_scored, frame_count, decided_by FROM decisions WHERE media_id = :m"
            ), {"m": media_id})).mappings().all()]
    await engine.dispose()

    result["checks"] = {
        "worker_a_crashed_with_137": result["worker_a_exit_code"] == 137,
        "pending_after_crash": result["status_after_crash"] == "pending",
        "never_published": "published" not in statuses,
        "final_is_blocked": result["final_status"] == "blocked",
        "first_call_marked_abandoned": [c["outcome"] for c in result["ledger_final"]] == ["abandoned", "ok"],
        "scored_once_in_db": result["scores_rows"] == 1,
    }
    result["passed"] = all(result["checks"].values())
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--provider", default="http://localhost:8001")
    ap.add_argument("--database-url", default="postgresql+asyncpg://moderation:moderation@localhost:5432/moderation")
    ap.add_argument("--lease", type=int, default=5)
    ap.add_argument("--log-dir", default="/tmp")
    ap.add_argument("--out")
    args = ap.parse_args()
    result = asyncio.run(run(args.api, args.provider, args.database_url, args.lease, Path(args.log_dir)))
    text_out = json.dumps(result, indent=2, default=str)
    print(text_out)
    if args.out:
        Path(args.out).write_text(text_out + "\n")
    sys.exit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
