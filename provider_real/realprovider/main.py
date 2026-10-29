"""Real moderation provider (port 8002). Same contract as the fake provider,
but scores the actual pixels of each frame.

POST /score   {"media_id", "frames": [{"image": "<path under MEDIA_STORE>"}], "callback_url"}
              always 202 {"provider_job_id"}; the result is POSTed to callback_url,
              signed with HMAC-SHA256 over the raw body (hex, X-Signature).
              On any scoring failure the callback carries {"error": ...} and no
              frames, which the API treats as a malformed answer: retried, then held.
              Fail closed, never a partial answer.
GET  /info    which models are active and which labels are really checked
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

state: dict[str, Any] = {"primary": None, "calls": Counter(), "tasks": set(), "lock": None, "loaded_at": None}


def log(event: str, **kw: Any) -> None:
    print(json.dumps({"event": event, "ts": round(time.time(), 3), **kw}), flush=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    state["lock"] = asyncio.Lock()
    started = time.monotonic()
    state["primary"] = await asyncio.to_thread(scorers.load_primary)
    state["loaded_at"] = time.time()
    log("model_loaded", primary=state["primary"].name, secondary=scorers.openai_enabled(),
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


async def _score_frames(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    images = []
    for f in frames:
        with Image.open(frame_path(str(f.get("image", "")))) as img:
            images.append(img.convert("RGB"))
    async with state["lock"]:  # one CPU inference at a time
        local = await asyncio.to_thread(state["primary"].explicit, images)
    secondary: list[dict[str, float] | None] = [None] * len(images)
    if scorers.openai_enabled():
        limit = asyncio.Semaphore(4)
        async with httpx.AsyncClient() as client:
            async def one(i: int) -> None:
                async with limit:
                    secondary[i] = await scorers.openai_scores(client, images[i])
            await asyncio.gather(*(one(i) for i in range(len(images))))
    return [{"index": i, "scores": scorers.combine(local[i], secondary[i])} for i in range(len(images))]


async def _job(provider_job_id: str, req: ScoreRequest) -> None:
    started = time.monotonic()
    payload: dict[str, Any] = {"provider_job_id": provider_job_id, "media_id": req.media_id}
    try:
        payload["frames"] = await _score_frames(req.frames)
        log("scored", media_id=req.media_id, frames=len(req.frames),
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
    if state["primary"] is None:
        raise HTTPException(503, "model loading")
    return {"status": "ok"}


@app.get("/info")
async def info() -> dict[str, Any]:
    secondary = scorers.openai_enabled()
    checked = ["explicit"] + (["self_harm", "sexual_violence"] if secondary else [])
    return {
        "primary": state["primary"].name if state["primary"] else None,
        "secondary": scorers.OPENAI_MODEL if secondary else None,
        "checked_labels": checked,
        "not_checked_labels": [label for label in scorers.LABELS if label not in checked],
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
