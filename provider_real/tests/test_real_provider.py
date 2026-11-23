"""Real provider logic with the stub tier 1 (colour-coded cues, no downloads) and OpenAI
mocked at the HTTP layer."""
import os

os.environ["REAL_MODEL"] = "stub"
os.environ.pop("OPENAI_API_KEY", None)

import asyncio  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from realprovider import main, scorers  # noqa: E402

COLOURS = {"blue": (20, 40, 200), "red": (230, 20, 20), "yellow": (230, 230, 20), "magenta": (230, 20, 230),
           "white": (240, 240, 240), "orange": (240, 140, 20), "green": (20, 200, 20)}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "MEDIA_STORE", tmp_path.resolve())
    main.state["tier1"] = scorers.StubTier1()
    main.state["lock"] = asyncio.Lock()
    (tmp_path / "m1").mkdir()
    for k, (name, rgb) in enumerate(COLOURS.items()):
        img = Image.new("RGB", (64, 64), rgb)
        ImageDraw.Draw(img).rectangle((0, 0, 4 + 4 * k, 12), fill=(0, 0, 0))  # distinct hash, centre untouched
        img.save(tmp_path / "m1" / f"{name}.jpg")
    return tmp_path


def frames(*names):
    return [{"image": f"m1/{n}.jpg"} for n in names]


def openai_mock(monkeypatch, status=200, scores=None):
    """Patch httpx so OpenAI calls hit a MockTransport; returns the list of requests seen."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    seen = []

    def handler(request):
        seen.append(request)
        if status != 200:
            return httpx.Response(status, text="nope")
        return httpx.Response(200, json={"results": [{"category_scores": scores or {}}]})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return seen


async def test_every_frame_has_all_six_labels_and_who_set_them(store):
    out, counts = await main._score_frames(frames(*COLOURS))
    assert [f["index"] for f in out] == list(range(len(COLOURS)))
    for f in out:
        assert set(f["scores"]) == set(scorers.LABELS) == set(f["checked_by"])
    assert out[0]["scores"]["explicit"] == 0.02 and out[1]["scores"]["explicit"] == 0.95
    assert counts == {"frames_total": 7, "frames_escalated": 0, "frames_reused": 0}  # no OpenAI key


async def test_a_person_is_held_for_real_person_because_nothing_checks_identity(store):
    blue, yellow = (await main._score_frames(frames("blue", "yellow")))[0]
    assert yellow["scores"]["real_person"] == 0.5 and yellow["checked_by"]["real_person"] == "hold:no_identity_detector"
    assert blue["scores"]["real_person"] < 0.5 and blue["checked_by"]["real_person"] == "tier1:clip_no_person"


async def test_minor_floor_on_sexual_person_frames_and_strong_cue_blocks(store):
    magenta, white, yellow = (await main._score_frames(frames("magenta", "white", "yellow")))[0]
    assert magenta["scores"]["minor"] == 0.5 and magenta["checked_by"]["minor"] == "hold:sexual_age_unclear"
    assert white["scores"]["minor"] >= 0.8  # strong cue on a person blocks
    assert yellow["scores"]["minor"] < 0.5  # clothed adult, weak cue: no hold from minor


async def test_bestiality_combines_animal_and_sexual(store):
    (orange,), _ = await main._score_frames(frames("orange"))
    assert orange["scores"]["bestiality"] == 0.5 and orange["checked_by"]["bestiality"] == "hold:animal_and_sexual"
    both, _ = scorers.combine({**scorers._SAFE, "animal": 0.9, "explicit": 0.85}, None)
    assert both["bestiality"] == 0.85
    pet, _ = scorers.combine({**scorers._SAFE, "animal": 0.9}, None)
    assert pet["bestiality"] < 0.5  # a pet in a clean frame is fine


async def test_uncertain_self_harm_is_held_without_tier2_and_resolved_with_it(store, monkeypatch):
    (green,), _ = await main._score_frames(frames("green"))
    assert green["scores"]["self_harm"] == 0.5 and green["checked_by"]["self_harm"] == "hold:uncertain_no_tier2"
    openai_mock(monkeypatch, scores={"self-harm": 0.05})
    (green,), _ = await main._score_frames(frames("green"))
    assert green["scores"]["self_harm"] == 0.35 and green["checked_by"]["self_harm"] == "tier1:clip+tier2:openai"


async def test_clear_safe_frames_skip_openai_and_duplicates_reuse_its_answer(store, monkeypatch):
    seen = openai_mock(monkeypatch, scores={"sexual": 0.99, "violence": 0.9})
    out, counts = await main._score_frames(frames("blue", "red", "red", "blue", "green"))
    assert len(seen) == 2  # red once (the second red reuses it), green; blue never
    assert counts == {"frames_total": 5, "frames_escalated": 2, "frames_reused": 1}
    assert out[0]["checked_by"]["explicit"] == "tier1:nsfw" and out[0]["scores"]["explicit"] == 0.02
    assert out[2]["checked_by"]["explicit"] == "tier1:nsfw+tier2:openai"
    assert out[2]["scores"]["sexual_violence"] == 0.9


async def test_path_outside_the_store_is_refused(store):
    with pytest.raises(ValueError):
        await main._score_frames([{"image": "../../etc/passwd"}])


async def job(monkeypatch, media):
    sent = []

    async def capture(url, payload):
        sent.append(payload)

    monkeypatch.setattr(main, "deliver", capture)
    await main._job("rpj_1", main.ScoreRequest(media_id="m1", frames=media, callback_url="http://api/cb"))
    return sent[0]


async def test_failure_sends_an_error_and_no_frames(store, monkeypatch):
    payload = await job(monkeypatch, frames("missing"))
    assert "frames" not in payload and "bad frame path" in payload["error"]


async def test_openai_failure_fails_the_whole_job(store, monkeypatch):
    openai_mock(monkeypatch, status=401)
    payload = await job(monkeypatch, frames("blue", "red"))
    assert "frames" not in payload and "401" in payload["error"]


async def test_info_names_a_detector_for_every_label(store):
    info = await main.info()
    assert set(info["labels"]) == set(scorers.LABELS)
    assert info["not_checked_labels"] == ["real_person"] and info["secondary"] is None
    assert any("real_person" in rule for rule in info["hold_rules"])
