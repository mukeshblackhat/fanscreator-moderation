"""The moderation policy. One pure function, no I/O, so every boundary can be tested.

Checked across every frame; the first rule that matches wins:
1. any prohibited label >= 0.8 on any frame  -> blocked (every workspace)
2. explicit >= 0.8 and the workspace is not
   verified                                   -> blocked
3. any prohibited label in [0.5, 0.8)         -> held for a human
4. otherwise                                  -> published (is_explicit when explicit >= 0.8)

Blocked beats held, and held beats published. Thresholds are inclusive at the
low end: 0.8 itself blocks and 0.5 itself holds. Explicit content alone never
holds an item: it is published in a verified workspace and blocked in an
unverified one.
"""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

PROHIBITED = ("minor", "sexual_violence", "bestiality", "self_harm", "real_person")
BLOCK_AT = 0.8
HOLD_AT = 0.5
EXPLICIT_AT = 0.8
POLICY_VERSION = "v2"


@dataclass(frozen=True)
class Decision:
    status: Literal["published", "held", "blocked"]
    reasons: list[str] = field(default_factory=list)
    is_explicit: bool = False


def decide(frames: Sequence[Mapping[str, float]], verified: bool) -> Decision:
    """`frames` is one score mapping per frame, in frame order. A missing label scores 0."""
    blocks: list[str] = []
    holds: list[str] = []
    explicit_frames: list[int] = []
    for i, scores in enumerate(frames):  # every frame, never a prefix
        for label in PROHIBITED:
            s = scores.get(label, 0.0)
            if s >= BLOCK_AT:
                blocks.append(f"prohibited:{label}@frame={i}")
            elif s >= HOLD_AT:
                holds.append(f"review:{label}@frame={i}")
        if scores.get("explicit", 0.0) >= EXPLICIT_AT:
            explicit_frames.append(i)
    explicit = bool(explicit_frames)
    if blocks:
        return Decision("blocked", blocks, is_explicit=explicit)
    if explicit and not verified:
        return Decision("blocked", ["explicit_unverified"], is_explicit=True)
    if holds:
        return Decision("held", holds, is_explicit=explicit)
    return Decision("published", [], is_explicit=explicit)
