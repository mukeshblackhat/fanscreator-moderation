"""Generation prompt gate.

PLACEHOLDER until the fix commit: it refuses every prompt. That is the safe
default, because a gate that is not built yet must not let anything through.
The real normaliser and rules replace this in step 4.
"""


def check(prompt: str) -> list[str]:
    return ["prompt:gate_not_implemented"]
