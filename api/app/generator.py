"""Stand-in for the AI image/video generator.

There is no real model in the test task: the media JSON already carries the
frames the generator "produced". What matters is whether we called it at all,
so every call is counted and logged. A refused prompt must leave this at zero.
"""
from collections import Counter
from typing import Any

from app.logging import log

calls: Counter[str] = Counter()


def generate(prompt: str, frames: list[dict[str, Any]], *, workspace_id: str) -> list[dict[str, Any]]:
    calls[workspace_id] += 1
    log.info("generator_called", workspace_id=workspace_id, prompt_chars=len(prompt), frames=len(frames))
    return frames


def total_calls() -> int:
    return sum(calls.values())
