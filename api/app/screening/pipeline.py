"""The fixed screening pipeline (SCREENING_MODE=fixed).

For one claimed job:
1. Already scored (a previous attempt stored scores, then died)? Decide from those.
2. A previous attempt's call still marked in_flight? That worker lost its
   lease, so the call is marked abandoned and we pay for one more.
3. Breaker or 429 cooldown says no? Reschedule; hold once we have waited
   HOLD_AFTER_SECONDS.
4. Write the provider_calls row, COMMIT, then call. The ledger is written
   before the money is spent.
5. Each outcome has its own path and budget. Running out of budget holds the
   media. Nothing on any failure path can publish.
6. Success: one transaction, guarded by the fencing token, stores scores,
   runs decide(), writes the decision and the media status, finishes the job.
"""
import asyncio
import json
import os
import time
import uuid
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db import SessionLocal
from app.logging import log
from app.screening import breaker, provider_client, queue
from app.screening.policy import POLICY_VERSION, decide
from app.screening.provider_client import MalformedResponse, ProviderOutcome, validate_frames
from app.screening.queue import ClaimedJob

SYNC_FRAME_LIMIT = 30
FAILURE_OUTCOMES = {"timeout", "server_error"}


def _scores_only(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"scores": f.get("scores", {}) or {}} for f in frames]


# ---------------------------------------------------------------- final writes

async def finalize(media_id: uuid.UUID, token: uuid.UUID, frames: list[dict[str, float]],
                   provider_call_id: uuid.UUID | None, job_id: int | None) -> str | None:
    """Store scores and the decision in ONE transaction, fenced by `token`.
    Returns the new status, or None if this writer is stale (token mismatch)
    or the media was already decided."""
    async with SessionLocal() as session, session.begin():
        media = (await session.execute(text(
            """
            SELECT m.status, m.frame_count, m.screening_token, w.verified
            FROM media m JOIN workspaces w ON w.id = m.workspace_id
            WHERE m.id = :m FOR UPDATE OF m
            """
        ), {"m": media_id})).mappings().first()
        if media is None or media["screening_token"] != token or media["status"] != "pending":
            return None
        if len(frames) != media["frame_count"]:
            raise MalformedResponse(f"scores cover {len(frames)} of {media['frame_count']} frames")

        stored = (await session.execute(text(
            """
            INSERT INTO scores (media_id, provider_call_id, frames) VALUES (:m, :c, CAST(:f AS jsonb))
            ON CONFLICT (media_id) DO NOTHING RETURNING media_id
            """
        ), {"m": media_id, "c": provider_call_id, "f": json.dumps(frames)})).first()
        if stored is None:  # scored before (crash after save, or a late duplicate): reuse those scores
            frames = (await session.execute(text("SELECT frames FROM scores WHERE media_id = :m"),
                                            {"m": media_id})).scalar_one()

        decision = decide(frames, media["verified"])
        decision_id = uuid.uuid4()
        await session.execute(text(
            """
            INSERT INTO decisions (id, media_id, status, reasons, frames_scored, frame_count,
                                   policy_version, decided_by)
            VALUES (:id, :m, :s, CAST(:r AS jsonb), :n, :n, :pv, 'policy')
            """
        ), {"id": decision_id, "m": media_id, "s": decision.status, "r": json.dumps(decision.reasons),
            "n": len(frames), "pv": POLICY_VERSION})
        await session.execute(text(
            """
            UPDATE media SET status = :s, reasons = CAST(:r AS jsonb), is_explicit = :e,
                   held_reason_kind = CASE WHEN :s = 'held' THEN 'score' END,
                   rescreen_pending = false, decision_id = :d
            WHERE id = :m AND screening_token = :token
            """
        ), {"s": decision.status, "r": json.dumps(decision.reasons), "e": decision.is_explicit,
            "d": decision_id, "m": media_id, "token": token})
        await session.execute(text(
            """
            UPDATE jobs SET state = 'done', lease_until = NULL, updated_at = now()
            WHERE media_id = :m AND state IN ('queued', 'running', 'waiting_callback')
            """
        ), {"m": media_id})
    log.info("media_decided", status=decision.status, reasons=decision.reasons,
             frames_scored=len(frames), frame_count=media["frame_count"], job_id=job_id)
    return decision.status


async def hold(job: ClaimedJob, kind: str, reason: str) -> bool:
    """Retries exhausted or provider unusable: held, never published."""
    rescreen = kind == "screening_unavailable"
    async with SessionLocal() as session, session.begin():
        updated = await session.execute(text(
            """
            UPDATE media SET status = 'held', held_reason_kind = :kind, rescreen_pending = :rescreen,
                   reasons = CAST(:r AS jsonb)
            WHERE id = :m AND screening_token = :token AND status = 'pending'
            """
        ), {"kind": kind, "rescreen": rescreen, "r": json.dumps([reason]), "m": job.media_id,
            "token": job.lease_token})
        if updated.rowcount != 1:
            return False
        await session.execute(text(
            """
            UPDATE jobs SET state = 'dead', lease_until = NULL, last_error = :e, updated_at = now()
            WHERE id = :id AND lease_token = :token
            """
        ), {"id": job.id, "token": job.lease_token, "e": reason})
    level = log.error if kind == "provider_malformed" else log.warning
    level("media_held", held_reason_kind=kind, reason=reason, rescreen_pending=rescreen)
    return True


