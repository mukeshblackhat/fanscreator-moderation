"""Generation prompt engine: score a prompt before anything is generated.

assess(prompt) -> PromptAssessment with a 0..1 score per category, the rule
hits behind each score, and a decision on the same scale as media:

  any category >= 0.8   refuse   422 before the generator or provider is called
  any category >= 0.5   review   generate, but hold the output for a human
                                 (reason "review:prompt_<category>"), even if
                                 the output itself scores clean
  otherwise             allow    generate; the output is screened as usual

Categories: minor, sexual_violence, bestiality, self_harm, real_person,
incest, trafficking. `explicit` is also scored, for information only:
explicit prompts are allowed (verified workspaces may publish explicit
output; unverified ones block it at the output check).

Steps:
1. Normalise: Unicode NFKC, lowercase, strip zero-width characters, collapse
   words split by spaces or punctuation ("t.e.e.n", "t e e n"), map common
   digit/symbol substitutions ("t33n" -> "teen"), collapse long repeats.
2. Weighted rules per category. A rule is a set of terms and a weight. Some
   terms only count, or count more, when the prompt also has sexual context
   ("young" alone 0.3, "young" + "nude" 0.65; "unconscious" + sexual 0.9).
   A category's score is the highest weight among its rules that matched.

check(prompt) keeps the old contract: the "prompt:<category>" reasons of the
refuse band (an empty list means the prompt may be generated).

Rules alone are weak: they miss paraphrase, other languages and new slang,
and they over-block innocent text. In production this module would call a
safety classifier (for example Llama Guard 4 with our own category
definitions) after the rules, and also check negative prompts, reference
images and LoRAs. The output is screened anyway. Keeping the logic behind
one function means the classifier can be swapped in without touching routes.

Term lists are kept deliberately short and synthetic.
"""
import re
import unicodedata

ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)
LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i"})

NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
}

AGE_PATTERNS = [
    # "16 year old", "16-year-old", "16yo", "16 y/o", "16 yrs", "16 years"
    re.compile(r"\b(\d{1,2})\s*(?:years?|yrs?|yo|y/o|y\.o\.?)(?:\s*old)?\b"),
    # "age 15", "aged 15", "age: 15"
    re.compile(r"\bage[ds]?\s*:?\s*(\d{1,2})\b"),
    # "sixteen year old", "sixteen yo"
    re.compile(r"\b(" + "|".join(NUMBER_WORDS) + r")\s*(?:years?|yrs?|yo)(?:\s*old)?\b"),
]

from dataclasses import dataclass, field

REFUSE_AT = 0.8
REVIEW_AT = 0.5
CATEGORIES = ("minor", "sexual_violence", "bestiality", "self_harm", "real_person", "incest", "trafficking")

# Sexual context. Some weak terms only matter next to these.
SEXUAL = ["nude", "naked", "sex", "sexy", "sexual", "explicit", "nsfw", "porn", "porno", "lingerie",
          "topless", "erotic", "undressed", "undressing", "seductive", "horny", "orgasm", "intercourse",
          "fetish", "stripping", "boudoir"]
EXPLICIT_STRONG = ["sex", "sexual", "explicit", "nsfw", "porn", "porno", "nude", "naked", "topless",
                   "intercourse", "orgasm", "erotic"]
EXPLICIT_SOFT = ["sexy", "lingerie", "seductive", "boudoir", "undressed", "undressing", "stripping", "fetish"]


@dataclass(frozen=True)
class Rule:
    weight: float                 # score when the terms match
    terms: tuple[str, ...]
    with_sexual: float | None = None  # score when the terms match AND there is sexual context
    label: str = ""


