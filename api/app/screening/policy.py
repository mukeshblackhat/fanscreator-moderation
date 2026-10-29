"""The moderation policy. One pure function, no I/O, so every boundary can be tested.

Precedence:
1. any prohibited label >= 0.8 on any frame  -> blocked
2. any prohibited label in [0.5, 0.8), or a
   generation prompt in the review band       -> held for a human
3. explicit >= 0.8 in an unverified workspace -> blocked
4. otherwise                                  -> published (is_explicit when explicit >= 0.8)
"""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

PROHIBITED = ("minor", "sexual_violence", "bestiality", "self_harm", "real_person")
BLOCK_AT = 0.8
HOLD_AT = 0.5
EXPLICIT_AT = 0.8
POLICY_VERSION = "v1"


@dataclass(frozen=True)
class Decision:
    status: Literal["published", "held", "blocked"]
    reasons: list[str] = field(default_factory=list)
    is_explicit: bool = False


def decide(frames: Sequence[Mapping[str, float]], verified: bool,
           prompt_reviews: Sequence[str] = ()) -> Decision:
    """`frames` is one score mapping per frame, in frame order. A missing label scores 0.
    `prompt_reviews` are "review:prompt_<category>" reasons from the prompt engine's
    review band: they hold otherwise-clean output for a human, like a 0.5 frame score."""
    blocks: list[str] = []
    holds: list[str] = list(prompt_reviews)
    explicit = False
    for i, scores in enumerate(frames):  # every frame, never a prefix
        for label in PROHIBITED:
            s = scores.get(label, 0.0)
            if s >= BLOCK_AT:
                blocks.append(f"prohibited:{label}@frame={i}")
            elif s >= HOLD_AT:
                holds.append(f"review:{label}@frame={i}")
        explicit |= scores.get("explicit", 0.0) >= EXPLICIT_AT
    if blocks:
        return Decision("blocked", blocks, is_explicit=explicit)
    if holds:
        return Decision("held", holds, is_explicit=explicit)
    if explicit and not verified:
        return Decision("blocked", ["explicit_unverified"], is_explicit=True)
    return Decision("published", [], is_explicit=explicit)
