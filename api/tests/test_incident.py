"""Regression tests for last Tuesday's incident, one per symptom.

These are written first against the deliberately broken v0
(SCREENING_MODE=broken) where they must FAIL, then against the fix where
they must pass. The output of both runs is saved in docs/evidence/.
"""
import asyncio

from conftest import ADMIN_U, ADMIN_V, upload, video, image, wait_final


# Symptom 1: uploads went live without being checked during the outage.
async def test_provider_timeout_never_publishes(api, provider, workers, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "provider_timeout_seconds", 0.3)
    monkeypatch.setattr(settings, "rescreen_interval_seconds", 60)  # keep it held while we look
    await provider.post("/_control", json={"mode": "timeout"})
    await workers(2)
    media_id = await upload(api, ADMIN_U, image({"minor": 0.95}))

    final, seen = await wait_final(api, ADMIN_U, media_id, timeout=40)

    assert "published" not in seen, f"published without a decision during an outage (saw {seen})"
    assert final == "held", f"expected held, got {final}"
    item = next(i for i in (await api.get("/review", headers=ADMIN_U)).json() if i["id"] == media_id)
    assert item["held_reason_kind"] == "screening_unavailable"


# Symptom 2: explicit content in a video's last minute went live in an unverified workspace.
async def test_explicit_only_on_frame_55_of_60_is_blocked_when_unverified(api, provider, workers):
    await workers(2)
    media_id = await upload(api, ADMIN_U, video(60, {55: {"explicit": 0.95}}))
    final, seen = await wait_final(api, ADMIN_U, media_id)
    assert final == "blocked", f"late explicit frame was missed: final {final}, saw {seen}"
    reasons = (await api.get(f"/media/{media_id}", headers=ADMIN_U)).json()["reasons"]
    assert reasons == ["explicit_unverified"]


async def test_minor_only_on_last_frame_is_blocked_even_when_verified(api, provider, workers):
    await workers(2)
    media_id = await upload(api, ADMIN_V, video(60, {59: {"minor": 0.9}}))
    final, seen = await wait_final(api, ADMIN_V, media_id)
    assert final == "blocked", f"prohibited last frame was missed: final {final}, saw {seen}"
    reasons = (await api.get(f"/media/{media_id}", headers=ADMIN_V)).json()["reasons"]
    assert "prohibited:minor@frame=59" in reasons


# Symptom 3: a prompt asking for a 16-year-old reached the generator.
async def test_refused_prompt_never_reaches_generator_or_provider(client, provider):
    from app import generator

    before = generator.total_calls()
    r = await client.post("/media", headers=ADMIN_V, json={
        "type": "image", "source": "generated",
        "prompt": "a 16 year old at the beach", "frames": [{"scores": {}}],
    })
    assert r.status_code == 422, f"prompt was accepted with {r.status_code}"
    assert any(reason.startswith("prompt:") for reason in r.json()["reasons"])
    assert generator.total_calls() == before, "the generator was called for a refused prompt"
    await asyncio.sleep(0.5)
    assert (await provider.get("/_stats")).json()["calls"] == {}, "the provider was called"


# Symptom 4: some uploads were scored two or three times.
async def test_redelivered_job_calls_provider_once(api, provider, workers):
    # The provider takes 3 s, longer than v0's 2 s visibility timeout, so a
    # second worker picks up the same job while the first is still running.
    await provider.post("/_control", json={"mode": "slow", "delay_ms": 3000})
    await workers(2)
    media_id = await upload(api, ADMIN_V, image())
    final, _ = await wait_final(api, ADMIN_V, media_id)
    await asyncio.sleep(3.5)  # let any duplicate attempt finish
    calls = (await provider.get("/_stats")).json()["calls"].get(media_id, 0)
    assert final == "published"
    assert calls == 1, f"provider was called {calls} times for one upload"


async def test_duplicate_callback_is_a_no_op(api, provider, db, workers):
    from sqlalchemy import text

    await provider.post("/_control", json={"mode": "ok", "duplicate_callbacks": True})
    await workers(2)
    media_id = await upload(api, ADMIN_V, video(40))  # > 30 frames: async callback
    final, _ = await wait_final(api, ADMIN_V, media_id)
    await asyncio.sleep(1.0)  # the duplicate arrives after the first
    scores_rows = (await db.execute(text("SELECT count(*) FROM scores WHERE media_id = :m"),
                                    {"m": media_id})).scalar()
    calls = (await provider.get("/_stats")).json()["calls"].get(media_id, 0)
    assert final == "published"
    assert scores_rows == 1, f"expected exactly one stored score for the video, found {scores_rows}"
    assert calls == 1, f"provider was called {calls} times"
