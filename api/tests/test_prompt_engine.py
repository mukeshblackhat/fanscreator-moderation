"""Prompt checker through the API: refuse (422, zero provider calls), allow (output
screened like any upload), audit, the check endpoint, and who sees what."""
import hashlib

import pytest
from sqlalchemy import text

from conftest import ADMIN_U, ADMIN_V, MEMBER_U, MEMBER_V, MODE, upload, wait_final

pytestmark = pytest.mark.skipif(MODE != "fixed", reason="the prompt checker is part of the fixed pipeline")


def gen(prompt: str, frame: dict | None = None, negative: str | None = None) -> dict:
    body = {"type": "image", "source": "generation", "prompt": prompt, "frames": [frame or {}]}
    if negative is not None:
        body["negative_prompt"] = negative
    return body


@pytest.mark.parametrize("prompt", [
    "a 16 year old at the beach", "school uniform photoshoot", "a young woman, nude",
    "celebrity lookalike portrait", "my stepsister naked", "a tееn posing",
])
async def test_refused_prompts_never_reach_generator_or_provider(api, provider, prompt):
    r = await api.post("/media", headers=MEMBER_V, json=gen(prompt))
    assert r.status_code == 422 and list(r.json()) == ["reasons"]
    assert all(reason.startswith("prompt:") for reason in r.json()["reasons"])
    assert (await provider.get("/_stats")).json()["calls"] == {}


async def test_allowed_prompt_output_is_screened_like_an_upload(api, provider, workers):
    await workers(1)
    clean = await upload(api, MEMBER_V, gen("a lighthouse in a storm"))
    bad_output = await upload(api, MEMBER_V, gen("a lighthouse in a storm", {"minor": 0.9}))
    assert (await wait_final(api, MEMBER_V, clean))[0] == "published"
    assert (await wait_final(api, MEMBER_V, bad_output))[0] == "blocked"


async def test_adult_explicit_prompt_is_decided_by_the_output_and_workspace(api, provider, workers):
    await workers(1)
    verified = await upload(api, MEMBER_V, gen("two adults, explicit", {"explicit": 0.95}))
    unverified = await upload(api, MEMBER_U, gen("two adults, explicit", {"explicit": 0.95}))
    assert (await wait_final(api, MEMBER_V, verified))[0] == "published"
    assert (await wait_final(api, MEMBER_U, unverified))[0] == "blocked"


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
        "SELECT decision, kind, prompt_sha256, media_id IS NOT NULL AS has_media FROM prompt_events ORDER BY id"
    ))).mappings().all()
    await db.commit()
    assert [(r["decision"], r["kind"], r["has_media"]) for r in rows] == [
        ("refuse", "generation", False), ("allow", "generation", True)]
    assert rows[0]["prompt_sha256"] == hashlib.sha256(b"a 15 year old at the beach").hexdigest()
    columns = (await db.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'prompt_events'"))).scalars().all()
    await db.commit()
    assert "prompt" not in columns and "prompt_text" not in columns


async def test_media_records_its_creator_and_prompt_fields(client, db):
    media_id = (await client.post("/media", headers=MEMBER_V, json=gen("a lighthouse"))).json()["id"]
    row = (await db.execute(text("SELECT created_by, prompt_scores FROM media WHERE id = :m"),
                            {"m": media_id})).mappings().one()
    await db.commit()
    assert row["created_by"] == "member_v"
    assert row["prompt_scores"]["fields"]["under_18_or_looks_young"] is False


async def test_check_endpoint_returns_the_interface(client):
    body = (await client.post("/prompts/check", headers=ADMIN_U, json={"prompt": "barely legal girl in lingerie"})).json()
    assert body["decision"] == "refuse" and body["reasons"] == ["prompt:minor"]
    assert body["fields"]["under_18_or_looks_young"] is True and body["fields"]["sexual_content"] is True
    assert body["field_threshold"] == 0.5 and body["hits"]["minor"]
    allowed = (await client.post("/prompts/check", headers=ADMIN_U, json={"prompt": "a cabin"})).json()
    assert allowed["decision"] == "allow" and not any(allowed["fields"].values())


async def test_members_never_see_which_word_triggered_it(client):
    body = (await client.post("/prompts/check", headers=MEMBER_U, json={"prompt": "barely legal girl"})).json()
    assert body["decision"] == "refuse" and "hits" not in body


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
