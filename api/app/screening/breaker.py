"""Circuit breaker in Redis, shared by every worker.

closed     calls go through; timeouts and 5xx are recorded in a rolling window
open       5 failures in 30 s -> no calls at all for 10 s
half-open  after the open period, exactly one caller gets to probe;
           success closes the breaker, failure opens it again

Redis is not the source of truth for anything that matters. If Redis is
unreachable the breaker allows calls: safety comes from the pipeline (every
outcome is validated, holds are written to Postgres), the breaker only saves
money and load during an outage.
"""
import time
import uuid

import redis.asyncio as redis

from app.config import settings
from app.logging import log

FAILURES = "breaker:failures"
OPEN_UNTIL = "breaker:open_until"   # exists while fully open (TTL = open period)
TRIPPED = "breaker:tripped"         # exists from first trip until a probe succeeds
PROBE = "breaker:probe"             # one probe at a time while half-open
COOLDOWN = "provider:cooldown"      # set from 429 Retry-After, global slow down
LAST_SUCCESS = "provider:last_success"  # unix time of the last good provider answer
RESCREEN_PROBE = "rescreen:probe"   # at most one blind re-screen probe per window

_client: redis.Redis | None = None


def client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def reset() -> None:
    await client().delete(FAILURES, OPEN_UNTIL, TRIPPED, PROBE, COOLDOWN, LAST_SUCCESS, RESCREEN_PROBE)


async def state() -> str:
    try:
        r = client()
        if await r.exists(OPEN_UNTIL):
            return "open"
        if await r.exists(TRIPPED):
            return "half_open"
        return "closed"
    except redis.RedisError:
        return "unknown"


async def allow() -> bool:
    """May this caller send a request to the provider right now?"""
    try:
        r = client()
        if await r.exists(COOLDOWN):
            return False
        if await r.exists(OPEN_UNTIL):
            return False
        if await r.exists(TRIPPED):
            # Half-open: only one probe per open period.
            ttl = max(1, int(settings.breaker_open_seconds))
            got = await r.set(PROBE, str(uuid.uuid4()), nx=True, ex=ttl)
            if got:
                log.info("breaker_probe")
            return bool(got)
        return True
    except redis.RedisError as exc:
        log.warning("breaker_redis_unavailable", error=str(exc))
        return True


async def record_success() -> None:
    try:
        r = client()
        await r.set(LAST_SUCCESS, str(time.time()))
        if await r.exists(TRIPPED):
            await r.delete(TRIPPED, OPEN_UNTIL, PROBE, FAILURES)
            log.info("breaker_closed")
    except redis.RedisError:
        pass


async def record_failure() -> None:
    try:
        r = client()
        now = time.time()
        await r.zadd(FAILURES, {f"{now}:{uuid.uuid4().hex[:6]}": now})
        await r.zremrangebyscore(FAILURES, 0, now - settings.breaker_window_seconds)
        await r.expire(FAILURES, int(settings.breaker_window_seconds) + 1)
        recent = await r.zcard(FAILURES)
        half_open = await r.exists(TRIPPED) and not await r.exists(OPEN_UNTIL)
        if recent >= settings.breaker_failure_threshold or half_open:
            await r.set(OPEN_UNTIL, "1", px=int(settings.breaker_open_seconds * 1000))
            await r.set(TRIPPED, str(now), nx=True)
            await r.delete(PROBE)
            log.warning("breaker_opened", recent_failures=recent, reopened=bool(half_open))
    except redis.RedisError:
        pass


async def tripped_since() -> float | None:
    """When the breaker first opened in this outage, or None if closed."""
    try:
        raw = await client().get(TRIPPED)
        return float(raw) if raw else None
    except redis.RedisError:
        return None


async def set_cooldown(seconds: float) -> None:
    """429: the provider asked everyone to slow down, not just this item."""
    try:
        await client().set(COOLDOWN, "1", px=max(1, int(seconds * 1000)))
    except redis.RedisError:
        pass


async def last_success() -> float | None:
    try:
        raw = await client().get(LAST_SUCCESS)
        return float(raw) if raw else None
    except redis.RedisError:
        return None


async def claim_rescreen_probe(window_seconds: float) -> bool:
    """With no sign the provider is back, release at most one held item per window."""
    try:
        return bool(await client().set(RESCREEN_PROBE, "1", nx=True, px=int(window_seconds * 1000)))
    except redis.RedisError:
        return True
