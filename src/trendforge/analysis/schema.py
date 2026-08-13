from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class VariationIdea(BaseModel):
    concept: str
    character: Optional[str] = None
    scenario: Optional[str] = None
    hook: Optional[str] = None
    production_method: Optional[str] = None
    notes: Optional[str] = None


class FormatAnalysis(BaseModel):
    """Structured LLM output. Must NOT include overall_score or commercial status."""

    what_happens: str
    first_second_hook: str
    why_stop_scrolling: str
    attention_mechanic: str
    format_name: str
    format_key: str = Field(
        description="Stable slug for format family matching, e.g. unexpected-character-performance"
    )
    format_category: Optional[str] = None
    format_description: str
    hook_pattern: str
    why_it_works: str
    variables: list[str] = Field(default_factory=list)
    estimated_variation_count: int = Field(ge=0, le=10000)
    novelty_signal: float = Field(ge=0, le=100)
    replicability_signal: float = Field(ge=0, le=100)
    saturation_signal: float = Field(ge=0, le=100)
    trend_velocity_signal: float = Field(ge=0, le=100)
    cross_platform_signal: float = Field(ge=0, le=100)
    hook_strength_signal: float = Field(ge=0, le=100)
    production_complexity_signal: float = Field(ge=0, le=100)
    comfy_feasibility_signal: float = Field(ge=0, le=100)
    ip_risk_signal: float = Field(ge=0, le=100)
    ip_notes: str = ""
    recommended_production_method: str
    production_notes: str = ""
    estimated_generation_cost_usd: Optional[float] = None
    original_variations: list[VariationIdea] = Field(default_factory=list)

    def to_score_signals(self) -> dict[str, Any]:
        return {
            "trend_velocity": self.trend_velocity_signal,
            "novelty": self.novelty_signal,
            "replicability": self.replicability_signal,
            "variation_density": min(100.0, self.estimated_variation_count * 5.0)
            if self.estimated_variation_count <= 20
            else 100.0,
            "cross_platform": self.cross_platform_signal,
            "production_complexity": self.production_complexity_signal,
            "hook_strength": self.hook_strength_signal,
            "ip_risk": self.ip_risk_signal,
            "saturation": self.saturation_signal,
            "comfy_feasibility": self.comfy_feasibility_signal,
            "estimated_cost": self.estimated_generation_cost_usd,
        }
