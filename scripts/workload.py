"""Repeatable two-workspace workload that replays the incident.

Phases (scaled by --scale):
  1. healthy   30 s  provider mode "ok"
  2. outage    40 s  provider mode "timeout" (a scaled-down stand-in for the 40 minute outage)
  3. recovery  60 s  provider mode "ok"
then up to --drain seconds for the queue to settle.

Traffic:
  ws_unverified  one image every 250 ms the whole time: clean, 0.6 hold, 0.9 block, explicit 0.9
  ws_verified    200 videos in one burst at the start of the outage (10, 31 and 60 frames),
                 some with explicit or prohibited scores only on late frames
  both           a few generated items, some with prompts that must be refused

Every item's true outcome is computed with the real policy on ALL its frames,
so the report can count media that went live when they should not have.

Run against a running stack (docker compose or local), from the api folder:
  uv run python ../scripts/workload.py --out ../docs/evidence/02-broken-workload.json
"""
import argparse
import asyncio
import json
import random
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
from app.screening.policy import decide  # noqa: E402

VERIFIED = {"X-User-Id": "member_v", "X-Workspace-Id": "ws_verified"}
UNVERIFIED = {"X-User-Id": "member_u", "X-Workspace-Id": "ws_unverified"}
REFUSED_PROMPTS = ["a 16 year old at the beach", "a sixteen-year-old in a bedroom", "teen girl, 15, posing"]
CLEAN_PROMPTS = ["a cat surfing at sunset", "a lighthouse in a storm"]


@dataclass
class Item:
    id: str | None
    workspace: str
    type: str
    phase: str
    expected: str               # what the policy says when every frame is scored
    sent_at: float
    http_status: int
    source: str = "upload"
    refused_expected: bool = False


@dataclass
class Run:
    label: str
    started_at: str
    scale: float
    items: list[Item] = field(default_factory=list)
    samples: list[dict[str, Any]] = field(default_factory=list)
    phase: str = "healthy"


def image_scores(rng: random.Random) -> dict[str, float]:
    roll = rng.random()
    if roll < 0.60:
        return {"explicit": round(rng.uniform(0, 0.3), 2)}
    if roll < 0.75:
        return {"real_person": 0.6}
    if roll < 0.85:
        return {"minor": 0.9}
    return {"explicit": 0.9}


