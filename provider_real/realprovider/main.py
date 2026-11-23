"""Real moderation provider (port 8002). Same contract as the fake provider,
but scores the actual pixels of each frame.

POST /score   {"media_id", "frames": [{"image": "<path under MEDIA_STORE>"}], "callback_url"}
              always 202 {"provider_job_id"}; the result is POSTed to callback_url,
              signed with HMAC-SHA256 over the raw body (hex, X-Signature).
              On any scoring failure the callback carries {"error": ...} and no
              frames, which the API treats as a malformed answer: retried, then held.
              Fail closed, never a partial answer.
GET  /info    which models are active, the detector behind each label, and the hold rules
GET  /_stats  calls per media id
"""
import asyncio
import hashlib
import hmac
import json
import os
import time
import uuid
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from PIL import Image
from pydantic import BaseModel

from realprovider import scorers

MEDIA_STORE = Path(os.environ.get("MEDIA_STORE", "/media-store")).resolve()
SECRET = os.environ.get("PROVIDER_WEBHOOK_SECRET", "change-me-in-real-life").encode()
MAX_FRAMES = 120

state: dict[str, Any] = {"tier1": None, "calls": Counter(), "tasks": set(), "lock": None, "loaded_at": None}


def log(event: str, **kw: Any) -> None:
    print(json.dumps({"event": event, "ts": round(time.time(), 3), **kw}), flush=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    state["lock"] = asyncio.Lock()
    started = time.monotonic()
    state["tier1"] = await asyncio.to_thread(scorers.load_tier1)
    state["loaded_at"] = time.time()
    log("model_loaded", tier1=state["tier1"].name, tier2=scorers.openai_enabled(),
        seconds=round(time.monotonic() - started, 1))
    yield


app = FastAPI(title="Real moderation provider", lifespan=lifespan)


class ScoreRequest(BaseModel):
    media_id: str
    frames: list[dict[str, Any]]
    callback_url: str | None = None


def frame_path(rel: str) -> Path:
    p = (MEDIA_STORE / rel).resolve()
    if MEDIA_STORE not in p.parents or not p.is_file():
        raise ValueError(f"bad frame path {rel!r}")
    return p


def sign(body: bytes) -> str:
    return hmac.new(SECRET, body, hashlib.sha256).hexdigest()


async def _score_frames(frames: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Tier 1 on every frame, tier 2 on the frames tier 1 does not clear. A frame within
    DEDUPE_BITS of the last frame sent to tier 2 reuses its answer instead of a new call."""
    images = []
    for f in frames:
        with Image.open(frame_path(str(f.get("image", "")))) as img:
            images.append(img.convert("RGB"))
    async with state["lock"]:  # one CPU inference at a time
        cues = await asyncio.to_thread(state["tier1"].cues, images)
    escalate = [i for i, c in enumerate(cues) if scorers.needs_tier2(c)] if scorers.openai_enabled() else []
    answer_from: dict[int, int] = {}  # escalated frame -> the frame whose tier-2 answer it uses
    last: tuple[int, int] | None = None  # (hash, index) of the last frame actually sent
    for i in escalate:
        h = scorers.ahash(images[i])
        if last and scorers.near_duplicate(h, last[0]):
            answer_from[i] = last[1]
        else:
            answer_from[i] = i
            last = (h, i)
    sent = sorted(set(answer_from.values()))
    answers: dict[int, dict[str, float]] = {}
    if sent:
        limit = asyncio.Semaphore(4)
        async with httpx.AsyncClient() as client:
            async def one(i: int) -> None:
                async with limit:
                    answers[i] = await scorers.openai_scores(client, images[i])
            await asyncio.gather(*(one(i) for i in sent))
    out = []
    for i, c in enumerate(cues):
        scores, checked_by = scorers.combine(c, answers[answer_from[i]] if i in answer_from else None)
        out.append({"index": i, "scores": scores, "checked_by": checked_by})
    return out, {"frames_total": len(images), "frames_escalated": len(sent),
                 "frames_reused": len(escalate) - len(sent)}


async def _job(provider_job_id: str, req: ScoreRequest) -> None:
    started = time.monotonic()
    payload: dict[str, Any] = {"provider_job_id": provider_job_id, "media_id": req.media_id}
    try:
        payload["frames"], counts = await _score_frames(req.frames)
        log("scored", media_id=req.media_id, **counts,
            max_explicit=max(f["scores"]["explicit"] for f in payload["frames"]),
            seconds=round(time.monotonic() - started, 2))
    except Exception as exc:  # fail closed: no frames, an explicit error
        payload["error"] = f"{type(exc).__name__}: {exc}"[:300]
        log("scoring_failed", media_id=req.media_id, error=payload["error"])
    if req.callback_url:
        await deliver(req.callback_url, payload)


async def deliver(url: str, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", "X-Signature": sign(body)}
    async with httpx.AsyncClient(timeout=15) as client:
        for attempt in range(5):
            try:
                r = await client.post(url, content=body, headers=headers)
                if r.status_code < 500:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(2 ** attempt)
    log("callback_failed", media_id=payload.get("media_id"))


@app.get("/health")
async def health() -> dict[str, str]:
    if state["tier1"] is None:
        raise HTTPException(503, "model loading")
    return {"status": "ok"}


@app.get("/info")
async def info() -> dict[str, Any]:
    tier2 = scorers.openai_enabled()
    return {
        "primary": state["tier1"].name if state["tier1"] else None,
        "secondary": scorers.OPENAI_MODEL if tier2 else None,
        "premium": None,  # tier 3 not wired; its cases are held for a human
        "checked_labels": [label for label in scorers.LABELS if label != "real_person"],
        "not_checked_labels": ["real_person"],  # no identity detector: held when a person is present
        "labels": scorers.describe(tier2),
        "hold_rules": scorers.HOLD_RULES,
        "max_frames": MAX_FRAMES,
    }


@app.post("/score")
async def score(req: ScoreRequest) -> JSONResponse:
    state["calls"][req.media_id] += 1
    if not req.frames or len(req.frames) > MAX_FRAMES:
        return JSONResponse({"error": f"1 to {MAX_FRAMES} frames"}, status_code=400)
    provider_job_id = f"rpj_{uuid.uuid4().hex}"
    task = asyncio.create_task(_job(provider_job_id, req))
    state["tasks"].add(task)
    task.add_done_callback(state["tasks"].discard)
    return JSONResponse({"provider_job_id": provider_job_id}, status_code=202)


@app.get("/_stats")
async def stats() -> dict[str, Any]:
    return {"calls": dict(state["calls"]), "mode": "real"}
