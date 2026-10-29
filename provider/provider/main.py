"""Fake moderation provider (port 8001).

POST /score      {"media_id", "frames": [{"scores": {...}}], "callback_url"}
                 <= 30 frames: 200 with every label for every frame
                 >  30 frames: 202 {"provider_job_id"}, result POSTed later to
                 callback_url, signed with HMAC-SHA256 (hex) in X-Signature
POST /_control   {"mode": "ok"|"slow"|"timeout"|"error", "delay_ms": 0}
                 extras for our own tests: "rate_limit", "malformed",
                 "duplicate_callbacks": true, "callback_delay_ms": int
GET  /_stats     {"calls": {"<media_id>": n}}  every call received, including failures
POST /_reset     clear stats and go back to mode "ok"
"""
import asyncio
import hashlib
import hmac
import json
import os
import uuid
from collections import Counter
from typing import Any, Literal

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

LABELS = ("minor", "sexual_violence", "bestiality", "self_harm", "real_person", "explicit")
SYNC_FRAME_LIMIT = 30
TIMEOUT_HANG_SECONDS = 30.0
SECRET = os.environ.get("PROVIDER_WEBHOOK_SECRET", "change-me-in-real-life").encode()

Mode = Literal["ok", "slow", "timeout", "error", "rate_limit", "malformed"]


class Control(BaseModel):
    mode: Mode = "ok"
    delay_ms: int = 0
    duplicate_callbacks: bool = False
    callback_delay_ms: int = 200


class ScoreRequest(BaseModel):
    media_id: str
    frames: list[dict[str, Any]]
    callback_url: str | None = None


class State:
    def __init__(self) -> None:
        self.control = Control()
        self.calls: Counter[str] = Counter()
        self.background: set[asyncio.Task[None]] = set()


state = State()
app = FastAPI(title="Fake moderation provider")
# Lets the review page's "Try it" panel switch modes. This is a local test fake, not a real provider.
app.add_middleware(CORSMiddleware, allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(","),
                   allow_methods=["*"], allow_headers=["*"])


def full_scores(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Echo each frame's scores back, with every label present (missing = 0)."""
    out = []
    for i, frame in enumerate(frames):
        # Flat {"explicit": 0.9} (the task's format) or nested {"scores": {...}}.
        if isinstance(frame.get("scores"), dict):
            given = frame["scores"]
        else:
            given = {k: v for k, v in frame.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
        out.append({"index": i, "scores": {label: float(given.get(label, 0.0)) for label in LABELS}})
    return out


def corrupt(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Malformed mode: drop a label from the first frame and drop the last frame."""
    bad = [dict(f, scores=dict(f["scores"])) for f in frames]
    bad[0]["scores"].pop("minor", None)
    if len(bad) > 1:
        bad.pop()
    return bad


def sign(body: bytes) -> str:
    return hmac.new(SECRET, body, hashlib.sha256).hexdigest()


async def deliver_callback(url: str, payload: dict[str, Any], control: Control) -> None:
    await asyncio.sleep(control.callback_delay_ms / 1000)
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", "X-Signature": sign(body)}
    times = 2 if control.duplicate_callbacks else 1
    async with httpx.AsyncClient(timeout=10) as client:
        for _ in range(times):
            try:
                await client.post(url, content=body, headers=headers)
            except httpx.HTTPError:
                pass  # a real provider would retry; we keep the fake simple


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/score")
async def score(req: ScoreRequest) -> JSONResponse:
    state.calls[req.media_id] += 1
    control = state.control

    if control.mode == "slow" and control.delay_ms:
        await asyncio.sleep(control.delay_ms / 1000)
    if control.mode == "timeout":
        await asyncio.sleep(max(TIMEOUT_HANG_SECONDS, control.delay_ms / 1000))
    if control.mode == "error":
        return JSONResponse({"error": "internal provider error"}, status_code=500)
    if control.mode == "rate_limit":
        return JSONResponse({"error": "rate limited"}, status_code=429, headers={"Retry-After": "2"})

    frames = full_scores(req.frames)
    if control.mode == "malformed":
        frames = corrupt(frames)

    if len(req.frames) <= SYNC_FRAME_LIMIT:
        return JSONResponse({"media_id": req.media_id, "frames": frames})

    provider_job_id = f"pj_{uuid.uuid4().hex}"
    if req.callback_url:
        payload = {"provider_job_id": provider_job_id, "media_id": req.media_id, "frames": frames}
        task = asyncio.create_task(deliver_callback(req.callback_url, payload, control.model_copy()))
        state.background.add(task)
        task.add_done_callback(state.background.discard)
    return JSONResponse({"provider_job_id": provider_job_id}, status_code=202)


@app.post("/_control")
async def set_control(control: Control) -> dict[str, Any]:
    state.control = control
    return control.model_dump()


@app.get("/_stats")
async def stats() -> dict[str, Any]:
    return {"calls": dict(state.calls), "mode": state.control.mode}


@app.post("/_reset")
async def reset(_: Request) -> dict[str, str]:
    state.calls.clear()
    state.control = Control()
    return {"status": "reset"}
