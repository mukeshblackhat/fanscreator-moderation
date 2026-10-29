"""HTTP client for the moderation provider, with every outcome classified.

A timeout and a malformed answer mean different things, so they come back as
different outcome kinds. What to do about each one is the caller's job.
"""
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.config import settings

ALL_LABELS = ("minor", "sexual_violence", "bestiality", "self_harm", "real_person", "explicit")

OutcomeKind = Literal["ok", "accepted", "timeout", "rate_limited", "server_error", "malformed"]


@dataclass
class ProviderOutcome:
    kind: OutcomeKind
    http_status: int | None = None
    frames: list[dict[str, float]] | None = None  # one score mapping per frame, in order
    provider_job_id: str | None = None
    retry_after: float | None = None
    error: str | None = None


class MalformedResponse(ValueError):
    pass


def validate_frames(body: Any, expected_frames: int) -> list[dict[str, float]]:
    """Return per-frame scores only if the answer covers frames 0..n-1 exactly,
    with every label present and every score inside [0, 1]."""
    if not isinstance(body, dict) or not isinstance(body.get("frames"), list):
        raise MalformedResponse("response has no frames list")
    frames = body["frames"]
    if len(frames) != expected_frames:
        raise MalformedResponse(f"expected {expected_frames} frames, got {len(frames)}")
    out: list[dict[str, float]] = [{} for _ in range(expected_frames)]
    seen: set[int] = set()
    for frame in frames:
        if not isinstance(frame, dict) or not isinstance(frame.get("scores"), dict):
            raise MalformedResponse("frame without a scores object")
        index = frame.get("index")
        if not isinstance(index, int) or not 0 <= index < expected_frames or index in seen:
            raise MalformedResponse(f"bad or duplicate frame index {index!r}")
        seen.add(index)
        scores = frame["scores"]
        missing = [label for label in ALL_LABELS if label not in scores]
        if missing:
            raise MalformedResponse(f"frame {index} missing labels {missing}")
        for label in ALL_LABELS:
            value = scores[label]
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0.0 <= value <= 1.0:
                raise MalformedResponse(f"frame {index} label {label} score {value!r} outside 0..1")
        out[index] = {label: float(scores[label]) for label in ALL_LABELS}
    return out


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None


async def score(
    media_id: str,
    frames: list[dict[str, Any]],
    *,
    callback_url: str | None = None,
    timeout: float | None = None,
    validate: bool = True,
) -> ProviderOutcome:
    payload = {"media_id": media_id, "frames": frames, "callback_url": callback_url}
    seconds = timeout if timeout is not None else settings.provider_timeout_seconds
    try:
        async with httpx.AsyncClient(base_url=settings.provider_url, timeout=httpx.Timeout(seconds)) as client:
            response = await client.post("/score", json=payload)
    except httpx.TimeoutException as exc:
        return ProviderOutcome("timeout", error=type(exc).__name__)
    except httpx.TransportError as exc:
        # Connection refused or reset: the provider is unreachable, treat like a 5xx.
        return ProviderOutcome("server_error", error=type(exc).__name__)

    status = response.status_code
    if status == 429:
        return ProviderOutcome("rate_limited", status, retry_after=_retry_after(response))
    if status >= 500:
        return ProviderOutcome("server_error", status, error=response.text[:200])
    if status == 202:
        try:
            job_id = response.json()["provider_job_id"]
        except (ValueError, KeyError, TypeError):
            return ProviderOutcome("malformed", status, error="202 without provider_job_id")
        return ProviderOutcome("accepted", status, provider_job_id=str(job_id))
    if status != 200:
        return ProviderOutcome("malformed", status, error=f"unexpected status {status}")
    try:
        body = response.json()
    except ValueError:
        return ProviderOutcome("malformed", status, error="body is not JSON")
    if not validate:
        raw = [f.get("scores", {}) for f in body.get("frames", []) if isinstance(f, dict)]
        return ProviderOutcome("ok", status, frames=raw)
    try:
        return ProviderOutcome("ok", status, frames=validate_frames(body, len(frames)))
    except MalformedResponse as exc:
        return ProviderOutcome("malformed", status, error=str(exc))
