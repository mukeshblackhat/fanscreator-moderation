"""Request and response shapes. The media input is tolerant: extra fields are
accepted, and `type` or `kind` both work."""
from typing import Any, Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class FrameIn(BaseModel):
    model_config = ConfigDict(extra="allow")
    scores: dict[str, float] = Field(default_factory=dict)


class MediaIn(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    type: Literal["image", "video"] = Field(validation_alias=AliasChoices("type", "kind"))
    source: Literal["upload", "generated"] = "upload"
    prompt: str | None = None
    frames: list[FrameIn] = Field(min_length=1)

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