def video_frames(rng: random.Random) -> list[dict[str, Any]]:
    n = rng.choice([10, 31, 60])
    frames: list[dict[str, Any]] = [{"scores": {"explicit": round(rng.uniform(0, 0.2), 2)}} for _ in range(n)]
    late = rng.randrange(max(n - n // 5, 9), n) if n > 9 else n - 1  # always after frame 8
    roll = rng.random()
    if roll < 0.15:
        frames[late] = {"scores": {"explicit": 0.95}}
    elif roll < 0.25:
        frames[late] = {"scores": {rng.choice(["minor", "sexual_violence", "real_person"]): 0.9}}
    elif roll < 0.30:
        frames[late] = {"scores": {"self_harm": 0.6}}
    return frames


def expected(frames: list[dict[str, Any]], verified: bool) -> str:
    return decide([f.get("scores", {}) for f in frames], verified).status


async def post(client: httpx.AsyncClient, run: Run, who: dict, body: dict, *, refused: bool = False) -> None:
    verified = who is VERIFIED
    sent = time.time()
    try:
        r = await client.post("/media", headers=who, json=body)
        status, media_id = r.status_code, (r.json().get("id") if r.status_code == 202 else None)
    except httpx.HTTPError:
        status, media_id = 0, None
    run.items.append(Item(
        id=media_id, workspace=who["X-Workspace-Id"], type=body["type"], phase=run.phase,
        expected="refused" if refused else expected(body["frames"], verified),
        sent_at=sent, http_status=status, source=body.get("source", "upload"), refused_expected=refused,
    ))


async def unverified_images(client: httpx.AsyncClient, run: Run, rng: random.Random, stop: asyncio.Event) -> None:
    while not stop.is_set():
        asyncio.create_task(post(client, run, UNVERIFIED, {"type": "image", "frames": [{"scores": image_scores(rng)}]}))
        await asyncio.sleep(0.25)


async def generated(client: httpx.AsyncClient, run: Run, rng: random.Random) -> None:
    for prompt in REFUSED_PROMPTS:
        body = {"type": "image", "source": "generated", "prompt": prompt, "frames": [{"scores": {}}]}
        await post(client, run, rng.choice([VERIFIED, UNVERIFIED]), body, refused=True)
    prompt = rng.choice(CLEAN_PROMPTS)
    await post(client, run, VERIFIED, {"type": "image", "source": "generated", "prompt": prompt,
                                       "frames": [{"scores": {}}]})


async def video_burst(client: httpx.AsyncClient, run: Run, rng: random.Random, count: int) -> None:
    bodies = [{"type": "video", "frames": video_frames(rng)} for _ in range(count)]
    limit = asyncio.Semaphore(25)

    async def one(body: dict) -> None:
        async with limit:
            await post(client, run, VERIFIED, body)

    await asyncio.gather(*(one(b) for b in bodies))


async def sampler(engine, run: Run, stop: asyncio.Event) -> None:
    t0 = time.time()
    while not stop.is_set():
        async with engine.connect() as conn:
            row = (await conn.execute(text(
                """
                SELECT
                  (SELECT EXTRACT(EPOCH FROM now() - min(created_at)) FROM jobs
                     WHERE state IN ('queued', 'running', 'waiting_callback')) AS oldest_job_s,
                  (SELECT EXTRACT(EPOCH FROM now() - min(created_at)) FROM media
                     WHERE status = 'pending') AS oldest_pending_s,
                  (SELECT count(*) FROM media WHERE status = 'pending') AS pending,
                  (SELECT count(*) FROM media WHERE status = 'held') AS held
                """
            ))).mappings().one()
        run.samples.append({"t": round(time.time() - t0, 1), "phase": run.phase,
                            **{k: (round(float(v), 1) if v is not None else None) for k, v in row.items()}})
        await asyncio.sleep(1.0)


def pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 3)
    return round(statistics.quantiles(values, n=100, method="inclusive")[int(p) - 1], 3)


async def summarize(engine, provider: httpx.AsyncClient, run: Run) -> dict[str, Any]:
    ids = [i.id for i in run.items if i.id]
    async with engine.connect() as conn:
        rows = (await conn.execute(text(
            """
            SELECT m.id::text AS id, m.status, m.frame_count, m.held_reason_kind,
                   EXTRACT(EPOCH FROM m.created_at) AS created,
                   d.frames_scored, d.frame_count AS d_frame_count, d.status AS d_status,
                   (SELECT EXTRACT(EPOCH FROM min(created_at)) FROM decisions x WHERE x.media_id = m.id) AS first_decision,
                   (SELECT jsonb_array_length(s.frames) FROM scores s WHERE s.media_id = m.id) AS scored_frames,
                   (SELECT count(*) FROM provider_calls c WHERE c.media_id = m.id) AS ledger_calls
            FROM media m LEFT JOIN decisions d ON d.id = m.decision_id
            WHERE m.id::text = ANY(:ids)
            """
        ), {"ids": ids})).mappings().all()
    db = {r["id"]: r for r in rows}
    stats = (await provider.get("/_stats")).json()["calls"]

    accepted = [i for i in run.items if i.id]
    without_complete_decision = 0
    against_policy = 0
    without_full_scores = 0
    unchecked_ids = set()
    final_counts: dict[str, int] = {}
    latencies: dict[str, dict[str, list[float]]] = {}
    undecided_images: dict[str, int] = {}
    for item in accepted:
        r = db.get(item.id)
        status = r["status"] if r else "missing"
        final_counts[status] = final_counts.get(status, 0) + 1
        if status == "published":
            complete = (r["d_status"] == "published" and r["frames_scored"] == r["frame_count"] == r["d_frame_count"])
            if not complete:
                without_complete_decision += 1
                unchecked_ids.add(item.id)
            if item.expected != "published":  # includes generated items whose prompt should be refused
                against_policy += 1
                unchecked_ids.add(item.id)
            if r["scored_frames"] != r["frame_count"]:
                without_full_scores += 1
        if item.type == "image" and r:
            key = f"{item.workspace}/{item.phase}"
            if r["first_decision"] is not None:
                latencies.setdefault(item.workspace, {}).setdefault(item.phase, []).append(
                    float(r["first_decision"]) - float(r["created"]))
            else:
                undecided_images[key] = undecided_images.get(key, 0) + 1

    refused = [i for i in run.items if i.refused_expected]
    total_calls = sum(stats.get(i.id, 0) for i in accepted)
    per_phase_age: dict[str, float | None] = {}
    for phase in ("healthy", "outage", "recovery", "drain"):
        ages = [s["oldest_job_s"] for s in run.samples if s["phase"] == phase and s["oldest_job_s"] is not None]
        per_phase_age[phase] = max(ages) if ages else 0.0

    return {
        "label": run.label,
        "started_at": run.started_at,
        "scale": run.scale,
        "uploads_sent": len(run.items),
        "uploads_accepted": len(accepted),
        "unchecked_published": len(unchecked_ids),
        "published_without_complete_decision": without_complete_decision,
        "published_against_true_policy": against_policy,
        "published_without_full_scores_row": without_full_scores,
        "refused_prompts_sent": len(refused),
        "refused_prompts_accepted": sum(1 for i in refused if i.http_status == 202),
        "provider_calls_total": total_calls,
        "provider_calls_per_upload": round(total_calls / len(accepted), 3) if accepted else None,
        "ledger_calls_total": sum(int(r["ledger_calls"]) for r in rows),
        "oldest_waiting_job_age_s_max_by_phase": per_phase_age,
        "image_latency_s": {
            ws: {phase: {"n": len(v), "p50": pct(v, 50), "p95": pct(v, 95)} for phase, v in phases.items()}
            for ws, phases in latencies.items()
        },
        "images_without_decision_by_phase": undecided_images,
        "final_status_counts": final_counts,
        "expected_status_counts": _count(i.expected for i in accepted),
        "samples": run.samples,
    }


def _count(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--provider", default="http://localhost:8001")
    ap.add_argument("--database-url", default="postgresql+asyncpg://moderation:moderation@localhost:5432/moderation")
    ap.add_argument("--scale", type=float, default=1.0, help="multiply every phase duration")
    ap.add_argument("--drain", type=float, default=30.0, help="max seconds to wait for pending to reach 0")
    ap.add_argument("--videos", type=int, default=200)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--label", default="run")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    run = Run(label=args.label, started_at=datetime.now(timezone.utc).isoformat(), scale=args.scale)
    engine = create_async_engine(args.database_url)
    stop = asyncio.Event()
    limits = httpx.Limits(max_connections=50)
    async with httpx.AsyncClient(base_url=args.api, timeout=15, limits=limits) as client, \
            httpx.AsyncClient(base_url=args.provider, timeout=10) as provider:
        await provider.post("/_reset")
        sampling = asyncio.create_task(sampler(engine, run, stop))
        images = asyncio.create_task(unverified_images(client, run, rng, stop))

        print(f"[{args.label}] healthy for {30 * args.scale:.0f}s", flush=True)
        await generated(client, run, rng)
        await asyncio.sleep(30 * args.scale)

        run.phase = "outage"
        print(f"[{args.label}] outage for {40 * args.scale:.0f}s, {args.videos} video burst", flush=True)
        await provider.post("/_control", json={"mode": "timeout"})
        await asyncio.gather(video_burst(client, run, rng, args.videos), generated(client, run, rng))
        await asyncio.sleep(max(0.0, 40 * args.scale - 1))

        run.phase = "recovery"
        print(f"[{args.label}] recovery for {60 * args.scale:.0f}s", flush=True)
        await provider.post("/_control", json={"mode": "ok"})
        await generated(client, run, rng)
        await asyncio.sleep(60 * args.scale)

        images.cancel()
        run.phase = "drain"
        deadline = time.time() + args.drain
        while time.time() < deadline:
            async with engine.connect() as conn:
                pending = (await conn.execute(text(
                    "SELECT count(*) FROM jobs WHERE state IN ('queued', 'running', 'waiting_callback')"
                ))).scalar()
            if pending == 0:
                break
            await asyncio.sleep(1)
        await asyncio.sleep(1.5)
        stop.set()
        await sampling
        summary = await summarize(engine, provider, run)
    await engine.dispose()

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(summary, indent=2, default=str))
    brief = {k: v for k, v in summary.items() if k not in ("samples",)}
    print(json.dumps(brief, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
