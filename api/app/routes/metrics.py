"""GET /metrics/summary: live numbers for the review page and the workload.

Internal: no identity headers, and it returns counts only, never media ids or
content. In production this would sit behind the internal network or an
admin token.
"""
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import generator
from app.db import get_session
from app.screening import breaker

router = APIRouter()


@router.get("/metrics/summary")
async def summary(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    status_rows = (await session.execute(text("SELECT status, count(*) FROM media GROUP BY status"))).all()
    held_rows = (await session.execute(text(
        "SELECT held_reason_kind, count(*) FROM media WHERE status = 'held' GROUP BY held_reason_kind"
    ))).all()
    prompt_rows = (await session.execute(text(
        "SELECT decision, count(*) FROM prompt_events WHERE kind = 'generation' GROUP BY decision"
    ))).all()
    queue_rows = (await session.execute(text(
        """
        SELECT lane, workspace_id, state, count(*) FROM jobs
        WHERE state IN ('queued', 'running', 'waiting_callback')
        GROUP BY lane, workspace_id, state
        """
    ))).all()
    row = (await session.execute(text(
        """
        SELECT
          (SELECT EXTRACT(EPOCH FROM now() - min(created_at)) FROM jobs
             WHERE state IN ('queued', 'running', 'waiting_callback')) AS oldest_waiting_job_s,
          (SELECT EXTRACT(EPOCH FROM now() - min(updated_at)) FROM media
             WHERE status = 'held') AS oldest_held_s,
          (SELECT count(*) FROM media m
             LEFT JOIN decisions d ON d.id = m.decision_id
             LEFT JOIN scores s ON s.media_id = m.id
             WHERE m.status = 'published'
               AND (d.id IS NULL OR d.frames_scored <> m.frame_count
                    OR s.media_id IS NULL OR jsonb_array_length(s.frames) <> m.frame_count)
          ) AS unchecked_published,
          (SELECT count(*) FROM provider_calls) AS provider_calls,
          (SELECT count(DISTINCT media_id) FROM provider_calls) AS media_with_calls
        """
    ))).mappings().one()

    queue: dict[str, dict[str, dict[str, int]]] = {}
    for lane, ws, state, n in queue_rows:
        queue.setdefault(lane, {}).setdefault(ws, {})[state] = n
    calls, media = int(row["provider_calls"]), int(row["media_with_calls"])
    return {
        "media_by_status": {s: n for s, n in status_rows},
        "held_by_reason": {k: n for k, n in held_rows},
        "queue_depth": queue,
        "oldest_waiting_job_s": round(float(row["oldest_waiting_job_s"] or 0), 1),
        "oldest_held_s": round(float(row["oldest_held_s"] or 0), 1),
        "unchecked_published": int(row["unchecked_published"]),
        "provider_calls_per_screened_media": round(calls / media, 3) if media else None,
        "breaker": await breaker.state(),
        "generation_prompts_by_decision": {d: n for d, n in prompt_rows},
        "generator_calls_this_process": generator.total_calls(),
    }
