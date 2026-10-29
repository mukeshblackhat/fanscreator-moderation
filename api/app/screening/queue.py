"""Job queue on PostgreSQL.

* claim: FOR UPDATE SKIP LOCKED, so two workers can never hold the same job.
  Each claim takes a lease and a fresh lease_token, and copies the token to
  media.screening_token (the fencing token).
* fairness: within a lane, the next job comes from the workspace with the
  fewest jobs running, and no workspace may have more than
  WORKSPACE_INFLIGHT_CAP jobs running in that lane.
* an expired lease makes the job claimable again (worker died). The old
  worker's writes are then rejected because its token no longer matches.
"""
import random
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal

ACTIVE_STATES = ("queued", "running", "waiting_callback")


@dataclass
class ClaimedJob:
    id: int
    media_id: uuid.UUID
    workspace_id: str
    lane: str
    attempts: int
    lease_token: uuid.UUID
    created_at: datetime
    frames: list[dict[str, Any]]
    frame_count: int
    verified: bool


CLAIM_SQL = text(
    """
    WITH running AS (
        SELECT workspace_id, count(*) AS n
        FROM jobs
        WHERE lane = :lane AND state = 'running' AND lease_until > now()
        GROUP BY workspace_id
    ),
    candidate AS (
        SELECT j.id
        FROM jobs j
        LEFT JOIN running r ON r.workspace_id = j.workspace_id
        WHERE j.lane = :lane
          AND ((j.state = 'queued' AND j.run_at <= now())
               OR (j.state = 'running' AND j.lease_until <= now()))
          AND coalesce(r.n, 0) < :cap
        ORDER BY coalesce(r.n, 0), j.run_at, j.id
        LIMIT 1
        FOR UPDATE OF j SKIP LOCKED
    )
    UPDATE jobs SET state = 'running',
                    attempts = jobs.attempts + 1,
                    lease_until = now() + make_interval(secs => :lease),
                    lease_token = :token,
                    updated_at = now()
    FROM candidate
    WHERE jobs.id = candidate.id
    RETURNING jobs.id, jobs.media_id, jobs.workspace_id, jobs.lane, jobs.attempts, jobs.created_at
    """
)


async def claim(lane: str) -> ClaimedJob | None:
    token = uuid.uuid4()
    async with SessionLocal() as session, session.begin():
        row = (await session.execute(CLAIM_SQL, {
            "lane": lane, "cap": settings.workspace_inflight_cap,
            "lease": settings.lease_seconds, "token": token,
        })).mappings().first()
        if row is None:
            return None
        media = (await session.execute(text(
            """
            UPDATE media SET screening_token = :token WHERE id = :m
            RETURNING frames, frame_count, (SELECT verified FROM workspaces w WHERE w.id = media.workspace_id) AS verified
            """
        ), {"token": token, "m": row["media_id"]})).mappings().one()
    return ClaimedJob(id=row["id"], media_id=row["media_id"], workspace_id=row["workspace_id"],
                      lane=row["lane"], attempts=row["attempts"], lease_token=token,
                      created_at=row["created_at"], frames=media["frames"],
                      frame_count=media["frame_count"], verified=media["verified"])


def backoff_seconds(failures: int) -> float:
    """Exponential backoff with full jitter."""
    ceiling = min(settings.backoff_cap_seconds, settings.backoff_base_seconds * (2 ** failures))
    return random.uniform(0, ceiling)


async def reschedule(job: ClaimedJob, delay: float, error: str) -> bool:
    """Give the job back to the queue. Only the current lease holder can."""
    async with SessionLocal() as session, session.begin():
        result = await session.execute(text(
            """
            UPDATE jobs SET state = 'queued', run_at = now() + make_interval(secs => :delay),
                   lease_until = NULL, last_error = :error, updated_at = now()
            WHERE id = :id AND lease_token = :token AND state = 'running'
            """
        ), {"id": job.id, "token": job.lease_token, "delay": delay, "error": error})
    return result.rowcount == 1


async def mark_waiting_callback(job: ClaimedJob, provider_job_id: str) -> bool:
    async with SessionLocal() as session, session.begin():
        result = await session.execute(text(
            """
            UPDATE jobs SET state = 'waiting_callback', provider_job_id = :pj,
                   lease_until = NULL, updated_at = now()
            WHERE id = :id AND lease_token = :token AND state = 'running'
            """
        ), {"id": job.id, "token": job.lease_token, "pj": provider_job_id})
    return result.rowcount == 1


async def failures_for_job(session: AsyncSession, job_id: int) -> dict[str, int]:
    rows = (await session.execute(text(
        "SELECT outcome, count(*) FROM provider_calls WHERE job_id = :j GROUP BY outcome"
    ), {"j": job_id})).all()
    return {outcome: n for outcome, n in rows}


async def enqueue(session: AsyncSession, media_id: uuid.UUID, workspace_id: str, lane: str) -> None:
    await session.execute(text(
        "INSERT INTO jobs (media_id, workspace_id, lane) VALUES (:m, :ws, :lane)"
    ), {"m": media_id, "ws": workspace_id, "lane": lane})
