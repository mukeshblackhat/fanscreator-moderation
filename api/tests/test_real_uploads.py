"""Real file uploads end to end: API -> media store -> worker -> real provider
(stub model: a mostly red image scores explicit 0.95) -> signed callback -> decision."""
import hashlib
import hmac
import io
import json
import subprocess
import uuid
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import text

from app.config import settings
from app.screening import pipeline, queue
from app.screening.provider_client import ALL_LABELS, ProviderOutcome, validate_frames
from conftest import ADMIN_U, ADMIN_V, MEDIA_STORE, MEMBER_U, MEMBER_V, MODE, wait_final

pytestmark = pytest.mark.skipif(MODE != "fixed", reason="real uploads use the fixed pipeline")
RED, BLUE = (230, 20, 20), (20, 40, 200)


def png(color, size=(320, 240)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def mp4(tmp_path: Path, seconds_blue: int, seconds_red: int) -> bytes:
    out = tmp_path / f"clip-{uuid.uuid4().hex[:6]}.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color=c=0x1428c8:s=320x240:d={seconds_blue}:r=10",
        "-f", "lavfi", "-i", f"color=c=0xe61414:s=320x240:d={seconds_red}:r=10",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]", "-pix_fmt", "yuv420p", str(out)],
        check=True)
    return out.read_bytes()


async def send(api, who, data: bytes, name: str, ctype: str):
    return await api.post("/media/upload", headers=who, files={"file": (name, data, ctype)})


async def test_explicit_photo_blocked_when_unverified_published_when_verified(api, provider, workers):
    await workers(1)
    r1 = await send(api, MEMBER_U, png(RED), "a.png", "image/png")
    r2 = await send(api, MEMBER_V, png(RED), "b.png", "image/png")
    assert r1.status_code == 202 and r1.json()["frames"] == 1
    assert (await wait_final(api, MEMBER_U, r1.json()["id"]))[0] == "blocked"
    assert (await wait_final(api, MEMBER_V, r2.json()["id"]))[0] == "published"
    body = (await api.get(f"/media/{r1.json()['id']}", headers=MEMBER_U)).json()
    assert body["reasons"] == ["explicit_unverified"]


async def test_clean_photo_published_and_viewable_by_members_only_in_its_workspace(api, provider, workers):
    await workers(1)
    media_id = (await send(api, MEMBER_U, png(BLUE), "c.jpg", "image/jpeg")).json()["id"]
    assert (await wait_final(api, MEMBER_U, media_id))[0] == "published"
    frame = await api.get(f"/media/{media_id}/frames/0", headers=MEMBER_U)
    assert frame.status_code == 200 and frame.headers["content-type"] == "image/jpeg"
    assert frame.headers["cache-control"] == "private, no-store"
    assert (await api.get(f"/media/{media_id}/frames/0", headers=ADMIN_V)).status_code == 404
    assert (await api.get(f"/media/{media_id}/frames/1", headers=MEMBER_U)).status_code == 404


async def test_video_with_explicit_ending_is_blocked_at_the_right_second(api, provider, workers, tmp_path):
    await workers(1)
    r = await send(api, MEMBER_U, mp4(tmp_path, 4, 2), "clip.mp4", "video/mp4")
    assert r.status_code == 202, r.text
    n = r.json()["frames"]
    assert 5 <= n <= 7  # 6 s at 1 frame per second
    final, _ = await wait_final(api, MEMBER_U, r.json()["id"], timeout=40)
    assert final == "blocked"
    # Blocked media is never served, not even to the admin.
    assert (await api.get(f"/media/{r.json()['id']}/frames/0", headers=ADMIN_U)).status_code == 404


async def test_rejected_uploads(api):
    assert (await send(api, MEMBER_U, b"%PDF-1.4", "x.pdf", "application/pdf")).status_code == 422
    bad = await send(api, MEMBER_U, b"not really a png", "x.png", "image/png")
    assert bad.status_code == 422 and bad.json()["reasons"][0].startswith("upload:not a readable image")
    assert (await send(api, {"X-User-Id": "nobody", "X-Workspace-Id": "ws_verified"}, png(BLUE), "x.png",
                       "image/png")).status_code == 404


