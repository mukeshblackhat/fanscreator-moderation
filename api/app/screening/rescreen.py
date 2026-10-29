"""Recovery without a human.

Every RESCREEN_INTERVAL_SECONDS, one worker (Postgres advisory lock) does:

1. Re-screen: media held as `screening_unavailable` goes back to `pending`
   with a job in the `rescreen` lane. Oldest first, taking turns across
   workspaces, at most RESCREEN_BATCH_SIZE per tick so recovery does not
   set off a burst of 429s. While the breaker is open we release only one
   item per tick: it acts as the probe that tells us the provider is back.
2. Callback watchdog: an async video whose callback never arrived within
   CALLBACK_TIMEOUT_SECONDS is resubmitted.

Only items held BEFORE the provider's last successful answer are released in
batches. With no success since, one item is released per probe window, so a
quiet outage (too little traffic to trip the breaker) does not turn into a
loop of paid retries. This can cost a second provider
   call; without it the video would wait forever.
"""
import asyncio
from datetime import datetime, timezone

from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal
from app.logging import log
from app.screening import breaker

LOCK_KEY = 0x5C3EE4  # advisory lock id for "rescreen leader"

RESCREEN_SQL = text(
    """
    WITH ranked AS (
        SELECT id, workspace_id, created_at,
               row_number() OVER (PARTITION BY workspace_id ORDER BY created_at) AS turn
        FROM media
        WHERE status = 'held' AND rescreen_pending
          AND (CAST(:since AS timestamptz) IS NULL OR updated_at < CAST(:since AS timestamptz))
    ),
    picked AS (
        SELECT m.id, m.workspace_id
        FROM ranked r JOIN media m ON m.id = r.id
        ORDER BY r.turn, r.created_at
        LIMIT :limit
        FOR UPDATE OF m SKIP LOCKED
    ),
    reset AS (
        UPDATE media SET status = 'pending', held_reason_kind = NULL, rescreen_pending = false,
               reasons = '[]'::jsonb
        FROM picked WHERE media.id = picked.id
        RETURNING media.id, media.workspace_id
    )
    INSERT INTO jobs (media_id, workspace_id, lane)
    SELECT id, workspace_id, 'rescreen' FROM reset
    RETURNING media_id
    """
)

WATCHDOG_SQL = text(
    """
    UPDATE jobs SET state = 'queued', run_at = now(), provider_job_id = NULL,
           last_error = 'callback_timeout', updated_at = now()
    WHERE state = 'waiting_callback' AND updated_at < now() - make_interval(secs => :timeout)
    RETURNING id, media_id
    """
)


async def tick() -> dict[str, int]:
    async with SessionLocal() as session, session.begin():
        leader = (await session.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": LOCK_KEY})).scalar()
        if not leader:
            return {"rescreened": 0, "resubmitted": 0}
        state = await breaker.state()
        last_ok = await breaker.last_success()
        since = datetime.fromtimestamp(last_ok, timezone.utc) if last_ok else None
        recovered = (await session.execute(text(
            """
            SELECT EXISTS (SELECT 1 FROM media WHERE status = 'held' AND rescreen_pending
                           AND updated_at < CAST(:since AS timestamptz))
            """
        ), {"since": since})).scalar() if since and state in ("closed", "unknown") else False
        waiting = (await session.execute(text(
            "SELECT EXISTS (SELECT 1 FROM media WHERE status = 'held' AND rescreen_pending)"
        ))).scalar()
        if recovered:
            # The provider answered after these items were held: release a batch.
            limit = settings.rescreen_batch_size
        elif waiting and await breaker.claim_rescreen_probe(settings.rescreen_probe_window_seconds):
            # No sign of recovery yet: one item acts as the probe.
            limit, since = 1, None
        else:
            limit = 0
        rescreened = (await session.execute(RESCREEN_SQL, {"limit": limit, "since": since})).all() if limit else []
        resubmitted = (await session.execute(WATCHDOG_SQL, {"timeout": settings.callback_timeout_seconds})).all()
    for r in rescreened:  # one line per item, so grepping a media_id shows its whole story
        log.info("rescreen_enqueued", media_id=str(r.media_id), breaker=state, batch=len(rescreened))
    for row in resubmitted:
        log.warning("callback_timeout_resubmit", job_id=row.id, media_id=str(row.media_id),
                    note="no callback in time; resubmitting may cost a second provider call")
    return {"rescreened": len(rescreened), "resubmitted": len(resubmitted)}


async def loop() -> None:
    while True:
        try:
            await tick()
        except Exception:
            log.exception("rescreen_tick_failed")
        await asyncio.sleep(settings.rescreen_interval_seconds)
