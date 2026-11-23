"""Frame scorers: a cascade, cheapest first. Every frame gets all six labels and a
`checked_by` entry per label naming what set it. No label is ever a silent 0.

tier 1  local, CPU, every frame, $0. Two pinned open models, baked into the image:
          Falconsai/nsfw_image_detection   ViT normal-vs-nsfw  -> explicit cue
          openai/clip-vit-base-patch32     zero-shot prompts   -> person, minor, animal,
                                                                  self_harm, violence cues
        Each CLIP cue is the softmax mass on its "present" prompts against a
        set of ordinary "absent" scenes, so a cue fires only when it beats every
        everyday explanation of the frame.
tier 2  OpenAI omni-moderation-latest, free, only when OPENAI_API_KEY is set, and only
        for frames tier 1 does not call clearly safe (`needs_tier2`). On images it scores
        sexual, violence and self-harm.
tier 3  premium identity / age detector (AWS Rekognition RecognizeCelebrities, Hive).
        Not wired: no keys. Its slot is a human: what only tier 3 could clear is held.
stub    REAL_MODEL=stub, for tests: cues come from the image's colour (STUB_CUES).
        Deterministic, no downloads.

How the labels are set (combine):
  explicit         max(nsfw, openai sexual)
  self_harm        max(clip cue, openai). Without openai, a cue in [LOW, HOLD) is held.
  sexual_violence  max(min(clip violence, explicit), openai min(sexual, violence)).
                   Without openai, a combined cue in [LOW, HOLD) is held.
  minor            min(clip minor, clip person): an age cue only means something on a
                   person. Held when a person is in a sexual frame (explicit >= HOLD) and
                   the minor cue is not clearly low (>= LOW): age is what CLIP is worst at,
                   and that is the one place a miss is unacceptable.
  bestiality       min(clip animal, explicit). Held when both are at least LOW.
  real_person      nothing here can tell who someone is. A person in the frame (person
                   cue >= PERSON_AT) is held for a human: "cannot check" is never "safe".
                   With no person there is nobody to identify, so the person cue itself is
                   the score. Narrowing this to sexual frames only would be the obvious
                   cost saving (non-consensual imagery is sexual), but a face swap in a
                   clothed frame is still a real person used without consent, so it is
                   not done without a tier-3 detector.

Never scored on real files: the minor category must never be tested with real images.
"""
import asyncio
import base64
import io
import os
from typing import Protocol

import httpx
from PIL import Image

LABELS = ("minor", "sexual_violence", "bestiality", "self_harm", "real_person", "explicit")
CUES = ("explicit", "person", "minor", "animal", "self_harm", "violence")
MODEL_ID = os.environ.get("REAL_MODEL", "real")  # "stub" for tests
NSFW_MODEL = ("Falconsai/nsfw_image_detection", "96cb0d0342c7afb80cab76ecc58b265fa44da256")
CLIP_MODEL = ("openai/clip-vit-base-patch32", "3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268")
OPENAI_URL = os.environ.get("OPENAI_MODERATION_URL", "https://api.openai.com/v1/moderations")
OPENAI_MODEL = "omni-moderation-latest"

HOLD = 0.5            # policy: [0.5, 0.8) holds for a human, 0.8 blocks
LOW = 0.3             # a CLIP cue under this is clearly absent; at or above it is uncertain
EXPLICIT_GATE = 0.2   # nsfw under this skips tier 2: the ViT sits near 0.0 on ordinary frames
PERSON_AT = 0.5       # person cue at or above this: someone is in the frame
DEDUPE_BITS = 2       # 64-bit average hash distance at which a frame reuses the last tier-2 answer

_EVERYDAY = ["a photo of people playing sports", "a photo of a person posing for a photo",
             "a portrait photo of a person", "a photo of a group of people", "a photo of a person in a bedroom",
             "a photo of a street with cars", "a photo of a room", "a photo of food", "a photo of a landscape",
             "a photo of a pet", "a photo of a stuffed toy"]
