"""decide() is pure, so every boundary is tested directly."""
import pytest

from app.screening.policy import PROHIBITED, Decision, decide


@pytest.mark.parametrize("label", PROHIBITED)
@pytest.mark.parametrize(("score", "expected"), [
    (0.4999, "published"),
    (0.5, "held"),
    (0.7999, "held"),
    (0.8, "blocked"),
    (1.0, "blocked"),
])
@pytest.mark.parametrize("verified", [True, False])
def test_prohibited_boundaries(label, score, expected, verified):
    assert decide([{label: score}], verified).status == expected


@pytest.mark.parametrize(("score", "verified", "expected", "explicit"), [
    (0.7999, False, "published", False),
    (0.8, False, "blocked", True),
    (0.8, True, "published", True),
    (0.95, True, "published", True),
])
def test_explicit(score, verified, expected, explicit):
    d = decide([{"explicit": score}], verified)
    assert (d.status, d.is_explicit) == (expected, explicit)
    if expected == "blocked":
        assert d.reasons == ["explicit_unverified"]


def test_missing_label_scores_zero():
    assert decide([{}], False) == Decision("published", [], False)


def test_prohibited_block_beats_explicit_publish():
    d = decide([{"explicit": 0.99, "real_person": 0.85}], verified=True)
    assert d.status == "blocked" and d.reasons == ["prohibited:real_person@frame=0"]


def test_hold_on_frame_3_and_block_on_frame_50_is_blocked():
    frames = [{} for _ in range(60)]
    frames[3] = {"self_harm": 0.6}
    frames[50] = {"bestiality": 0.9}
    d = decide(frames, verified=True)
    assert d.status == "blocked" and d.reasons == ["prohibited:bestiality@frame=50"]


def test_hold_beats_explicit_block_in_unverified():
    d = decide([{"explicit": 0.9, "minor": 0.6}], verified=False)
    assert d.status == "held" and d.reasons == ["review:minor@frame=0"]


def test_every_frame_counts_not_a_prefix():
    frames = [{} for _ in range(60)]
    frames[59] = {"minor": 0.8}
    assert decide(frames, verified=True).reasons == ["prohibited:minor@frame=59"]


def test_reasons_name_label_and_frame():
    d = decide([{}, {"real_person": 0.55}, {"real_person": 0.6}], verified=True)
    assert d.status == "held"
    assert d.reasons == ["review:real_person@frame=1", "review:real_person@frame=2"]
