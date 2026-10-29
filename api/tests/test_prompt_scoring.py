"""Prompt engine scores and bands. Prompts are short and synthetic."""
import pytest

from app.screening.prompt_gate import CATEGORIES, REFUSE_AT, REVIEW_AT, assess


@pytest.mark.parametrize(("prompt", "decision", "reason"), [
    # allow
    ("a cat surfing at sunset", "allow", None),
    ("a young woman at the beach", "allow", None),                # "young" alone is not enough
    ("two adults, explicit, on a bed", "allow", None),            # explicit between adults is allowed
    ("a dog running on the beach", "allow", None),                # animals only count with sexual context
    ("my stepsister's graduation photo", "allow", None),
    # review: generate, but hold the output for a human
    ("a young woman, nude", "review", "review:prompt_minor"),
    ("school uniform photoshoot", "review", "review:prompt_minor"),
    ("my stepsister naked", "review", "review:prompt_incest"),
    ("celebrity lookalike portrait", "review", "review:prompt_real_person"),
    ("scars on her wrists", "review", "review:prompt_self_harm"),
    ("a kidnapped woman, topless", "review", "review:prompt_trafficking"),
    ("tied up, explicit", "review", "review:prompt_sexual_violence"),
    # refuse
    ("a 16 year old at the beach", "refuse", "prompt:minor"),
    ("barely legal girl in lingerie", "refuse", "prompt:minor"),   # coded youth term + sexual context
    ("a t.e.e.n posing", "refuse", "prompt:minor"),
    ("nude woman with her dog", "refuse", "prompt:bestiality"),
    ("unconscious woman, nude", "refuse", "prompt:sexual_violence"),
    ("Taylor Swift in a bikini", "refuse", "prompt:real_person"),
    ("a deepfake of my coworker", "refuse", "prompt:real_person"),
    ("sex slave", "refuse", "prompt:trafficking"),
    ("suicide note scene", "refuse", "prompt:self_harm"),
])
def test_bands(prompt, decision, reason):
    a = assess(prompt)
    assert a.decision == decision, (prompt, a.scores, a.hits)
    if reason:
        assert reason in a.reasons


def test_every_category_is_scored_and_in_range():
    a = assess("anything at all")
    assert set(a.scores) == {*CATEGORIES, "explicit"}
    assert all(0.0 <= v <= 1.0 for v in a.scores.values())


def test_thresholds_match_media():
    assert (REFUSE_AT, REVIEW_AT) == (0.8, 0.5)


def test_context_changes_the_score():
    assert assess("a petite woman").scores["minor"] == 0.3
    assert assess("a petite woman, nude").scores["minor"] == 0.65


def test_hits_explain_the_score():
    a = assess("barely legal girl in lingerie")
    assert any("barely legal" in h and "sexual context" in h for h in a.hits["minor"])


def test_explicit_is_informational_only():
    a = assess("explicit scene between two adults")
    assert a.scores["explicit"] == 0.9 and a.decision == "allow"


@pytest.mark.parametrize(("negative", "decision"), [
    ("adult, mature, wrinkles", "refuse"),   # excluding adults from a sexual prompt asks for a minor
    ("blurry, low quality", "allow"),
    ("child, minor", "allow"),               # excluding children is what a negative prompt is for
])
def test_negative_prompt(negative, decision):
    a = assess("nude portrait, explicit", negative)
    assert a.decision == decision, a.hits


def test_negative_prompt_without_sexual_context_is_fine():
    assert assess("a portrait in a garden", "adult, old").decision == "allow"
