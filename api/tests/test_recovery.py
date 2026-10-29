"""Each provider failure follows its own path and limit, and the system
recovers by itself. Assertions read our provider_calls ledger and compare it
with the provider's own count, because every call costs money."""
import asyncio

import pytest
from sqlalchemy import text

from conftest import ADMIN_U, ADMIN_V, MODE, image, upload, video, wait_final

pytestmark = pytest.mark.skipif(MODE != "fixed", reason="recovery paths exist only in the fixed pipeline")


async def ledger(db, media_id: str) -> dict[str, int]:
    rows = (await db.execute(text(
        "SELECT outcome, count(*) FROM provider_calls WHERE media_id = :m GROUP BY outcome"
    ), {"m": media_id})).all()
    await db.commit()
    return {o: n for o, n in rows}


async def provider_calls(provider, media_id: str) -> int:
    return (await provider.get("/_stats")).json()["calls"].get(media_id, 0)


async def held_kind(api, who, media_id) -> str | None:
    items = (await api.get("/review", headers=who)).json()
    return next((i["held_reason_kind"] for i in items if i["id"] == media_id), None)


@pytest.fixture
def quick(monkeypatch):
    """Short timeouts and no automatic re-screen unless a test turns it on."""
    from app.config import settings

    monkeypatch.setattr(settings, "provider_timeout_seconds", 0.3)
    monkeypatch.setattr(settings, "rescreen_interval_seconds", 60)
    monkeypatch.setattr(settings, "hold_after_seconds", 2)
    return settings


async def test_timeout_retries_three_times_then_holds(api, provider, db, workers, quick):
    await provider.post("/_control", json={"mode": "timeout"})
    await workers(1)
    media_id = await upload(api, ADMIN_U, image())
    final, seen = await wait_final(api, ADMIN_U, media_id)
    assert final == "held" and "published" not in seen
    assert await held_kind(api, ADMIN_U, media_id) == "screening_unavailable"
    assert await ledger(db, media_id) == {"timeout": 3}
    assert await provider_calls(provider, media_id) == 3  # the provider saw (and may bill) all three


async def test_5xx_retries_three_times_then_holds(api, provider, db, workers, quick):
    await provider.post("/_control", json={"mode": "error"})
    await workers(1)
    media_id = await upload(api, ADMIN_U, image())
    assert (await wait_final(api, ADMIN_U, media_id))[0] == "held"
    assert await ledger(db, media_id) == {"server_error": 3}
    assert await provider_calls(provider, media_id) == 3


async def test_429_waits_for_retry_after_without_holding(api, provider, db, workers, quick):
    await provider.post("/_control", json={"mode": "rate_limit"})
    await workers(1)
    media_id = await upload(api, ADMIN_V, image())
    await asyncio.sleep(1.0)
    assert (await api.get(f"/media/{media_id}", headers=ADMIN_V)).json()["status"] == "pending"
    assert (await ledger(db, media_id)) == {"rate_limited": 1}, "429 must wait for Retry-After (2 s)"
    await provider.post("/_control", json={"mode": "ok"})
    final, _ = await wait_final(api, ADMIN_V, media_id, timeout=15)
    assert final == "published"
    calls = await ledger(db, media_id)
    assert calls["ok"] == 1 and set(calls) <= {"rate_limited", "ok"}
    assert await provider_calls(provider, media_id) == sum(calls.values())


async def test_malformed_answer_is_never_trusted(api, provider, db, workers, quick):
    await provider.post("/_control", json={"mode": "malformed"})
    await workers(1)
    media_id = await upload(api, ADMIN_V, video(3, {2: {"minor": 0.9}}))  # the dropped frame hides the minor
    final, seen = await wait_final(api, ADMIN_V, media_id)
    assert final == "held" and "published" not in seen
    assert await held_kind(api, ADMIN_V, media_id) == "provider_malformed"
    assert await ledger(db, media_id) == {"malformed": 2}
    r = await api.post(f"/review/{media_id}/approve", headers=ADMIN_V)
    assert r.status_code == 409


