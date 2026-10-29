"""Prompt engine through the API: refuse, review (output held), allow, audit, check endpoint."""
import hashlib

import pytest
from sqlalchemy import text

from conftest import ADMIN_U, ADMIN_V, MEMBER_U, MEMBER_V, MODE, upload, wait_final

pytestmark = pytest.mark.skipif(MODE != "fixed", reason="the prompt engine is part of the fixed pipeline")


def gen(prompt: str, frame: dict | None = None, negative: str | None = None) -> dict:
    body = {"type": "image", "source": "generation", "prompt": prompt, "frames": [frame or {}]}
    if negative is not None:
        body["negative_prompt"] = negative
    return body


async def test_review_band_prompt_holds_clean_output_for_a_human(api, provider, workers):
    await workers(1)
    media_id = await upload(api, MEMBER_V, gen("school uniform photoshoot"))  # output scores clean
    assert (await wait_final(api, MEMBER_V, media_id))[0] == "held"
    assert (await api.get(f"/media/{media_id}", headers=MEMBER_V)).json()["reasons"] == ["review:prompt_minor"]
    listed = (await api.get("/review", headers=ADMIN_V)).json()
    assert [i["held_reason_kind"] for i in listed if i["id"] == media_id] == ["score"]
    r = await api.post(f"/review/{media_id}/approve", headers=ADMIN_V)
    assert r.status_code == 200 and r.json()["status"] == "published"


async def test_review_band_does_not_soften_a_blocking_output(api, provider, workers):
    await workers(1)
    media_id = await upload(api, MEMBER_V, gen("a young woman, nude", {"minor": 0.9}))
    assert (await wait_final(api, MEMBER_V, media_id))[0] == "blocked"


async def test_allowed_prompt_with_clean_output_is_published(api, provider, workers):
    await workers(1)
    media_id = await upload(api, MEMBER_V, gen("a lighthouse in a storm"))
    assert (await wait_final(api, MEMBER_V, media_id))[0] == "published"


async def test_negative_prompt_excluding_adults_is_refused(client):
    r = await client.post("/media", headers=MEMBER_V, json=gen("nude portrait", negative="adult, mature"))
    assert r.status_code == 422 and r.json() == {"reasons": ["prompt:minor"]}


async def test_every_decision_is_audited_without_the_prompt_text(client, db):
    from app import generator

    await db.execute(text("DELETE FROM prompt_events"))
    await db.commit()
    before = generator.total_calls()
    refused = await client.post("/media", headers=MEMBER_U, json=gen("a 15 year old at the beach"))
    allowed = await client.post("/media", headers=MEMBER_U, json=gen("a cabin at dawn"))
    assert refused.status_code == 422 and allowed.status_code == 202
    assert generator.total_calls() == before + 1  # only the allowed one reached the generator
    rows = (await db.execute(text(
        "SELECT decision, kind, prompt_sha256, prompt_chars, media_id IS NOT NULL AS has_media "
        "FROM prompt_events ORDER BY id"))).mappings().all()
    await db.commit()
    assert [(r["decision"], r["kind"], r["has_media"]) for r in rows] == [
        ("refuse", "generation", False), ("allow", "generation", True)]
    assert rows[0]["prompt_sha256"] == hashlib.sha256(b"a 15 year old at the beach").hexdigest()
    columns = (await db.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'prompt_events'"))).scalars().all()
    await db.commit()
    assert "prompt" not in columns and "prompt_text" not in columns


async def test_prompt_scores_are_stored_with_generated_media(client, db):
    media_id = (await client.post("/media", headers=MEMBER_V, json=gen("celebrity lookalike portrait"))).json()["id"]
    row = (await db.execute(text("SELECT prompt_scores, prompt_flags FROM media WHERE id = :m"),
                            {"m": media_id})).mappings().one()
    await db.commit()
    assert row["prompt_scores"]["real_person"] == 0.55
    assert row["prompt_flags"] == ["review:prompt_real_person"]


async def test_check_endpoint(client):
    r = await client.post("/prompts/check", headers=MEMBER_U, json={"prompt": "barely legal girl in lingerie"})
    body = r.json()
    assert r.status_code == 200 and body["decision"] == "refuse" and body["reasons"] == ["prompt:minor"]
    assert body["scores"]["minor"] == 0.85 and body["thresholds"] == {"refuse": 0.8, "review": 0.5}
    assert body["hits"]["minor"]
    assert (await client.post("/prompts/check", headers=MEMBER_U, json={"prompt": "a cabin"})).json()["decision"] == "allow"


async def test_check_endpoint_needs_a_member(client):
    stranger = {"X-User-Id": "nobody", "X-Workspace-Id": "ws_verified"}
    assert (await client.post("/prompts/check", headers=stranger, json={"prompt": "x"})).status_code == 404
    assert (await client.post("/prompts/check", json={"prompt": "x"})).status_code == 401
    assert (await client.post("/prompts/check", headers=MEMBER_U, json={"prompt": ""})).status_code == 422


async def test_events_are_per_workspace(client):
    await client.post("/prompts/check", headers=ADMIN_U, json={"prompt": "a 12 year old"})
    events = (await client.get("/prompts/events", headers=ADMIN_V)).json()["events"]
    mine = (await client.get("/prompts/events", headers=ADMIN_U)).json()["events"]
    assert all(e["user_id"] in ("admin_v", "member_v") for e in events)
    assert mine and mine[0]["decision"] == "refuse" and len(mine[0]["prompt_sha256"]) == 12