async def test_video_over_the_limit_is_refused(api, tmp_path):
    r = await send(api, MEMBER_U, mp4(tmp_path, 21, 1), "long.mp4", "video/mp4")
    assert r.status_code == 422 and "limit is 20 s" in r.json()["reasons"][0]


async def test_held_frames_are_for_admins_only(api, db):
    media_id = uuid.uuid4()
    folder = Path(MEDIA_STORE) / str(media_id)
    folder.mkdir()
    Image.new("RGB", (64, 64), BLUE).save(folder / "frame_0000.jpg")
    await db.execute(text(
        """
        INSERT INTO media (id, workspace_id, type, source, frame_count, frames, status, held_reason_kind, reasons)
        VALUES (:id, 'ws_unverified', 'image', 'upload', 1, CAST(:f AS jsonb), 'held', 'score',
                '["review:self_harm@frame=0"]'::jsonb)
        """), {"id": media_id, "f": json.dumps([{"scores": {}, "image": f"{media_id}/frame_0000.jpg"}])})
    await db.commit()
    assert (await api.get(f"/media/{media_id}/frames/0", headers=ADMIN_U)).status_code == 200
    assert (await api.get(f"/media/{media_id}/frames/0", headers=MEMBER_U)).status_code == 404


async def test_real_mode_info(api):
    info = (await api.get("/real-mode")).json()
    assert info["available"] is True
    # real_person has no identity detector without a premium key: a person in frame is held, never 0
    assert "minor" in info["checked_labels"] and info["not_checked_labels"] == ["real_person"]


async def test_mock_adapter_scores_real_uploads_on_the_mock(api, provider, workers, monkeypatch):
    """MODERATION_PROVIDER=mock: the red photo goes to the mock, which scores it all zero,
    so it is published where the real model would have blocked it."""
    monkeypatch.setattr(settings, "moderation_provider", "mock")
    await workers(1)
    media_id = (await send(api, MEMBER_U, png(RED), "a.jpg", "image/jpeg")).json()["id"]
    assert (await wait_final(api, MEMBER_U, media_id))[0] == "published"
    assert (await provider.get("/_stats")).json()["calls"] == {media_id: 1}


async def test_real_callback_missing_a_label_is_held_never_published(api, workers):
    """An unchecked label is not a 0. The real provider's answer must carry all six,
    else it is malformed: retried once, then held as provider_malformed."""
    media_id = (await send(api, MEMBER_V, png(BLUE), "b.jpg", "image/jpeg")).json()["id"]
    partial = {label: 0.0 for label in ALL_LABELS if label != "minor"}
    for attempt in range(settings.max_malformed_attempts):
        job = await queue.claim("image")
        assert job is not None and str(job.media_id) == media_id
        provider_job_id = f"rpj_missing_{attempt}"
        call_id = await pipeline.open_call(job, "async_submit")
        await pipeline.close_call(call_id, ProviderOutcome("accepted", 202, provider_job_id=provider_job_id))
        assert await queue.mark_waiting_callback(job, provider_job_id)
        body = json.dumps({"provider_job_id": provider_job_id, "media_id": media_id,
                           "frames": [{"index": 0, "scores": partial, "checked_by": {"explicit": "nsfw"}}]}).encode()
        sig = hmac.new(settings.provider_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        r = await api.post("/provider/callback", content=body, headers={"X-Signature": sig})
        assert r.json()["result"] == "malformed"
    status, seen = await wait_final(api, ADMIN_V, media_id, timeout=5)
    assert status == "held" and "published" not in seen
    assert (await api.get(f"/media/{media_id}", headers=ADMIN_V)).json()["reasons"] == ["provider_malformed"]


def test_extra_frame_keys_like_checked_by_are_allowed():
    full = {label: 0.1 for label in ALL_LABELS}
    body = {"frames": [{"index": 0, "scores": full, "checked_by": {"explicit": "nsfw"}}]}
    assert validate_frames(body, 1) == [full]
