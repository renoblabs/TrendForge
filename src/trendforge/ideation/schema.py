from __future__ import annotations

from pydantic import BaseModel, Field


class FormatIdea(BaseModel):
    idea_title: str
    hook: str
    premise: str
    format_family: str = ""
    primary_mechanic: str = ""
    variation_axis: str = ""
    why_it_could_work: str = ""
    production_notes: str = ""
    ai_leverage: int = Field(default=3, ge=1, le=5)
    production_complexity: int = Field(default=3, ge=1, le=5)
    variation_potential: int = Field(default=3, ge=1, le=5)
    originality_risk: str = "MEDIUM"


class IdeationPayload(BaseModel):
    ideas: list[FormatIdea] = Field(default_factory=list)
