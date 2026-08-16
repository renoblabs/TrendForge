from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from trendforge.analysis.mechanics import clamp_rating, normalize_mechanic, normalize_mechanics
from trendforge.production.spec_prompt import DEFAULT_ASPECT_RATIO, DEFAULT_DURATION_SECONDS
from trendforge.production.spec_schema import (
    Character,
    ComfyRecommendation,
    DialogueLine,
    ProductionSpecDocument,
    QAItem,
    RequiredAsset,
    Shot,
    StoryBeat,
)

_ASSET_SOURCES = {"generate", "user_provided", "stock", "library", "future_factory"}
_PRIORITIES = {"HIGH", "MEDIUM", "LOW"}


def _as_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, ProductionSpecDocument):
        return raw.model_dump()
    if not isinstance(raw, dict):
        raise ValueError("Production spec must be a JSON object")
    return dict(raw)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            text = str(item).strip()
            if text:
                out.append(text)
        return out
    return []


def _parse_beats(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    beats = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        start = _num(item.get("start_seconds"), 0)
        end = _num(item.get("end_seconds"), start)
        if end < start:
            start, end = end, start
        beats.append(
            {
                "start_seconds": start,
                "end_seconds": end,
                "purpose": str(item.get("purpose") or ""),
                "action": str(item.get("action") or ""),
                "dialogue_or_narration": str(
                    item.get("dialogue_or_narration") or item.get("dialogue") or ""
                ),
                "visual_requirement": str(item.get("visual_requirement") or ""),
                "audio_requirement": str(item.get("audio_requirement") or ""),
            }
        )
    return beats


def _parse_shots(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    shots = []
    for i, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        start = _num(item.get("start_seconds"), 0)
        end = _num(item.get("end_seconds"), start)
        duration = _num(item.get("duration_seconds"), max(0.0, end - start))
        if end <= start and duration > 0:
            end = start + duration
        if end < start:
            start, end = end, start
        duration = max(duration, max(0.0, end - start))
        shots.append(
            {
                "shot_number": int(_num(item.get("shot_number"), i)) or i,
                "duration_seconds": duration,
                "start_seconds": start,
                "end_seconds": end,
                "purpose": str(item.get("purpose") or ""),
                "camera": str(item.get("camera") or ""),
                "framing": str(item.get("framing") or ""),
                "subject": str(item.get("subject") or ""),
                "action": str(item.get("action") or ""),
                "environment": str(item.get("environment") or ""),
                "continuity_notes": str(item.get("continuity_notes") or ""),
                "dialogue": str(item.get("dialogue") or ""),
                "audio": str(item.get("audio") or ""),
                "generation_notes": str(item.get("generation_notes") or ""),
            }
        )
    return shots


def _parse_characters(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        out.append(
            {
                "name": name,
                "role": str(item.get("role") or ""),
                "appearance": str(item.get("appearance") or ""),
                "behavior": str(item.get("behavior") or ""),
                "visual_constraints": str(item.get("visual_constraints") or ""),
                "continuity_constraints": str(item.get("continuity_constraints") or ""),
            }
        )
    return out


def _parse_dialogue(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    lines = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("line") or "").strip()
        if not text:
            continue
        lines.append(
            {
                "speaker": str(item.get("speaker") or "POV"),
                "start_seconds": _num(item.get("start_seconds"), 0),
                "text": text,
            }
        )
    return lines


def _parse_assets(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    assets = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "generate").strip().lower().replace(" ", "_")
        if source not in _ASSET_SOURCES:
            source = "generate"
        assets.append(
            {
                "asset_type": str(item.get("asset_type") or item.get("type") or "reference"),
                "description": str(item.get("description") or ""),
                "mandatory": bool(item.get("mandatory", True)),
                "source": source,
                "notes": str(item.get("notes") or ""),
            }
        )
    return assets


def _parse_comfy(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    recs = []
    for item in raw:
        if isinstance(item, str):
            recs.append(
                {
                    "technique": item,
                    "priority": "MEDIUM",
                    "reason": "",
                    "required_inputs": [],
                }
            )
            continue
        if not isinstance(item, dict):
            continue
        priority = str(item.get("priority") or "MEDIUM").upper()
        if priority not in _PRIORITIES:
            priority = "MEDIUM"
        recs.append(
            {
                "technique": str(item.get("technique") or item.get("name") or ""),
                "priority": priority,
                "reason": str(item.get("reason") or ""),
                "required_inputs": _str_list(item.get("required_inputs")),
            }
        )
    return [r for r in recs if r["technique"]]


def _parse_qa(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    items = []
    for item in raw:
        if isinstance(item, str):
            items.append({"check": item, "required": True})
            continue
        if not isinstance(item, dict):
            continue
        check = str(item.get("check") or item.get("label") or item.get("id") or "").strip()
        if check:
            items.append({"check": check, "required": bool(item.get("required", True))})
    return items


def parse_production_spec(
    raw: Any,
    *,
    family_key: str,
    primary_mechanic: str,
    duration_seconds: float | None = None,
) -> ProductionSpecDocument:
    data = _as_dict(raw)
    data.pop("overall_score", None)
    data.pop("status", None)
    duration = _num(data.get("duration_seconds"), duration_seconds or DEFAULT_DURATION_SECONDS)
    if duration <= 0:
        duration = DEFAULT_DURATION_SECONDS
    hook = str(data.get("hook") or "")
    premise = str(data.get("premise") or "")
    title = str(data.get("title") or hook or "Untitled spec")
    payload = {
        "title": title,
        "format_family": str(data.get("format_family") or family_key),
        "specific_format": str(data.get("specific_format") or data.get("format_name") or family_key),
        "primary_mechanic": normalize_mechanic(data.get("primary_mechanic") or primary_mechanic),
        "secondary_mechanics": normalize_mechanics(data.get("secondary_mechanics") or []),
        "format_hypothesis": str(data.get("format_hypothesis") or ""),
        "hook": hook,
        "premise": premise,
        "target_audience": str(data.get("target_audience") or ""),
        "duration_seconds": duration,
        "aspect_ratio": str(data.get("aspect_ratio") or DEFAULT_ASPECT_RATIO),
        "story_beats": _parse_beats(data.get("story_beats") or data.get("beats")),
        "shots": _parse_shots(data.get("shots") or data.get("shot_list")),
        "characters": _parse_characters(data.get("characters") or data.get("subjects")),
        "environment": str(data.get("environment") or ""),
        "props": _str_list(data.get("props")),
        "dialogue": _parse_dialogue(data.get("dialogue")),
        "narration": str(data.get("narration") or ""),
        "audio_direction": str(data.get("audio_direction") or ""),
        "sound_effects": _str_list(data.get("sound_effects") or data.get("sfx")),
        "music": str(data.get("music") or ""),
        "audio_priority": str(data.get("audio_priority") or ""),
        "visual_style": str(data.get("visual_style") or ""),
        "camera_direction": str(data.get("camera_direction") or ""),
        "continuity_requirements": _str_list(data.get("continuity_requirements")),
        "generation_requirements": _str_list(data.get("generation_requirements")),
        "negative_constraints": _str_list(data.get("negative_constraints")),
        "production_complexity": clamp_rating(data.get("production_complexity")),
        "complexity_reasons": _str_list(data.get("complexity_reasons")),
        "required_assets": _parse_assets(data.get("required_assets")),
        "qa_checklist": _parse_qa(data.get("qa_checklist")),
        "originality_notes": str(data.get("originality_notes") or ""),
        "ip_considerations": str(data.get("ip_considerations") or ""),
        "comfyui_recommendations": _parse_comfy(data.get("comfyui_recommendations")),
    }
    try:
        return ProductionSpecDocument.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"Invalid production spec payload: {exc}") from exc
