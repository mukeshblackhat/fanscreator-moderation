"""Frame scorers.

primary   a local open-source image classifier (default Falconsai/nsfw_image_detection,
          a ViT fine-tuned for normal vs nsfw). Runs on CPU, files never leave the machine.
          Covers: explicit.
secondary OpenAI omni-moderation-latest, only when OPENAI_API_KEY is set.
          On images it covers sexual, violence and self-harm. Mapped to:
            explicit        = max(local nsfw, openai sexual)
            self_harm       = max(self-harm, self-harm/intent, self-harm/instructions)
            sexual_violence = min(sexual, max(violence, violence/graphic))   (heuristic)
stub      REAL_MODEL=stub, for tests: a mostly red image scores explicit 0.95,
          anything else 0.02. Deterministic, no downloads.

Never scored on real files here: minor, real_person, bestiality. They come back
as 0. Detecting age or identity needs specialised models, and the minor category
must never be tested with real images.
"""
import asyncio
import base64
import io
import os
from typing import Protocol

import httpx
from PIL import Image

LABELS = ("minor", "sexual_violence", "bestiality", "self_harm", "real_person", "explicit")
MODEL_ID = os.environ.get("REAL_MODEL", "Falconsai/nsfw_image_detection")
OPENAI_URL = os.environ.get("OPENAI_MODERATION_URL", "https://api.openai.com/v1/moderations")
OPENAI_MODEL = "omni-moderation-latest"


class ScoringError(RuntimeError):
    pass


class Primary(Protocol):
    name: str

    def explicit(self, images: list[Image.Image]) -> list[float]: ...


class StubPrimary:
    name = "stub (tests only)"

    def explicit(self, images: list[Image.Image]) -> list[float]:
        out = []
        for img in images:
            r, g, b = img.convert("RGB").resize((8, 8)).getpixel((4, 4))
            out.append(0.95 if r > 180 and g < 80 and b < 80 else 0.02)
        return out


class LocalPrimary:
    def __init__(self, model_id: str) -> None:
        from transformers import pipeline  # heavy import, only in the real image

        self.name = model_id
        self._clf = pipeline("image-classification", model=model_id, device=-1)

    def explicit(self, images: list[Image.Image]) -> list[float]:
        results = self._clf(images, top_k=None, batch_size=8)
        scores = []
        for per_image in results:
            by_label = {r["label"].lower(): float(r["score"]) for r in per_image}
            scores.append(by_label.get("nsfw", 0.0))
        return scores


def load_primary() -> Primary:
    if MODEL_ID == "stub":
        return StubPrimary()
    return LocalPrimary(MODEL_ID)


def openai_enabled() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def _data_url(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


async def openai_scores(client: httpx.AsyncClient, img: Image.Image) -> dict[str, float]:
    """One frame through omni-moderation. Retries 429 and 5xx; anything else raises,
    and the caller fails the whole job closed (no partial answers)."""
    payload = {"model": OPENAI_MODEL, "input": [{"type": "image_url", "image_url": {"url": _data_url(img)}}]}
    headers = {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"}
    for attempt in range(3):
        try:
            r = await client.post(OPENAI_URL, json=payload, headers=headers, timeout=30)
        except httpx.HTTPError as exc:
            err = f"openai unreachable: {type(exc).__name__}"
        else:
            if r.status_code == 200:
                s = r.json()["results"][0]["category_scores"]
                g = lambda k: float(s.get(k, 0.0) or 0.0)  # noqa: E731
                violence = max(g("violence"), g("violence/graphic"))
                return {
                    "explicit": g("sexual"),
                    "self_harm": max(g("self-harm"), g("self-harm/intent"), g("self-harm/instructions")),
                    "sexual_violence": min(g("sexual"), violence),
                }
            err = f"openai HTTP {r.status_code}: {r.text[:200]}"
            if r.status_code not in (429, 500, 502, 503, 504):
                raise ScoringError(err)
        await asyncio.sleep(1.5 * (attempt + 1))
    raise ScoringError(err)


def combine(local_explicit: float, secondary: dict[str, float] | None) -> dict[str, float]:
    scores = {label: 0.0 for label in LABELS}
    scores["explicit"] = local_explicit
    if secondary:
        scores["explicit"] = max(scores["explicit"], secondary["explicit"])
        scores["self_harm"] = secondary["self_harm"]
        scores["sexual_violence"] = secondary["sexual_violence"]
    return {k: round(min(1.0, max(0.0, v)), 4) for k, v in scores.items()}
