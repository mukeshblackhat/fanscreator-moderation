"""Generation prompt gate: refuse before anything is generated or scored.

check(prompt) -> list of reasons like "prompt:minor". Empty list = allowed.

Steps:
1. Normalise: Unicode NFKC, lowercase, strip zero-width characters, collapse
   words split by spaces or punctuation ("t.e.e.n", "t e e n"), map common
   digit/symbol substitutions ("t33n" -> "teen"), collapse long repeats.
2. Rules per category: minor, sexual_violence, bestiality, self_harm,
   real_person, incest, trafficking.

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

TERMS: dict[str, list[str]] = {
    "minor": ["child", "children", "kid", "kids", "teen", "teens", "teenager", "minor", "minors",
              "underage", "under age", "preteen", "schoolgirl", "schoolboy", "toddler", "infant",
              "young girl", "young boy", "little girl", "little boy", "middle school", "high school"],
    "sexual_violence": ["rape", "raped", "raping", "non consensual", "nonconsensual", "noncon",
                        "forced sex", "against her will", "against his will", "drugged"],
    "bestiality": ["bestiality", "zoophilia", "animal sex", "sex with a dog", "sex with a horse"],
    "self_harm": ["self harm", "selfharm", "cutting myself", "cutting herself", "suicide", "slit wrists"],
    "real_person": ["taylor swift", "scarlett johansson", "emma watson", "billie eilish", "elon musk"],
    "incest": ["incest", "with my sister", "with my brother", "with her father", "with his mother",
               "father and daughter", "mother and son", "brother and sister"],
    "trafficking": ["trafficking", "trafficked", "sex slave", "sold for sex"],
}

_TERM_RE = {
    cat: re.compile(r"\b(?:" + "|".join(re.escape(t) for t in terms) + r")\b")
    for cat, terms in TERMS.items()
}
_SPLIT_LETTERS = re.compile(r"\b(?:[a-z0-9][\s._\-*]+){2,}[a-z0-9]\b")


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


def check(prompt: str) -> list[str]:
    with_digits, words = normalise(prompt)
    runs = _split_runs(prompt)
    reasons: list[str] = []
    for category, pattern in _TERM_RE.items():
        squashed_terms = [t.replace(" ", "") for t in TERMS[category]]
        if (pattern.search(words) or pattern.search(with_digits)
                or any(t in run for run in runs for t in squashed_terms if len(t) >= 4)):
            reasons.append(f"prompt:{category}")
    if "prompt:minor" not in reasons and _under_18(with_digits):
        reasons.append("prompt:minor")
    return sorted(reasons)