PROMPTS = {  # cue -> (present prompts, absent prompts), each prompt its own class
    "person": (["a photo of a person", "a photo of people", "a photo of a man", "a photo of a woman",
                "a close-up photo of a human body"],
               ["a photo of an empty room", "a photo of a landscape", "a photo of an object", "a photo of an animal",
                "a photo of food", "a photo of a street with no people", "a photo of a stuffed toy"]),
    "minor": (["a photo of a child", "a photo of a young kid", "a photo of a teenager",
               "a photo of a student in a school uniform"],
              ["a photo of an adult", "a photo of an adult woman", "a photo of an adult man",
               "a photo of a young woman in her twenties", "a photo of a young man in his twenties"]),
    "animal": (["a photo of an animal", "a photo of a dog", "a photo of a cat", "a photo of a horse",
                "a photo of a farm animal"],
               ["a photo of a person", "a photo of people", "a photo of people playing sports", "a photo of a room",
                "a photo of a landscape", "a photo of an object", "a photo of a street", "a photo of a stuffed toy"]),
    "self_harm": (["a photo of cuts and scars on a wrist", "a photo of self-harm wounds on skin",
                   "a photo of a bleeding wound", "a photo of blood on skin"],
                  ["a photo of healthy skin", *_EVERYDAY]),
    "violence": (["a photo of a person tied up and restrained", "a photo of a person being attacked",
                  "a photo of a violent assault", "a photo of a person being choked"],
                 ["a photo of people hugging", *_EVERYDAY]),
}

# stub: nearest colour wins (JPEG shifts colours a little). Unlisted colours read as clear-safe.
_SAFE = {c: 0.02 for c in CUES}
STUB_CUES = {
    (20, 40, 200): _SAFE,                                                       # blue: nothing
    (230, 20, 20): {**_SAFE, "explicit": 0.95},                                 # red: explicit, no person
    (230, 230, 20): {**_SAFE, "person": 0.9},                                   # yellow: clothed adult
    (230, 20, 230): {**_SAFE, "explicit": 0.95, "person": 0.9, "minor": 0.35},  # magenta: sexual, age unclear
    (240, 240, 240): {**_SAFE, "person": 0.9, "minor": 0.95},                   # white: strong minor cue
    (240, 140, 20): {**_SAFE, "explicit": 0.4, "animal": 0.9},                  # orange: animal, some sexual
    (20, 200, 20): {**_SAFE, "self_harm": 0.35},                                # green: uncertain self-harm
}


class ScoringError(RuntimeError):
    pass


class Tier1(Protocol):
    name: str

    def cues(self, images: list[Image.Image]) -> list[dict[str, float]]: ...


class StubTier1:
    name = "stub (tests only)"

    def cues(self, images: list[Image.Image]) -> list[dict[str, float]]:
        out = []
        for img in images:
            px = img.convert("RGB").resize((8, 8)).getpixel((4, 4))
            near = min(STUB_CUES, key=lambda c: sum((a - b) ** 2 for a, b in zip(c, px)))
            out.append(dict(STUB_CUES[near]))
        return out


class LocalTier1:
    def __init__(self) -> None:
        import torch  # heavy imports, only in the real image
        from transformers import CLIPModel, CLIPProcessor, pipeline

        self.name = f"{NSFW_MODEL[0]} + {CLIP_MODEL[0]}"
        self._torch = torch
        self._nsfw = pipeline("image-classification", model=NSFW_MODEL[0], revision=NSFW_MODEL[1], device=-1)
        self._clip = CLIPModel.from_pretrained(CLIP_MODEL[0], revision=CLIP_MODEL[1]).eval()
        self._proc = CLIPProcessor.from_pretrained(CLIP_MODEL[0], revision=CLIP_MODEL[1])
        with torch.no_grad():  # per cue: the "present" prompts first, then the "absent" ones
            self._text_emb = {cue: torch.cat([self._text(present), self._text(absent)])
                              for cue, (present, absent) in PROMPTS.items()}

    def _text(self, prompts: list[str]):
        tok = self._proc(text=prompts, return_tensors="pt", padding=True)
        e = _tensor(self._clip.get_text_features(**tok))
        return e / e.norm(dim=-1, keepdim=True)

    def cues(self, images: list[Image.Image]) -> list[dict[str, float]]:
        nsfw = self._nsfw(images, top_k=None, batch_size=8)
        out = []
        with self._torch.no_grad():
            for start in range(0, len(images), 16):
                px = self._proc(images=images[start:start + 16], return_tensors="pt")
                e = _tensor(self._clip.get_image_features(**px))
                e = e / e.norm(dim=-1, keepdim=True)
                p = {cue: (100.0 * e @ t.T).softmax(-1)[:, :len(PROMPTS[cue][0])].sum(-1).tolist()
                     for cue, t in self._text_emb.items()}  # 100 = CLIP's own logit scale
                out += [{cue: p[cue][i] for cue in PROMPTS} for i in range(len(e))]
        for c, per_image in zip(out, nsfw):
            c["explicit"] = next((float(r["score"]) for r in per_image if r["label"].lower() == "nsfw"), 0.0)
        return out


