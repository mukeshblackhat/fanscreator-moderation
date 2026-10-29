"""Generation prompt checker: refuse or allow, before anything is generated.

Follows the brief's rules and our notes (docs/decisions.md #36):

* Refuse a prompt that asks for any of: minor (anyone under 18 or who appears
  to be), sexual violence or sex without consent, bestiality, self-harm, a real
  identifiable person, incest, trafficking. In every workspace, verified or not.
* Refusal happens before anything is generated or scored: 422 {"reasons"} with
  the category only, never the word that triggered it.
* Adult or explicit prompts are NOT refused. Explicit content is handled at the
  output check, based on whether the workspace is verified.
* There is no middle "review" band. A prompt is refused or allowed, with a bias
  towards refusing: a false refusal is acceptable, a false pass is not. Every
  generated output is still screened like an upload (the final backstop).

Interface (the same shape a production model would return):

    under_18_or_looks_young, sexual_content, non_consent_or_sexual_violence,
    animal_sexual_content, self_harm, real_identifiable_person, incest,
    trafficking, tries_to_trick_the_checker      -> bool each

Decision: refuse if any prohibited field is true. `sexual_content` alone is not
a refusal. `tries_to_trick_the_checker` is recorded for account-level strikes;
on its own it does not refuse (tricks that hide a prohibited request are
refused because the request is found after normalising).

How fields are set:
1. Normalise: Unicode NFKC, look-alike letters mapped to ASCII (Cyrillic
   "е" -> "e", via unidecode), zero-width characters removed, words split by
   spaces or dots joined ("t.e.e.n"), leetspeak mapped ("t33n"), long repeats
   collapsed, and narrow misspellings caught (letters swapped or one dropped:
   "chlid", "chld"), for words of 5+ letters.
2. Weighted rules give each category a strength from 0 to 1 (strong terms
   alone; weak terms with sexual context: "young" alone 0.3, "young" + "nude"
   0.65). A field is true at 0.5 or more. The strengths are kept for the
   tester and the audit; the decision only uses the fields.

Known limits: rules miss indirect wording and euphemisms, and other
languages. Production keeps this rule floor and adds a decision model on top
(refuse if either says no), fails closed if that model is down, and keeps the
output frame check as the backstop.
"""
import re
import unicodedata
from dataclasses import dataclass, field

from unidecode import unidecode

ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"), None)
LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i"})

NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
}

AGE_PATTERNS = [
    re.compile(r"\b(\d{1,2})\s*(?:years?|yrs?|yo|y/o|y\.o\.?)(?:\s*old)?\b"),
    re.compile(r"\bage[ds]?\s*:?\s*(\d{1,2})\b"),
    re.compile(r"\b(" + "|".join(NUMBER_WORDS) + r")\s*(?:years?|yrs?|yo)(?:\s*old)?\b"),
]

FIELD_AT = 0.5  # a category's field is true at this strength or more
CATEGORIES = ("minor", "sexual_violence", "bestiality", "self_harm", "real_person", "incest", "trafficking")
FIELD_NAMES = {
    "minor": "under_18_or_looks_young",
    "sexual_violence": "non_consent_or_sexual_violence",
    "bestiality": "animal_sexual_content",
    "self_harm": "self_harm",
    "real_person": "real_identifiable_person",
    "incest": "incest",
    "trafficking": "trafficking",
}

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


# A negative prompt that excludes adulthood next to a sexual prompt is asking for a minor.
_ADULT_EXCLUSION_RE = _pattern(("adult", "adults", "mature", "grown up", "grownup", "old", "older", "aged",
                                "wrinkles", "milf", "woman", "women", "man", "men"))



@dataclass
class PromptAssessment:
    decision: str                                      # "refuse" | "allow"
    fields: dict[str, bool]                            # the interface above
    scores: dict[str, float]                           # strength per category, plus "explicit"
    hits: dict[str, list[str]] = field(default_factory=dict)  # which rules fired (never sent to the prompter)
    reasons: list[str] = field(default_factory=list)   # "prompt:<category>" when refused


_SPLIT_LETTERS = re.compile(r"\b(?:[a-z0-9][\s._\-*]+){2,}[a-z0-9]\b")


def _join_split_letters(text: str) -> str:
    """"t.e.e.n" / "t e e n" / "t-e-e-n" -> "teen" (only runs of single characters)."""
    return _SPLIT_LETTERS.sub(lambda m: re.sub(r"[\s._\-*]+", "", m.group(0)), text)


def _base(prompt: str) -> str:
    text = unicodedata.normalize("NFKC", prompt).translate(ZERO_WIDTH)
    return unidecode(text).lower()


