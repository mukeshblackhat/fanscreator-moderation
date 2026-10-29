"""Prompt gate rules, including simple obfuscation. Prompts are minimal and synthetic."""
import pytest

from app.screening.prompt_gate import check, normalise


@pytest.mark.parametrize("prompt", [
    "a 16 year old at the beach",
    "a 16-year-old at the beach",
    "16yo on a bed",
    "a sixteen year old",
    "a sixteen-year-old",
    "aged 15, posing",
    "a t33n posing",
    "a t.e.e.n posing",
    "a t e e n posing",
    "a te​en posing",               # zero-width space inside the word
    "a ＴＥＥＮ posing",                 # full-width letters (NFKC)
    "a teeeeen posing",
    "an underage model",
    "a schoolgirl outfit on a little girl",
])
def test_minor_refused(prompt):
    assert "prompt:minor" in check(prompt)


@pytest.mark.parametrize(("prompt", "reason"), [
    ("a r4pe scene", "prompt:sexual_violence"),
    ("non-consensual scene", "prompt:sexual_violence"),
    ("zoophilia", "prompt:bestiality"),
    ("self-harm aesthetic", "prompt:self_harm"),
    ("Taylor Swift in a bikini", "prompt:real_person"),
    ("incest roleplay", "prompt:incest"),
    ("a sex slave", "prompt:trafficking"),
])
def test_other_categories_refused(prompt, reason):
    assert reason in check(prompt)


@pytest.mark.parametrize("prompt", [
    "a cat surfing at sunset",
    "a lighthouse in a storm",
    "a 25 year old woman on a beach",
    "an 18-year-old graduate",
    "two adults, both 30 years old",
    "kidney beans in a bowl",            # word boundaries: "kid" is not inside "kidney"
    "a teenage mutant ninja turtle style frog",  # "teenage" is not a listed term; documented limit
])
def test_allowed(prompt):
    assert check(prompt) == []


def test_reasons_are_sorted_and_unique():
    assert check("a 15 year old teen, incest") == ["prompt:incest", "prompt:minor"]


def test_normalise_keeps_digits_for_ages_and_maps_them_for_words():
    with_digits, words = normalise("A T33N, 16-yo")
    assert "16 yo" in with_digits
    assert "teen" in words
