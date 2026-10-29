"""Prompt engine endpoints (extra to the graded API).

POST /prompts/check   score a prompt without generating anything: decision, per-category
                      scores, the rule hits behind them, and the thresholds. Recorded in
                      prompt_events like any decision (hash only, never the text).
GET  /prompts/events  recent prompt decisions in the caller's workspace (no prompt text).
"""
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.identity import Caller, require_member
from app.schemas import PromptCheckIn
from app.screening import prompt_audit, prompt_gate

router = APIRouter()


@router.post("/prompts/check")
async def check_prompt(body: PromptCheckIn, caller: Caller = Depends(require_member),
                       session: AsyncSession = Depends(get_session)):
    a = prompt_gate.assess(body.prompt, body.negative_prompt)
    async with session.begin():
        await prompt_audit.record(session, workspace_id=caller.workspace_id, user_id=caller.user_id, kind="check",
                                  prompt=body.prompt, negative_prompt=body.negative_prompt, assessment=a)
    return {
        "decision": a.decision,
        "reasons": a.reasons,
        "scores": a.scores,
        "hits": a.hits,
        "thresholds": {"refuse": prompt_gate.REFUSE_AT, "review": prompt_gate.REVIEW_AT},
        "categories": list(prompt_gate.CATEGORIES),
    }


@router.get("/prompts/events")
async def prompt_events(limit: int = 30, caller: Caller = Depends(require_member),
                        session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(text(
        """
        SELECT id, user_id, media_id, kind, decision, reasons, prompt_sha256, prompt_chars, has_negative, created_at
        FROM prompt_events WHERE workspace_id = :ws ORDER BY created_at DESC LIMIT :n
        """
    ), {"ws": caller.workspace_id, "n": max(1, min(limit, 200))})).mappings().all()
    counts = (await session.execute(text(
        "SELECT decision, count(*) FROM prompt_events WHERE workspace_id = :ws GROUP BY decision"
    ), {"ws": caller.workspace_id})).all()
    return {"counts": {d: n for d, n in counts},
            "events": [{**dict(r), "prompt_sha256": r["prompt_sha256"][:12]} for r in rows]}
