"""Real provider logic with the stub model (no downloads) and a mocked OpenAI."""
import os

os.environ["REAL_MODEL"] = "stub"
os.environ.pop("OPENAI_API_KEY", None)

import pytest  # noqa: E402
from PIL import Image  # noqa: E402

from realprovider import main, scorers  # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "MEDIA_STORE", tmp_path.resolve())
    main.state["primary"] = scorers.StubPrimary()
    import asyncio

    main.state["lock"] = asyncio.Lock()
    (tmp_path / "m1").mkdir()
    Image.new("RGB", (64, 64), (230, 20, 20)).save(tmp_path / "m1" / "red.jpg")
    Image.new("RGB", (64, 64), (20, 40, 200)).save(tmp_path / "m1" / "blue.jpg")
    return tmp_path


async def test_scores_every_frame_with_every_label(store):
    frames = await main._score_frames([{"image": "m1/blue.jpg"}, {"image": "m1/red.jpg"}])
    assert [f["index"] for f in frames] == [0, 1]
    assert all(set(f["scores"]) == set(scorers.LABELS) for f in frames)
    assert frames[0]["scores"]["explicit"] == 0.02 and frames[1]["scores"]["explicit"] == 0.95


async def test_path_outside_the_store_is_refused(store):
    with pytest.raises(ValueError):
        await main._score_frames([{"image": "../../etc/passwd"}])


async def test_failure_sends_an_error_and_no_frames(store, monkeypatch):
    sent = []

    async def capture(url, payload):
        sent.append(payload)

    monkeypatch.setattr(main, "deliver", capture)
    await main._job("rpj_1", main.ScoreRequest(media_id="m1", frames=[{"image": "m1/missing.jpg"}],
                                               callback_url="http://api/cb"))
    assert "frames" not in sent[0] and "bad frame path" in sent[0]["error"]


async def test_openai_secondary_is_combined_conservatively(store, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    async def fake_openai(client, img):
        return {"explicit": 0.3, "self_harm": 0.7, "sexual_violence": 0.1}

    monkeypatch.setattr(scorers, "openai_scores", fake_openai)
    frames = await main._score_frames([{"image": "m1/red.jpg"}, {"image": "m1/blue.jpg"}])
    assert frames[0]["scores"]["explicit"] == 0.95  # max(local 0.95, openai 0.3)
    assert frames[1]["scores"]["explicit"] == 0.3   # max(local 0.02, openai 0.3)
    assert frames[1]["scores"]["self_harm"] == 0.7
    assert frames[0]["scores"]["minor"] == 0.0 and frames[0]["scores"]["real_person"] == 0.0


async def test_openai_failure_fails_the_whole_job(store, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    async def broken(client, img):
        raise scorers.ScoringError("openai HTTP 401")

    sent = []

    async def capture(url, payload):
        sent.append(payload)

    monkeypatch.setattr(scorers, "openai_scores", broken)
    monkeypatch.setattr(main, "deliver", capture)
    await main._job("rpj_2", main.ScoreRequest(media_id="m1", frames=[{"image": "m1/red.jpg"}],
                                               callback_url="http://api/cb"))
    assert "frames" not in sent[0] and "401" in sent[0]["error"]


def test_combine_mapping():
    s = scorers.combine(0.1, {"explicit": 0.9, "self_harm": 0.2, "sexual_violence": 0.05})
    assert s["explicit"] == 0.9 and s["self_harm"] == 0.2 and s["bestiality"] == 0.0
    assert scorers.combine(0.4, None) == {**{k: 0.0 for k in scorers.LABELS}, "explicit": 0.4}


async def test_info_reports_what_is_really_checked(store):
    info = await main.info()
    assert info["checked_labels"] == ["explicit"]
    assert "minor" in info["not_checked_labels"]
