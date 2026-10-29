"""Workspace isolation, roles, and callback signatures."""
import hashlib
import hmac
import json

from conftest import ADMIN_U, ADMIN_V, MEMBER_U, MEMBER_V, image, upload, wait_final


async def test_missing_headers_is_401(client):
    assert (await client.get("/review")).status_code == 401
    assert (await client.post("/media", json=image())).status_code == 401


async def test_unknown_user_gets_404_on_media(client):
    r = await client.post("/media", headers={"X-User-Id": "nobody", "X-Workspace-Id": "ws_verified"},
                          json=image())
    assert r.status_code == 404


async def test_member_cannot_list_review(client):
    assert (await client.get("/review", headers=MEMBER_V)).status_code == 403


async def test_media_input_shape(client):
    assert (await client.post("/media", headers=MEMBER_V, json={"kind": "image", "frames": [{}]})).status_code == 202
    two_frame_image = {"type": "image", "frames": [{}, {}]}
    assert (await client.post("/media", headers=MEMBER_V, json=two_frame_image)).status_code == 422
    no_prompt = {"type": "image", "source": "generated", "frames": [{}]}
    assert (await client.post("/media", headers=MEMBER_V, json=no_prompt)).status_code == 422


async def test_hold_is_visible_to_own_admin_only_and_reviewable(api, provider, workers):
    await workers(1)
    media_id = await upload(api, MEMBER_U, image({"real_person": 0.6}))
    final, _ = await wait_final(api, MEMBER_U, media_id)
    assert final == "held"
    assert (await api.get(f"/media/{media_id}", headers=MEMBER_U)).json()["reasons"] == [
        "review:real_person@frame=0"]

    # Other workspace: 404 on read, absent from its review list, 404 on approve.
    assert (await api.get(f"/media/{media_id}", headers=ADMIN_V)).status_code == 404
    assert media_id not in [i["id"] for i in (await api.get("/review", headers=ADMIN_V)).json()]
    assert (await api.post(f"/review/{media_id}/approve", headers=ADMIN_V)).status_code == 404

    # Own workspace: visible to the admin, members get 403, admin approves.
    listed = (await api.get("/review", headers=ADMIN_U)).json()
    assert [i["held_reason_kind"] for i in listed if i["id"] == media_id] == ["score"]
    assert (await api.post(f"/review/{media_id}/approve", headers=MEMBER_U)).status_code == 403
    r = await api.post(f"/review/{media_id}/approve", headers=ADMIN_U)
    assert r.status_code == 200 and r.json()["status"] == "published"
    assert (await api.post(f"/review/{media_id}/approve", headers=ADMIN_U)).status_code == 409


async def test_rejecting_a_hold_blocks_it(api, provider, workers):
    await workers(1)
    media_id = await upload(api, MEMBER_V, image({"self_harm": 0.7}))
    assert (await wait_final(api, MEMBER_V, media_id))[0] == "held"
    r = await api.post(f"/review/{media_id}/reject", headers=ADMIN_V)
    assert r.status_code == 200 and r.json()["status"] == "blocked"


def _sign(body: bytes) -> str:
    import os

    return hmac.new(os.environ["PROVIDER_WEBHOOK_SECRET"].encode(), body, hashlib.sha256).hexdigest()


async def test_callback_signature(client):
    body = json.dumps({"provider_job_id": "pj_unknown", "media_id": "00000000-0000-0000-0000-000000000000",
                       "frames": []}).encode()
    headers = {"Content-Type": "application/json"}
    assert (await client.post("/provider/callback", content=body, headers=headers)).status_code == 401
    bad = {**headers, "X-Signature": "0" * 64}
    assert (await client.post("/provider/callback", content=body, headers=bad)).status_code == 401
    good = {**headers, "X-Signature": _sign(body)}
    assert (await client.post("/provider/callback", content=body, headers=good)).status_code == 200


async def test_media_list_shows_only_own_workspace(client):
    mine = (await client.post("/media", headers=MEMBER_V, json=image())).json()["id"]
    theirs = (await client.post("/media", headers=MEMBER_U, json=image())).json()["id"]
    listed = [m["id"] for m in (await client.get("/media", headers=MEMBER_V)).json()]
    assert str(mine) in [str(i) for i in listed]
    assert str(theirs) not in [str(i) for i in listed]
    assert (await client.get("/media", headers={"X-User-Id": "nobody", "X-Workspace-Id": "ws_verified"})).status_code == 404