async def test_breaker_stops_calls_during_an_outage(api, provider, db, workers, quick):
    await provider.post("/_control", json={"mode": "error"})
    await workers(1)
    ids = [await upload(api, ADMIN_U, image()) for _ in range(8)]
    finals = [(await wait_final(api, ADMIN_U, m))[0] for m in ids]
    assert finals == ["held"] * 8
    total = sum((await provider.get("/_stats")).json()["calls"].values())
    assert total < 8 * 3, f"breaker should have stopped calls, provider saw {total}"
    kinds = {await held_kind(api, ADMIN_U, m) for m in ids}
    assert kinds == {"screening_unavailable"}


async def test_outage_holds_then_recovers_without_a_human(api, provider, db, workers, quick, monkeypatch):
    await provider.post("/_control", json={"mode": "timeout"})
    await workers(2)
    clean = await upload(api, ADMIN_U, image())
    review = await upload(api, ADMIN_U, image({"real_person": 0.6}))
    blocked = await upload(api, ADMIN_U, image({"minor": 0.9}))
    explicit = await upload(api, ADMIN_U, image({"explicit": 0.9}))
    for m in (clean, review, blocked, explicit):
        assert (await wait_final(api, ADMIN_U, m))[0] == "held"
        assert await held_kind(api, ADMIN_U, m) == "screening_unavailable"

    # Approving an item nobody screened would publish it unchecked.
    r = await api.post(f"/review/{clean}/approve", headers=ADMIN_U)
    assert r.status_code == 409 and "unchecked" in r.json()["detail"]

    # Provider comes back. The re-screen loop probes, sees success, and releases the rest.
    await provider.post("/_control", json={"mode": "ok"})
    monkeypatch.setattr(quick, "rescreen_interval_seconds", 0.3)
    monkeypatch.setattr(quick, "rescreen_probe_window_seconds", 0.5)
    from app.screening import rescreen

    task = asyncio.create_task(rescreen.loop())
    try:
        expected = {clean: "published", review: "held", blocked: "blocked", explicit: "blocked"}
        for media_id, want in expected.items():
            for _ in range(150):
                body = (await api.get(f"/media/{media_id}", headers=ADMIN_U)).json()
                kind = await held_kind(api, ADMIN_U, media_id)
                if body["status"] == want and (want != "held" or kind == "score"):
                    break
                await asyncio.sleep(0.1)
            assert body["status"] == want, f"{media_id}: {body}"
        assert await held_kind(api, ADMIN_U, review) == "score"
    finally:
        task.cancel()


async def test_missing_callback_is_resubmitted_and_scored_once(api, provider, db, workers, quick, monkeypatch):
    """Trade-off made visible: the watchdog pays for a second call rather than
    leave a video pending forever. Scores are still stored exactly once."""
    monkeypatch.setattr(quick, "callback_timeout_seconds", 2)
    monkeypatch.setattr(quick, "rescreen_interval_seconds", 0.3)
    await provider.post("/_control", json={"mode": "ok", "callback_delay_ms": 3000})
    await workers(1)
    media_id = await upload(api, ADMIN_V, video(40, {39: {"explicit": 0.9}}))
    final, _ = await wait_final(api, ADMIN_V, media_id, timeout=15)
    await asyncio.sleep(3.0)  # let the second callback land too
    assert final == "published"
    rows = (await db.execute(text("SELECT count(*) FROM scores WHERE media_id = :m"), {"m": media_id})).scalar()
    receipts = (await db.execute(text("SELECT count(*) FROM callback_receipts WHERE media_id = :m"),
                                 {"m": media_id})).scalar()
    await db.commit()
    assert rows == 1
    assert receipts == 2
    assert await provider_calls(provider, media_id) == 2


async def test_malformed_async_callback_is_retried_once_then_held(api, provider, db, workers, quick):
    """Bad answers that arrive by callback count against the same budget as sync ones.
    Before this fix a provider sending bad callbacks was called forever."""
    await provider.post("/_control", json={"mode": "malformed", "callback_delay_ms": 50})
    await workers(1)
    media_id = await upload(api, ADMIN_V, video(40))  # > 30 frames: async callback
    final, seen = await wait_final(api, ADMIN_V, media_id, timeout=20)
    assert final == "held" and "published" not in seen
    assert await held_kind(api, ADMIN_V, media_id) == "provider_malformed"
    await asyncio.sleep(1.0)
    assert await ledger(db, media_id) == {"malformed": 2}
    assert await provider_calls(provider, media_id) == 2