RULES: dict[str, list[Rule]] = {
    "minor": [
        Rule(0.95, ("child", "children", "kid", "kids", "minor", "minors", "underage", "under age", "preteen",
                    "toddler", "infant", "little girl", "little boy", "young girl", "young boy", "schoolgirl",
                    "schoolboy", "middle school", "elementary school", "jailbait", "loli"), label="minor term"),
        Rule(0.85, ("teen", "teens", "teenager", "high school"), label="teen term"),
        Rule(0.6, ("barely legal", "school uniform", "babyface", "baby face"), with_sexual=0.85,
             label="youth-coded term"),
        Rule(0.3, ("young", "petite", "youthful", "tiny", "innocent looking"), with_sexual=0.65,
             label="ambiguous youth term"),
    ],
    "sexual_violence": [
        Rule(0.95, ("rape", "raped", "raping", "non consensual", "nonconsensual", "noncon", "forced sex",
                    "against her will", "against his will", "sexual assault"), label="violence term"),
        Rule(0.5, ("drugged",), with_sexual=0.95, label="incapacitation term"),
        Rule(0.2, ("unconscious", "asleep", "passed out", "sleeping"), with_sexual=0.9, label="no-consent term"),
        Rule(0.2, ("forced", "struggling", "restrained", "tied up", "crying", "screaming"), with_sexual=0.7,
             label="force term"),
    ],
    "bestiality": [
        Rule(0.95, ("bestiality", "zoophilia", "animal sex"), label="bestiality term"),
        Rule(0.0, ("dog", "dogs", "horse", "horses", "animal", "animals", "pony", "goat", "pig", "canine",
                   "beast"), with_sexual=0.9, label="animal + sexual context"),
    ],
    "self_harm": [
        Rule(0.9, ("self harm", "selfharm", "cutting myself", "cutting herself", "cutting himself", "suicide",
                   "suicidal", "slit wrists", "slitting her wrists", "hanging herself", "hanging himself"),
             label="self-harm term"),
        Rule(0.55, ("scars on her wrists", "scars on his wrists", "blood on her wrists", "razor blade",
                    "overdose", "pills everywhere"), label="self-harm cue"),
    ],
    "real_person": [
        Rule(0.9, ("taylor swift", "scarlett johansson", "emma watson", "billie eilish", "elon musk",
                   "deepfake", "deep fake"), label="named real person"),
        Rule(0.55, ("celebrity", "famous actress", "famous singer", "lookalike", "look alike",
                    "real person", "my ex", "my coworker", "my neighbor"), label="real-person cue"),
    ],
    "incest": [
        Rule(0.95, ("incest", "with my sister", "with my brother", "with her father", "with his mother",
                    "father and daughter", "mother and son", "brother and sister"), label="incest term"),
        Rule(0.1, ("stepsister", "step sister", "stepbrother", "step brother", "stepmom", "step mom",
                   "stepdad", "step dad", "stepdaughter", "stepson", "cousin"), with_sexual=0.6,
             label="step-family + sexual context"),
    ],
    "trafficking": [
        Rule(0.95, ("trafficking", "trafficked", "sex slave", "sold for sex"), label="trafficking term"),
        Rule(0.2, ("slave", "enslaved"), with_sexual=0.85, label="slavery + sexual context"),
        Rule(0.2, ("kidnapped", "captive", "sold", "smuggled"), with_sexual=0.6, label="captivity + sexual context"),
    ],
}


def _pattern(terms: tuple[str, ...] | list[str]) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(re.escape(t) for t in terms) + r")\b")


_RULE_RE = {cat: [(rule, _pattern(rule.terms)) for rule in rules] for cat, rules in RULES.items()}
_SEXUAL_RE = _pattern(SEXUAL)
_EXPLICIT_STRONG_RE = _pattern(EXPLICIT_STRONG)
_EXPLICIT_SOFT_RE = _pattern(EXPLICIT_SOFT)
_SPLIT_LETTERS = re.compile(r"\b(?:[a-z0-9][\s._\-*]+){2,}[a-z0-9]\b")


@dataclass
class PromptAssessment:
    decision: str                                   # "refuse" | "review" | "allow"
    scores: dict[str, float]                        # every category plus "explicit"
    hits: dict[str, list[str]] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)  # prompt:<cat> (refuse) or review:prompt_<cat> (review)

    @property
    def review_categories(self) -> list[str]:
        return [c for c in CATEGORIES if REVIEW_AT <= self.scores[c] < REFUSE_AT]


def _join_split_letters(text: str) -> str:
    """"t.e.e.n" / "t e e n" / "t-e-e-n" -> "teen" (only runs of single characters)."""
    return _SPLIT_LETTERS.sub(lambda m: re.sub(r"[\s._\-*]+", "", m.group(0)), text)