def _tensor(x):
    """transformers 5 returns a model output where 4.x returned the tensor."""
    return x if hasattr(x, "norm") else x.pooler_output


def load_tier1() -> Tier1:
    return StubTier1() if MODEL_ID == "stub" else LocalTier1()


def openai_enabled() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def needs_tier2(c: dict[str, float]) -> bool:
    """False only when tier 1 calls the frame clearly safe on what tier 2 can change.
    Tier 2 scores sexual and self-harm directly, so those gate low. Minor, animal and
    violence gate at HOLD: they only matter if tier 2 finds a sexual signal the ViT
    missed, and gating them at LOW sent most ordinary person and pet photos to OpenAI."""
    return (c["explicit"] >= EXPLICIT_GATE or c["self_harm"] >= LOW
            or max(c["violence"], c["animal"], min(c["minor"], c["person"])) >= HOLD)


def ahash(img: Image.Image) -> int:
    px = img.convert("L").resize((8, 8)).tobytes()
    mean = sum(px) / 64
    return sum(1 << i for i, p in enumerate(px) if p > mean)


def near_duplicate(a: int, b: int) -> bool:
    return (a ^ b).bit_count() <= DEDUPE_BITS


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


def combine(c: dict[str, float], t2: dict[str, float] | None) -> tuple[dict[str, float], dict[str, str]]:
    """Tier-1 cues plus the tier-2 answer (None when not asked) -> (scores, checked_by)."""
    s: dict[str, float] = {}
    by: dict[str, str] = {}
    s["explicit"] = max(c["explicit"], t2["explicit"]) if t2 else c["explicit"]
    by["explicit"] = "tier1:nsfw+tier2:openai" if t2 else "tier1:nsfw"

    for label, cue in (("self_harm", c["self_harm"]), ("sexual_violence", min(c["violence"], s["explicit"]))):
        if t2:
            s[label], by[label] = max(cue, t2[label]), "tier1:clip+tier2:openai"
        elif LOW <= cue < HOLD:
            s[label], by[label] = HOLD, "hold:uncertain_no_tier2"
        else:
            s[label], by[label] = cue, "tier1:clip"

    person = c["person"] >= PERSON_AT
    s["minor"], by["minor"] = min(c["minor"], c["person"]), "tier1:clip"
    if person and s["explicit"] >= HOLD and c["minor"] >= LOW and s["minor"] < HOLD:
        s["minor"], by["minor"] = HOLD, "hold:sexual_age_unclear"

    s["bestiality"], by["bestiality"] = min(c["animal"], s["explicit"]), "tier1:clip+nsfw"
    if c["animal"] >= LOW and s["explicit"] >= LOW and s["bestiality"] < HOLD:
        s["bestiality"], by["bestiality"] = HOLD, "hold:animal_and_sexual"

    if person:
        s["real_person"], by["real_person"] = HOLD, "hold:no_identity_detector"
    else:
        s["real_person"], by["real_person"] = c["person"], "tier1:clip_no_person"
    return {k: round(min(1.0, max(0.0, s[k])), 4) for k in LABELS}, by


def describe(tier2: bool) -> dict[str, str]:
    """Per-label detector, for GET /info."""
    t2 = " + OpenAI omni-moderation" if tier2 else ""
    return {
        "explicit": f"Falconsai nsfw ViT{t2}",
        "self_harm": f"CLIP cue{t2}" + ("" if tier2 else "; uncertain cue held for a human"),
        "sexual_violence": f"CLIP violence cue x explicit{t2}" + ("" if tier2 else "; uncertain cue held"),
        "minor": "CLIP age cue on a person; a sexual frame with a person and an unclear age is held",
        "bestiality": "CLIP animal cue x explicit; both moderately present is held",
        "real_person": "no identity detector (tier 3 not wired): any frame with a person is held",
    }


HOLD_RULES = [
    "real_person: person cue >= 0.5 -> 0.5 (hold:no_identity_detector)",
    "minor: person present, explicit >= 0.5, minor cue >= 0.3 -> at least 0.5 (hold:sexual_age_unclear)",
    "bestiality: animal cue >= 0.3 and explicit >= 0.3 -> at least 0.5 (hold:animal_and_sexual)",
    "self_harm, sexual_violence: no tier 2 and cue in [0.3, 0.5) -> 0.5 (hold:uncertain_no_tier2)",
    "tier 2 error after retries -> whole job fails closed (held by the API)",
]
