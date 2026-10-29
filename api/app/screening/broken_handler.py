"""DELIBERATELY BROKEN v0 screening handler. Selected by SCREENING_MODE=broken.

This file reproduces last Tuesday's incident on purpose so the regression
tests can fail against it first. Do not use it for anything else.

The five bugs, each marked BUG(n) below:
1. a provider timeout is treated as a pass, so the media is published
2. only the first 8 frames of a video are sent and decided on
3. generation prompts are never checked (see routes/media.py)
4. a job is "redelivered" while the first attempt is still running:
   no row lock, no lease token, and a 2 second visibility timeout
5. the review page shows a failed request as an empty list (web/src/ReviewQueue.tsx)

v0 also wrote its decision as if it had covered the whole video. That is why
the database guard alone did not stop it: the guard trusts the decision row,
and v0 filled that row in with a lie.
"""
import asyncio
import json
import uuid
from typing import Any

from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal
from app.logging import log
from app.screening import provider_client
from app.screening.policy import POLICY_VERSION, decide

FIRST_FRAMES_ONLY = 8          # BUG(2)
VISIBILITY_TIMEOUT_SECONDS = 2  # BUG(4): shorter than a slow provider call


async def claim_job(worker_id: str) -> int | None:
    """BUG(4): plain SELECT then UPDATE. No FOR UPDATE SKIP LOCKED, no lease
    token, and a running job counts as abandoned after 2 seconds."""
    async with SessionLocal() as session, session.begin():
        job_id = (await session.execute(text(
            """
            SELECT id FROM jobs
            WHERE (state = 'queued' AND run_at <= now())
               OR (state = 'running' AND updated_at < now() - make_interval(secs => :vis))
            ORDER BY run_at, id
            LIMIT 1
            """
        ), {"vis": VISIBILITY_TIMEOUT_SECONDS})).scalar()
        if job_id is None:
            return None
        await session.execute(text(
            "UPDATE jobs SET state = 'running', attempts = attempts + 1, updated_at = now() WHERE id = :id"
        ), {"id": job_id})
    log.info("job_claimed", job_id=job_id, worker_id=worker_id, mode="broken")
    return job_id


async def _load(job_id: int) -> dict[str, Any] | None:
    async with SessionLocal() as session:
        row = (await session.execute(text(
            """
            SELECT j.id AS job_id, j.attempts, m.id AS media_id, m.workspace_id, m.frames,
                   m.frame_count, w.verified
            FROM jobs j JOIN media m ON m.id = j.media_id JOIN workspaces w ON w.id = m.workspace_id
            WHERE j.id = :id
            """
        ), {"id": job_id})).mappings().first()
        return dict(row) if row else None


async def _record_decision(media_id: uuid.UUID, frame_count: int, scored: list[dict[str, float]],
                           verified: bool, job_id: int | None) -> str:
    decision = decide(scored, verified)
    decision_id = uuid.uuid4()
    async with SessionLocal() as session, session.begin():
        # v0 claims it covered every frame, whatever it actually scored.
        await session.execute(text(
            """
            INSERT INTO decisions (id, media_id, status, reasons, frames_scored, frame_count,
                                   policy_version, decided_by)
            VALUES (:id, :m, :s, CAST(:r AS jsonb), :n, :n, :pv, 'policy')
            """
        ), {"id": decision_id, "m": media_id, "s": decision.status,
            "r": _json(decision.reasons), "n": frame_count, "pv": POLICY_VERSION})
        await session.execute(text(
            """
            UPDATE media SET status = :s, reasons = CAST(:r AS jsonb), is_explicit = :e,
                   held_reason_kind = CASE WHEN :s = 'held' THEN 'score' END,
                   decision_id = :d
            WHERE id = :m
            """
        ), {"s": decision.status, "r": _json(decision.reasons), "e": decision.is_explicit,
            "d": decision_id, "m": media_id})
        if job_id is not None:
            await session.execute(text("UPDATE jobs SET state = 'done', updated_at = now() WHERE id = :id"),
                                  {"id": job_id})
    return decision.status


def _json(value: Any) -> str:
    return json.dumps(value)


async def process_job(job_id: int, worker_id: str = "test") -> None:
    job = await _load(job_id)
    if job is None:
        return
    bind = {"job_id": job_id, "media_id": str(job["media_id"]), "workspace_id": job["workspace_id"],
            "attempt": job["attempts"], "mode": "broken"}
    frames = job["frames"][:FIRST_FRAMES_ONLY]  # BUG(2): a prefix, not the whole video
    outcome = await provider_client.score(str(job["media_id"]), frames, validate=False)
    log.info("provider_answered", outcome=outcome.kind, http_status=outcome.http_status, **bind)

    if outcome.kind == "timeout":
        scored: list[dict[str, float]] = [{} for _ in frames]  # BUG(1): no answer read as "all zero"
    elif outcome.kind == "ok" and outcome.frames is not None:
        scored = outcome.frames
    else:
        async with SessionLocal() as session, session.begin():
            await session.execute(text(
                "UPDATE jobs SET state = 'queued', run_at = now() + interval '1 second', "
                "last_error = :e, updated_at = now() WHERE id = :id"
            ), {"id": job_id, "e": outcome.kind})
        log.warning("job_retry", outcome=outcome.kind, **bind)
        return

    status = await _record_decision(job["media_id"], job["frame_count"], scored, job["verified"], job_id)
    log.info("media_decided", status=status, frames_scored=len(scored), frame_count=job["frame_count"], **bind)


async def process_callback(payload: dict[str, Any]) -> None:
    """v0 never sends more than 8 frames, so the provider always answers
    synchronously and this path is rarely hit. It applies whatever arrives,
    with no duplicate check."""
    media_id = uuid.UUID(str(payload["media_id"]))
    async with SessionLocal() as session:
        row = (await session.execute(text(
            "SELECT m.frame_count, w.verified FROM media m JOIN workspaces w ON w.id = m.workspace_id "
            "WHERE m.id = :id"
        ), {"id": media_id})).first()
    if row is None:
        return
    scored = [f.get("scores", {}) for f in payload.get("frames", [])]
    await _record_decision(media_id, row.frame_count, scored, row.verified, None)


async def run(worker_id: str) -> None:
    log.warning("broken_handler_running", worker_id=worker_id,
                note="SCREENING_MODE=broken reproduces the incident on purpose")
    while True:
        job_id = await claim_job(worker_id)
        if job_id is None:
            await asyncio.sleep(0.2)
            continue
        try:
            await process_job(job_id, worker_id)
        except Exception:  # v0 swallowed errors and moved on
            log.exception("job_crashed", job_id=job_id, worker_id=worker_id)


__all__ = ["claim_job", "process_job", "process_callback", "run", "settings"]
