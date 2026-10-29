import hashlib
import hmac

import httpx
import pytest

from provider.main import LABELS, SECRET, app, state


@pytest.fixture
async def client():
    state.calls.clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://p") as c:
        await c.post("/_reset")
        yield c


async def test_sync_score_fills_every_label(client):
    r = await client.post("/score", json={"media_id": "m1", "frames": [{"scores": {"explicit": 0.9}}]})
    assert r.status_code == 200
    frame = r.json()["frames"][0]
    assert frame["index"] == 0
    assert set(frame["scores"]) == set(LABELS)
    assert frame["scores"]["explicit"] == 0.9 and frame["scores"]["minor"] == 0.0


async def test_long_video_is_async(client):
    frames = [{"scores": {}} for _ in range(31)]
    r = await client.post("/score", json={"media_id": "m2", "frames": frames})
    assert r.status_code == 202 and r.json()["provider_job_id"].startswith("pj_")


async def test_counts_failed_calls_too(client):
    await client.post("/_control", json={"mode": "error"})
    r = await client.post("/score", json={"media_id": "m3", "frames": [{"scores": {}}]})
    assert r.status_code == 500
    await client.post("/_control", json={"mode": "rate_limit"})
    r = await client.post("/score", json={"media_id": "m3", "frames": [{"scores": {}}]})
    assert r.status_code == 429 and r.headers["retry-after"] == "2"
    assert (await client.get("/_stats")).json()["calls"] == {"m3": 2}


async def test_malformed_drops_label_and_frame(client):
    await client.post("/_control", json={"mode": "malformed"})
    r = await client.post("/score", json={"media_id": "m4", "frames": [{"scores": {}}, {"scores": {}}]})
    frames = r.json()["frames"]
    assert len(frames) == 1 and "minor" not in frames[0]["scores"]


def test_signature_is_hmac_sha256_hex():
    from provider.main import sign

    assert sign(b"{}") == hmac.new(SECRET, b"{}", hashlib.sha256).hexdigest()