# ---------------------------------------------------------------- ledger

async def open_call(job: ClaimedJob, kind: str) -> uuid.UUID:
    call_id = uuid.uuid4()
    async with SessionLocal() as session, session.begin():
        # A call still in flight from an attempt whose lease expired will never be
        # recorded by its worker. Mark it so the ledger stays honest.
        await session.execute(text(
            """
            UPDATE provider_calls SET outcome = 'abandoned', finished_at = now()
            WHERE media_id = :m AND outcome = 'in_flight'
            """
        ), {"m": job.media_id})
        await session.execute(text(
            """
            INSERT INTO provider_calls (id, media_id, job_id, attempt, kind)
            VALUES (:id, :m, :j, :a, :k)
            """
        ), {"id": call_id, "m": job.media_id, "j": job.id, "a": job.attempts, "k": kind})
    return call_id


async def close_call(call_id: uuid.UUID, outcome: ProviderOutcome) -> None:
    ledger_outcome = {"accepted": "accepted"}.get(outcome.kind, outcome.kind)
    async with SessionLocal() as session, session.begin():
        await session.execute(text(
            """
            UPDATE provider_calls SET outcome = :o, http_status = :s, provider_job_id = :pj,
                   finished_at = now()
            WHERE id = :id
            """
        ), {"id": call_id, "o": ledger_outcome, "s": outcome.http_status, "pj": outcome.provider_job_id})


# ---------------------------------------------------------------- one job

async def _existing_scores(media_id: uuid.UUID) -> list[dict[str, float]] | None:
    async with SessionLocal() as session:
        return (await session.execute(text("SELECT frames FROM scores WHERE media_id = :m"),
                                      {"m": media_id})).scalar()


async def _maybe_hold_for_outage(job: ClaimedJob) -> bool:
    """Breaker is open (or the provider asked us to back off). Wait, but not forever."""
    waited = time.time() - job.created_at.timestamp()
    if waited >= settings.hold_after_seconds:
        return await hold(job, "screening_unavailable", "screening_unavailable:provider_down")
    await queue.reschedule(job, min(2.0, settings.breaker_open_seconds), "breaker_open")
    return False


async def process(job: ClaimedJob, worker_id: str = "") -> None:
    with structlog.contextvars.bound_contextvars(
        job_id=job.id, media_id=str(job.media_id), workspace_id=job.workspace_id,
        lane=job.lane, attempt=job.attempts,
    ):
        started = time.monotonic()
        log.info("job_claimed", worker_id=worker_id, frame_count=job.frame_count)
        existing = await _existing_scores(job.media_id)
        if existing is not None:
            log.info("scores_already_stored", note="previous attempt saved scores; deciding without a new call")
            await finalize(job.media_id, job.lease_token, existing, None, job.id)
            return

        if not await breaker.allow():
            await _maybe_hold_for_outage(job)
            return

        kind = "sync" if job.frame_count <= SYNC_FRAME_LIMIT else "async_submit"
        call_id = await open_call(job, kind)
        with structlog.contextvars.bound_contextvars(provider_call_id=str(call_id)):
            outcome = await provider_client.score(
                str(job.media_id), _scores_only(job.frames), callback_url=settings.callback_url)
            if settings.crash_after_provider and outcome.kind in ("ok", "accepted"):
                log.warning("crash_after_provider", note="CRASH_AFTER_PROVIDER=1: exiting before the save")
                os._exit(137)  # the provider scored (and billed) it; we die before recording anything
            await close_call(call_id, outcome)
            log.info("provider_answered", outcome=outcome.kind, http_status=outcome.http_status,
                     duration_ms=round((time.monotonic() - started) * 1000), error=outcome.error)
            await _handle(job, call_id, outcome)


