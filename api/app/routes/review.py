import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.identity import Caller, require_admin
from app.logging import log
from app.routes.media import parse_media_id
from app.schemas import MediaOut, ReviewItem
from app.screening.policy import POLICY_VERSION

router = APIRouter()

NOT_APPROVABLE = {
    "screening_unavailable": "This item was never screened (the provider was unavailable). "
                             "It is re-screened automatically; approving it would publish unchecked content.",
    "provider_malformed": "The provider returned an invalid answer for this item. "
                          "It needs a fresh screening, not an approval.",
}


@router.get("/review", response_model=list[ReviewItem])
async def list_review(caller: Caller = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(text(
        """
        SELECT id, status, reasons, created_at, held_reason_kind FROM media
        WHERE workspace_id = :ws AND status = 'held'
        ORDER BY created_at
        """
    ), {"ws": caller.workspace_id})).mappings().all()
    return [ReviewItem(**r) for r in rows]


async def _review(media_id: str, action: str, caller: Caller, session: AsyncSession) -> MediaOut:
    mid = parse_media_id(media_id)
    async with session.begin():
        media = (await session.execute(text(
            """
            SELECT m.id, m.status, m.held_reason_kind, m.frame_count, d.frames_scored
            FROM media m LEFT JOIN decisions d ON d.id = m.decision_id
            WHERE m.id = :id AND m.workspace_id = :ws
            FOR UPDATE OF m
            """
        ), {"id": mid, "ws": caller.workspace_id})).first()
        if media is None:
            raise HTTPException(status_code=404, detail="Not found")
        if media.status != "held":
            raise HTTPException(status_code=409, detail=f"Only held media can be reviewed; this is {media.status}")
        if action == "approve" and media.held_reason_kind in NOT_APPROVABLE:
            raise HTTPException(status_code=409, detail=NOT_APPROVABLE[media.held_reason_kind])

        status = "published" if action == "approve" else "blocked"
        reasons = [f"reviewed:{action}d_by={caller.user_id}"]
        decision_id = uuid.uuid4()
        await session.execute(text(
            """
            INSERT INTO decisions (id, media_id, status, reasons, frames_scored, frame_count,
                                   policy_version, decided_by)
            VALUES (:id, :m, :s, CAST(:r AS jsonb), :scored, :n, :pv, :by)
            """
        ), {"id": decision_id, "m": mid, "s": status, "r": json.dumps(reasons),
            "scored": media.frames_scored or 0, "n": media.frame_count, "pv": POLICY_VERSION,
            "by": caller.user_id})
        await session.execute(text(
            """
            UPDATE media SET status = :s, reasons = CAST(:r AS jsonb), held_reason_kind = NULL,
                   rescreen_pending = false, decision_id = :d
            WHERE id = :m
            """
        ), {"s": status, "r": json.dumps(reasons), "d": decision_id, "m": mid})
        await session.execute(text(
            "INSERT INTO review_actions (media_id, user_id, action) VALUES (:m, :u, :a)"
        ), {"m": mid, "u": caller.user_id, "a": action})
    log.info("media_reviewed", media_id=str(mid), workspace_id=caller.workspace_id,
             user_id=caller.user_id, action=action, status=status)
    return MediaOut(id=mid, status=status, reasons=reasons)


@router.post("/review/{media_id}/approve", response_model=MediaOut)
async def approve(media_id: str, caller: Caller = Depends(require_admin),
                  session: AsyncSession = Depends(get_session)):
    return await _review(media_id, "approve", caller, session)


@router.post("/review/{media_id}/reject", response_model=MediaOut)
async def reject(media_id: str, caller: Caller = Depends(require_admin),
                 session: AsyncSession = Depends(get_session)):
    return await _review(media_id, "reject", caller, session)
