"""Request and response shapes. The media input is tolerant: extra fields are
accepted, and `type` or `kind` both work."""
from typing import Any, Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


SOURCE_ALIASES = {"upload": "upload", "uploaded": "upload", "generation": "generated", "generated": "generated"}


class FrameIn(BaseModel):
    """One frame's scores. The task's format is flat: {"explicit": 0.95, "self_harm": 0.1}.
    The older nested form {"scores": {...}} is accepted too. A label left out scores 0;
    keys that are not numbers (for example an "index") are ignored."""

    model_config = ConfigDict(extra="ignore")
    scores: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _flat_or_nested(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            raise ValueError("each frame must be an object of label scores")
        if isinstance(value.get("scores"), dict):
            raw = value["scores"]
        else:
            raw = {k: v for k, v in value.items()
                   if isinstance(v, (int, float)) and not isinstance(v, bool)}
        for label, score in raw.items():
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0.0 <= score <= 1.0:
                raise ValueError(f"score for {label!r} must be a number from 0 to 1")
        return {"scores": raw}


class MediaIn(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    type: Literal["image", "video"] = Field(validation_alias=AliasChoices("type", "kind"))
    # Stored as "generated"; the task's "generation" is the main spelling on input.
    source: Literal["upload", "generated"] = "upload"
    prompt: str | None = None
    negative_prompt: str | None = None  # also checked: excluding "adult" next to a sexual prompt asks for a minor
    frames: list[FrameIn] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _source_alias(cls, value: Any) -> Any:
        if isinstance(value, dict) and isinstance(value.get("source"), str):
            source = SOURCE_ALIASES.get(value["source"].strip().lower())
            if source is None:
                raise ValueError("source must be 'upload' or 'generation'")
            value = {**value, "source": source}
        return value

    @model_validator(mode="after")
    def _shape_rules(self) -> "MediaIn":
        if self.type == "image" and len(self.frames) != 1:
            raise ValueError("an image must have exactly 1 frame")
        if self.source == "generated" and not (self.prompt and self.prompt.strip()):
            raise ValueError("generated media must include the prompt it was made from")
        return self


class MediaAccepted(BaseModel):
    id: UUID
    status: Literal["pending"] = "pending"


class MediaOut(BaseModel):
    id: UUID
    status: str
    reasons: list[str]


class ReviewItem(BaseModel):
    id: UUID
    status: str
    reasons: list[str]
    created_at: Any
    held_reason_kind: str | None


class Refused(BaseModel):
    reasons: list[str]


class PromptCheckIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    negative_prompt: str | None = Field(default=None, max_length=4000)