async def _handle(job: ClaimedJob, call_id: uuid.UUID, outcome: ProviderOutcome) -> None:
    if outcome.kind == "ok" and outcome.frames is not None:
        await breaker.record_success()
        try:
            await finalize(job.media_id, job.lease_token, outcome.frames, call_id, job.id)
        except MalformedResponse as exc:
            await _malformed(job, str(exc))
        return

    if outcome.kind == "accepted" and outcome.provider_job_id:
        await breaker.record_success()
        if await queue.mark_waiting_callback(job, outcome.provider_job_id):
            log.info("waiting_callback", provider_job_id=outcome.provider_job_id)
        return

    async with SessionLocal() as session:
        counts = await queue.failures_for_job(session, job.id)

    if outcome.kind in FAILURE_OUTCOMES:
        await breaker.record_failure()
        failures = sum(counts.get(k, 0) for k in FAILURE_OUTCOMES)
        # A re-screen is itself a probe of a provider that just failed: one try,
        # then back to held. The loop will try again later at no extra cost.
        budget = 1 if job.lane == "rescreen" else settings.max_failure_attempts
        if failures >= budget:
            await hold(job, "screening_unavailable", f"screening_unavailable:{outcome.kind}")
            return
        delay = queue.backoff_seconds(failures)
        await queue.reschedule(job, delay, outcome.kind)
        log.info("job_retry_scheduled", outcome=outcome.kind, failures=failures, delay_s=round(delay, 2))
        return

    if outcome.kind == "rate_limited":
        retry_after = outcome.retry_after or 1.0
        await breaker.set_cooldown(retry_after)
        if counts.get("rate_limited", 0) >= settings.max_rate_limited_attempts:
            await hold(job, "screening_unavailable", "screening_unavailable:rate_limited")
            return
        await queue.reschedule(job, retry_after, "rate_limited")
        log.info("job_retry_scheduled", outcome="rate_limited", retry_after_s=retry_after)
        return

    await _malformed(job, outcome.error or "malformed")


async def _malformed(job: ClaimedJob, error: str) -> None:
    async with SessionLocal() as session:
        counts = await queue.failures_for_job(session, job.id)
    log.error("provider_malformed", error=error)
    if counts.get("malformed", 0) >= settings.max_malformed_attempts:
        await hold(job, "provider_malformed", "provider_malformed")
        return
    await queue.reschedule(job, 0.2, "malformed")


# ---------------------------------------------------------------- callbacks

async def process_callback(payload: dict[str, Any], body_sha256: str) -> str:
    """Signed callback for an async video. A duplicate is a no-op."""
    provider_job_id = str(payload.get("provider_job_id") or "")
    if not provider_job_id:
        return "ignored:no_provider_job_id"
    async with SessionLocal() as session:
        call = (await session.execute(text(
            "SELECT id, media_id, job_id FROM provider_calls WHERE provider_job_id = :pj"
        ), {"pj": provider_job_id})).mappings().first()
    if call is None:
        log.warning("callback_unknown_job", provider_job_id=provider_job_id)
        return "ignored:unknown_provider_job"

    with structlog.contextvars.bound_contextvars(media_id=str(call["media_id"]), job_id=call["job_id"],
                                                 provider_call_id=str(call["id"]),
                                                 provider_job_id=provider_job_id):
        try:
            async with SessionLocal() as session, session.begin():
                await session.execute(text(
                    """
                    INSERT INTO callback_receipts (provider_job_id, media_id, body_sha256)
                    VALUES (:pj, :m, :h)
                    """
                ), {"pj": provider_job_id, "m": call["media_id"], "h": body_sha256})
        except IntegrityError:
            log.info("callback_duplicate_ignored")
            return "duplicate"

        async with SessionLocal() as session:
            media = (await session.execute(text(
                "SELECT frame_count, screening_token, status FROM media WHERE id = :m"
            ), {"m": call["media_id"]})).mappings().one()
        if media["status"] != "pending":
            log.info("callback_after_decision", status=media["status"])
            return "already_decided"
        try:
            frames = validate_frames(payload, media["frame_count"])
        except MalformedResponse as exc:
            log.error("provider_malformed", error=str(exc), via="callback")
            await _requeue_after_bad_callback(call["media_id"], str(exc))
            return "malformed"
        status = await finalize(call["media_id"], media["screening_token"], frames, call["id"], call["job_id"])
        return status or "stale"


async def _requeue_after_bad_callback(media_id: uuid.UUID, error: str) -> None:
    async with SessionLocal() as session, session.begin():
        await session.execute(text(
            """
            UPDATE jobs SET state = 'queued', run_at = now(), provider_job_id = NULL,
                   last_error = :e, updated_at = now()
            WHERE media_id = :m AND state = 'waiting_callback'
            """
        ), {"m": media_id, "e": f"malformed_callback:{error}"[:300]})
        # Count it against the malformed budget for that job.
        await session.execute(text(
            """
            UPDATE provider_calls SET outcome = 'malformed' WHERE media_id = :m AND outcome = 'accepted'
            """
        ), {"m": media_id})


# ---------------------------------------------------------------- worker loops

async def lane_loop(lane: str, worker_id: str, idle: float = 0.1) -> None:
    while True:
        try:
            job = await queue.claim(lane)
        except Exception:
            log.exception("claim_failed", lane=lane, worker_id=worker_id)
            await asyncio.sleep(1)
            continue
        if job is None:
            await asyncio.sleep(idle)
            continue
        try:
            await process(job, worker_id)
        except Exception:
            # The lease runs out and another worker retries. Media stays pending (invisible).
            log.exception("job_crashed", job_id=job.id, media_id=str(job.media_id), worker_id=worker_id)