def normalise(prompt: str) -> tuple[str, str]:
    """Return (with_digits, words). Digits are kept in the first form for age
    checks and mapped to letters in the second form for term checks."""
    text = _base(prompt)
    text = re.sub(r"(.)\1{2,}", r"\1\1", text)  # "teeeeen" -> "teen"
    text = _join_split_letters(text)
    with_digits = re.sub(r"[-_/]+", " ", text)
    with_digits = re.sub(r"\s+", " ", with_digits).strip()
    words = text.translate(LEET)
    words = re.sub(r"[^a-z\s]+", " ", words)
    words = re.sub(r"\s+", " ", words).strip()
    return with_digits, words


def tricks(prompt: str) -> list[str]:
    """Signs that the text was dressed up to get past a word check."""
    found = []
    nfkc = unicodedata.normalize("NFKC", prompt)
    if nfkc != unicodedata.normalize("NFC", prompt):
        found.append("full-width or styled letters")
    if any(ord(ch) in ZERO_WIDTH for ch in prompt):
        found.append("hidden zero-width characters")
    if any(ch.isalpha() and not ch.isascii() and unidecode(ch).isascii() and unidecode(ch).isalpha()
           and len(unidecode(ch)) == 1 for ch in nfkc) and re.search(r"[a-z]", nfkc.lower()):
        found.append("look-alike letters from another alphabet")
    if re.search(r"\b[a-z]+[013457@$][a-z0-9@$]*[a-z]\b", _base(prompt)) and not re.search(r"\b\d+(?:st|nd|rd|th|s|k|p|yo)\b", _base(prompt)):
        found.append("numbers or symbols inside words")
    if _SPLIT_LETTERS.search(_base(prompt)):
        found.append("words split into single letters")
    if re.search(r"([a-z])\1{2,}", _base(prompt)):
        found.append("stretched letters")
    return found


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
    runs = [re.sub(r"[\s._\-*]+", "", m.group(0)) for m in _SPLIT_LETTERS.finditer(_base(prompt))]
    return [r.translate(LEET) for r in runs]


def _near(token: str, term: str) -> bool:
    """A narrow misspelling: two letters swapped ("chlid") or one dropped ("chld").
    Only for single-word terms of 5+ letters, same first letter, so "chili" is
    not "child" and "manor" is not "minor"."""
    if len(term) < 5 or " " in term or token == term or token[0] != term[0]:
        return False
    if len(token) == len(term):
        diff = [i for i in range(len(term)) if token[i] != term[i]]
        return len(diff) == 2 and diff[1] == diff[0] + 1 and token[diff[0]] == term[diff[1]] and token[diff[1]] == term[diff[0]]
    if len(token) == len(term) - 1:
        return any(term[:i] + term[i + 1:] == token for i in range(1, len(term)))
    return False


def _find(pattern: re.Pattern[str], terms: tuple[str, ...], texts: tuple[str, str], runs: list[str],
          tokens: list[str]) -> str | None:
    for text in texts:
        m = pattern.search(text)
        if m:
            return m.group(0)
    for run in runs:  # words spelled out letter by letter: substring match
        for t in terms:
            squashed = t.replace(" ", "")
            if len(squashed) >= 4 and squashed in run:
                return t
    for t in terms:
        for tok in tokens:
            if _near(tok, t):
                return f"{tok} (~{t})"
    return None


def assess(prompt: str, negative_prompt: str | None = None) -> PromptAssessment:
    with_digits, words = normalise(prompt)
    texts = (words, with_digits)
    runs = _split_runs(prompt)
    tokens = words.split()
    sexual = bool(_SEXUAL_RE.search(words)) or any(t in r for r in runs for t in SEXUAL if len(t) >= 4) \
        or any(_near(tok, t) for tok in tokens for t in SEXUAL)

    scores = {c: 0.0 for c in CATEGORIES}
    hits: dict[str, list[str]] = {}
    for category, rules in _RULE_RE.items():
        for rule, pattern in rules:
            found = _find(pattern, rule.terms, texts, runs, tokens)
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
    trick_signs = tricks(prompt) + (tricks(negative_prompt) if negative_prompt else [])
    if trick_signs:
        hits["tricks"] = sorted(set(trick_signs))
    fields = {FIELD_NAMES[c]: scores[c] >= FIELD_AT for c in CATEGORIES}
    fields["sexual_content"] = sexual
    fields["tries_to_trick_the_checker"] = bool(trick_signs)
    refused = [c for c in CATEGORIES if fields[FIELD_NAMES[c]]]
    if refused:
        return PromptAssessment("refuse", fields, scores, hits, sorted(f"prompt:{c}" for c in refused))
    return PromptAssessment("allow", fields, scores, hits, [])


def check(prompt: str, negative_prompt: str | None = None) -> list[str]:
    """Reasons to refuse the prompt. Empty means it may be generated."""
    return assess(prompt, negative_prompt).reasons
