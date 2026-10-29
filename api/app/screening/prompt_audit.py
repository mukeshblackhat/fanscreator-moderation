"""Audit of prompt decisions. Stores a SHA-256 and the length of the prompt, never its text:
enough to spot an account that keeps probing, without keeping what was asked for."""
import hashlib
import json
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.screening.prompt_gate import PromptAssessment


async def record(session: AsyncSession, *, workspace_id: str, user_id: str, kind: str, prompt: str,
                 negative_prompt: str | None, assessment: PromptAssessment,
                 media_id: uuid.UUID | None = None) -> None:
    await session.execute(text(
        """
        INSERT INTO prompt_events (workspace_id, user_id, media_id, kind, decision, scores, reasons,
                                   prompt_sha256, prompt_chars, has_negative)
        VALUES (:ws, :u, :m, :k, :d, CAST(:s AS jsonb), CAST(:r AS jsonb), :h, :n, :neg)
        """
    ), {"ws": workspace_id, "u": user_id, "m": media_id, "k": kind, "d": assessment.decision,
        "s": json.dumps(assessment.scores), "r": json.dumps(assessment.reasons),
        "h": hashlib.sha256(prompt.encode()).hexdigest(), "n": len(prompt), "neg": bool(negative_prompt)})
