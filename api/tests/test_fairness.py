"""A burst from one workspace must not starve another."""
import asyncio
import time

import pytest

from conftest import ADMIN_U, ADMIN_V, MODE, image, upload, video, wait_final

pytestmark = pytest.mark.skipif(MODE != "fixed", reason="lanes and fair claiming exist only in the fixed pipeline")


async def test_video_burst_does_not_starve_another_workspace(api, provider, workers):
    # 30 sync videos from ws_verified, each taking 300 ms at the provider.
    # One worker has 2 video slots, so the burst alone needs about 4.5 s.
    await provider.post("/_control", json={"mode": "slow", "delay_ms": 300})
    await workers(1)
    burst = await asyncio.gather(*(upload(api, ADMIN_V, video(10)) for _ in range(30)))
    await asyncio.sleep(0.3)

    started = time.monotonic()
    other_video = await upload(api, ADMIN_U, video(10))
    other_image = await upload(api, ADMIN_U, image())
    assert (await wait_final(api, ADMIN_U, other_image))[0] == "published"
    image_latency = time.monotonic() - started
    assert (await wait_final(api, ADMIN_U, other_video))[0] == "published"
    video_latency = time.monotonic() - started

    # Fair claim: the workspace with the fewest running jobs goes next, so
    # ws_unverified's video jumps ahead of the remaining burst.
    assert video_latency < 2.0, f"ws_unverified video waited {video_latency:.1f}s behind the burst"
    # Separate lane: images never queue behind videos.
    assert image_latency < 1.5, f"ws_unverified image waited {image_latency:.1f}s"
    pending = [m for m in burst if (await api.get(f"/media/{m}", headers=ADMIN_V)).json()["status"] == "pending"]
    assert pending, "the burst should still be in progress, otherwise this test proves nothing"


async def test_metrics_summary(api, provider, workers):
    await workers(1)
    media_id = await upload(api, ADMIN_V, image({"self_harm": 0.6}))
    assert (await wait_final(api, ADMIN_V, media_id))[0] == "held"
    m = (await api.get("/metrics/summary")).json()
    assert m["unchecked_published"] == 0
    assert m["held_by_reason"] == {"score": 1}
    assert m["provider_calls_per_screened_media"] == 1.0
    assert m["breaker"] == "closed"
