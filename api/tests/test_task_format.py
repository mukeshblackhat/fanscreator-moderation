"""The media format exactly as the task page shows it.

Frames are flat objects of label scores and generated media uses
"source": "generation". An earlier transcription of the task used nested
{"scores": {...}} frames and "generated"; that form is still accepted, but a
flat frame must never be read as all zeros (which would publish anything).
"""
import pytest

from conftest import ADMIN_U, ADMIN_V, MEMBER_U, MODE, upload, wait_final

TASK_EXAMPLE = {"type": "video", "source": "generation", "prompt": "two adults on a beach at sunset",
                "frames": [{"explicit": 0.02}, {"explicit": 0.95, "self_harm": 0.1}]}
fixed_only = pytest.mark.skipif(MODE != "fixed", reason="end-to-end decisions use the fixed pipeline")


async def test_task_example_is_accepted(client):
    r = await client.post("/media", headers=ADMIN_V, json=TASK_EXAMPLE)
    assert r.status_code == 202 and r.json()["status"] == "pending"


@fixed_only
async def test_task_example_blocked_unverified_published_verified(api, provider, workers):
    await workers(1)
    unverified = await upload(api, ADMIN_U, TASK_EXAMPLE)
    verified = await upload(api, ADMIN_V, TASK_EXAMPLE)
    assert await wait_final(api, ADMIN_U, unverified) == ("blocked", ["pending", "blocked"])
    assert (await api.get(f"/media/{unverified}", headers=ADMIN_U)).json()["reasons"] == ["explicit_unverified"]
    assert (await wait_final(api, ADMIN_V, verified))[0] == "published"


@fixed_only
@pytest.mark.parametrize(("frame", "expected"), [
    ({"minor": 0.95}, "blocked"),
    ({"real_person": 0.6}, "held"),
    ({"self_harm": 0.4999}, "published"),
    ({}, "published"),
])
async def test_flat_frames_are_scored_not_ignored(api, provider, workers, frame, expected):
    await workers(1)
    media_id = await upload(api, MEMBER_U, {"type": "image", "frames": [frame]})
    assert (await wait_final(api, MEMBER_U, media_id))[0] == expected


async def test_refused_prompt_in_task_format_returns_reasons(client):
    r = await client.post("/media", headers=ADMIN_V, json={**TASK_EXAMPLE, "prompt": "a 16 year old on a beach"})
    assert r.status_code == 422 and r.json() == {"reasons": ["prompt:minor"]}


async def test_nested_scores_and_generated_still_accepted(client):
    r = await client.post("/media", headers=ADMIN_V, json={
        "type": "image", "source": "generated", "prompt": "a lighthouse", "frames": [{"scores": {"explicit": 0.1}}]})
    assert r.status_code == 202


@pytest.mark.parametrize("body", [
    {"type": "image", "frames": [{"minor": 1.5}]},              # score out of range
    {"type": "image", "source": "ai", "frames": [{}]},          # unknown source
    {"type": "image", "frames": ["not an object"]},
    {"type": "image", "frames": [{}, {}]},                      # an image has one frame
    {"type": "video", "source": "generation", "frames": [{}]},  # generation needs its prompt
])
async def test_bad_media_is_422(client, body):
    assert (await client.post("/media", headers=ADMIN_V, json=body)).status_code == 422