def normalise(prompt: str) -> tuple[str, str]:
    """Return (with_digits, words). Digits are kept in the first form for age
    checks and mapped to letters in the second form for term checks."""
    text = unicodedata.normalize("NFKC", prompt).translate(ZERO_WIDTH).lower()
    text = re.sub(r"(.)\1{2,}", r"\1\1", text)  # "teeeeen" -> "teen"
    text = _join_split_letters(text)
    with_digits = re.sub(r"[-_/]+", " ", text)
    with_digits = re.sub(r"\s+", " ", with_digits).strip()
    words = text.translate(LEET)
    words = re.sub(r"[^a-z\s]+", " ", words)
    words = re.sub(r"\s+", " ", words).strip()
    return with_digits, words


def _under_18(with_digits: str) -> bool:
    for pattern in AGE_PATTERNS:
        for m in pattern.finditer(with_digits):
            raw = m.group(1)
            age = NUMBER_WORDS.get(raw) if not raw.isdigit() else int(raw)
            if age is not None and age < 18:
                return True
    return False


def _split_runs(prompt: str) -> list[str]:
    """Words spelled out one character at a time, joined and leet-mapped.
    "a t e e n" -> "ateen": the article gets glued on, so these runs are
    searched for terms as substrings instead of whole words."""
    text = unicodedata.normalize("NFKC", prompt).translate(ZERO_WIDTH).lower()
    runs = [re.sub(r"[\s._\-*]+", "", m.group(0)) for m in _SPLIT_LETTERS.finditer(text)]
    return [r.translate(LEET) for r in runs]


def _find(pattern: re.Pattern[str], terms: tuple[str, ...], texts: tuple[str, str], runs: list[str]) -> str | None:
    for text in texts:
        m = pattern.search(text)
        if m:
            return m.group(0)
    for run in runs:  # words spelled out letter by letter: substring match
        for t in terms:
            squashed = t.replace(" ", "")
            if len(squashed) >= 4 and squashed in run:
                return t
    return None


# A negative prompt that excludes adulthood next to a sexual prompt is asking for a minor.
_ADULT_EXCLUSION_RE = _pattern(("adult", "adults", "mature", "grown up", "grownup", "old", "older", "aged",
                                "wrinkles", "milf", "woman", "women", "man", "men"))


def assess(prompt: str, negative_prompt: str | None = None) -> PromptAssessment:
    with_digits, words = normalise(prompt)
    texts = (words, with_digits)
    runs = _split_runs(prompt)
    sexual = bool(_SEXUAL_RE.search(words)) or any(t in r for r in runs for t in SEXUAL if len(t) >= 4)

    scores = {c: 0.0 for c in CATEGORIES}
    hits: dict[str, list[str]] = {}
    for category, rules in _RULE_RE.items():
        for rule, pattern in rules:
            found = _find(pattern, rule.terms, texts, runs)
            if found is None:
                continue
            weight = rule.with_sexual if (sexual and rule.with_sexual is not None) else rule.weight
            if weight <= 0:
                continue
            note = f"{rule.label}: {found!r}" + (" + sexual context" if sexual and rule.with_sexual else "")
            hits.setdefault(category, []).append(f"{note} -> {weight}")
            scores[category] = max(scores[category], weight)
    if _under_18(with_digits):
        scores["minor"] = 1.0
        hits.setdefault("minor", []).append("stated age under 18 -> 1.0")
    if negative_prompt:
        _, negative_words = normalise(negative_prompt)
        m = _ADULT_EXCLUSION_RE.search(negative_words)
        if m and sexual:
            scores["minor"] = max(scores["minor"], 0.9)
            hits.setdefault("minor", []).append(f"negative prompt excludes adults: {m.group(0)!r} + sexual context -> 0.9")

    scores["explicit"] = 0.9 if _EXPLICIT_STRONG_RE.search(words) else 0.6 if _EXPLICIT_SOFT_RE.search(words) else 0.0
    refuse = [c for c in CATEGORIES if scores[c] >= REFUSE_AT]
    review = [c for c in CATEGORIES if REVIEW_AT <= scores[c] < REFUSE_AT]
    if refuse:
        return PromptAssessment("refuse", scores, hits, sorted(f"prompt:{c}" for c in refuse))
    if review:
        return PromptAssessment("review", scores, hits, sorted(f"review:prompt_{c}" for c in review))
    return PromptAssessment("allow", scores, hits, [])


def check(prompt: str, negative_prompt: str | None = None) -> list[str]:
    """Reasons to refuse the prompt (the refuse band). Empty means it may be generated."""
    a = assess(prompt, negative_prompt)
    return a.reasons if a.decision == "refuse" else []
