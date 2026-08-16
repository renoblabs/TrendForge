from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from trendforge.analysis.mechanics import (
    clamp_rating,
    normalize_confidence,
    normalize_ip_dependency,
    normalize_mechanic,
    normalize_mechanics,
)
from trendforge.analysis.schema import FormatAnalysis


REQUIRED_FALLBACKS = {
    "what_happens": "",
    "first_second_hook": "",
    "why_stop_scrolling": "",
    "attention_mechanic": "",
    "format_name": "Unnamed format",
    "format_key": "unnamed-format",
    "format_description": "",
    "hook_pattern": "",
    "why_it_works": "",
    "estimated_variation_count": 0,
    "novelty_signal": 0,
    "replicability_signal": 0,
    "saturation_signal": 0,
    "trend_velocity_signal": 0,
    "cross_platform_signal": 0,
    "hook_strength_signal": 0,
    "production_complexity_signal": 0,
    "comfy_feasibility_signal": 0,
    "ip_risk_signal": 0,
    "recommended_production_method": "unknown",
}


def _as_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, FormatAnalysis):
        return raw.model_dump()
    if not isinstance(raw, dict):
        raise ValueError("LLM analysis must be a JSON object")
    return dict(raw)


def parse_format_analysis(raw: Any) -> FormatAnalysis:
    """Validate LLM JSON, fill missing optional fields, normalize taxonomy."""
    data = _as_dict(raw)
    if "overall_score" in data or "status" in data:
        data.pop("overall_score", None)
        data.pop("status", None)

    if not data.get("first_second_hook") and data.get("hook"):
        data["first_second_hook"] = data["hook"]
    if not data.get("what_happens") and data.get("surface_content"):
        data["what_happens"] = data["surface_content"]
    if not data.get("why_it_works") and data.get("reason_it_might_work"):
        data["why_it_works"] = data["reason_it_might_work"]
    if not data.get("format_key") and data.get("format_family"):
        data["format_key"] = str(data["format_family"])
    if not data.get("attention_mechanic") and data.get("primary_mechanic"):
        data["attention_mechanic"] = str(data["primary_mechanic"])

    for key, fallback in REQUIRED_FALLBACKS.items():
        if data.get(key) in (None, ""):
            data[key] = fallback

    try:
        analysis = FormatAnalysis.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"Invalid analysis payload: {exc}") from exc

    primary = normalize_mechanic(analysis.primary_mechanic or analysis.attention_mechanic)
    secondary = normalize_mechanics(analysis.secondary_mechanics, primary=primary)
    family = (analysis.format_family or analysis.format_key or "unnamed-format").strip()
    payload = analysis.model_dump()
    payload.update(
        {
            "primary_mechanic": primary,
            "secondary_mechanics": secondary,
            "format_family": family,
            "surface_content": analysis.surface_content or analysis.what_happens,
            "ai_leverage": clamp_rating(analysis.ai_leverage),
            "variation_density_rating": clamp_rating(analysis.variation_density_rating),
            "production_complexity_rating": clamp_rating(analysis.production_complexity_rating),
            "ip_dependency": normalize_ip_dependency(analysis.ip_dependency),
            "analysis_confidence": normalize_confidence(analysis.analysis_confidence),
            "reason_it_might_work": analysis.reason_it_might_work or analysis.why_it_works,
        }
    )
    if primary == "OTHER" and not payload.get("proposed_mechanic_label"):
        label = str(data.get("primary_mechanic") or data.get("attention_mechanic") or "").strip()
        if label and normalize_mechanic(label) == "OTHER":
            payload["proposed_mechanic_label"] = label
    return FormatAnalysis.model_validate(payload)
