from __future__ import annotations

from pydantic import BaseModel, Field


class StoryBeat(BaseModel):
    start_seconds: float = 0
    end_seconds: float = 0
    purpose: str = ""
    action: str = ""
    dialogue_or_narration: str = ""
    visual_requirement: str = ""
    audio_requirement: str = ""


class Shot(BaseModel):
    shot_number: int = 1
    duration_seconds: float = 0
    start_seconds: float = 0
    end_seconds: float = 0
    purpose: str = ""
    camera: str = ""
    framing: str = ""
    subject: str = ""
    action: str = ""
    environment: str = ""
    continuity_notes: str = ""
    dialogue: str = ""
    audio: str = ""
    generation_notes: str = ""


class Character(BaseModel):
    name: str
    role: str = ""
    appearance: str = ""
    behavior: str = ""
    visual_constraints: str = ""
    continuity_constraints: str = ""


class DialogueLine(BaseModel):
    speaker: str = ""
    start_seconds: float = 0
    text: str = ""


class ComfyRecommendation(BaseModel):
    technique: str
    priority: str = "MEDIUM"
    reason: str = ""
    required_inputs: list[str] = Field(default_factory=list)


class RequiredAsset(BaseModel):
    asset_type: str
    description: str = ""
    mandatory: bool = True
    source: str = "generate"
    notes: str = ""


class QAItem(BaseModel):
    check: str
    required: bool = True


class ProductionSpecDocument(BaseModel):
    title: str
    format_family: str
    specific_format: str = ""
    primary_mechanic: str = ""
    secondary_mechanics: list[str] = Field(default_factory=list)
    format_hypothesis: str = ""
    hook: str
    premise: str
    target_audience: str = ""
    duration_seconds: float = 20
    aspect_ratio: str = "9:16"
    story_beats: list[StoryBeat] = Field(default_factory=list)
    shots: list[Shot] = Field(default_factory=list)
    characters: list[Character] = Field(default_factory=list)
    environment: str = ""
    props: list[str] = Field(default_factory=list)
    dialogue: list[DialogueLine] = Field(default_factory=list)
    narration: str = ""
    audio_direction: str = ""
    sound_effects: list[str] = Field(default_factory=list)
    music: str = ""
    audio_priority: str = ""
    visual_style: str = ""
    camera_direction: str = ""
    continuity_requirements: list[str] = Field(default_factory=list)
    generation_requirements: list[str] = Field(default_factory=list)
    negative_constraints: list[str] = Field(default_factory=list)
    production_complexity: int = Field(default=3, ge=1, le=5)
    complexity_reasons: list[str] = Field(default_factory=list)
    required_assets: list[RequiredAsset] = Field(default_factory=list)
    qa_checklist: list[QAItem] = Field(default_factory=list)
    originality_notes: str = ""
    ip_considerations: str = ""
    comfyui_recommendations: list[ComfyRecommendation] = Field(default_factory=list)
