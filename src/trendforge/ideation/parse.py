from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from trendforge.analysis.mechanics import clamp_rating, normalize_mechanic
from trendforge.ideation.schema import FormatIdea, IdeationPayload

_PUNCT = re.compile(r"[^a-z0-9]+")


def _as_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, IdeationPayload):
        return raw.model_dump()
    if not isinstance(raw, dict):
        raise ValueError("Ideation response must be a JSON object")
    return dict(raw)


def parse_ideation_payload(raw: Any, *, family_key: str, primary_mechanic: str) -> list[FormatIdea]:
    data = _as_dict(raw)
    if "overall_score" in data or "status" in data:
        data.pop("overall_score", None)
        data.pop("status", None)
    ideas_raw = data.get("ideas")
    if ideas_raw is None and isinstance(data.get("concepts"), list):
        ideas_raw = data["concepts"]
    if not isinstance(ideas_raw, list):
        raise ValueError("Ideation JSON must include an ideas array")

    parsed: list[FormatIdea] = []
    for item in ideas_raw:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        if not row.get("idea_title") and row.get("title"):
            row["idea_title"] = row["title"]
        if not row.get("why_it_could_work") and row.get("why"):
            row["why_it_could_work"] = row["why"]
        if not row.get("premise") and row.get("concept"):
            row["premise"] = row["concept"]
        for key, fallback in (
            ("idea_title", "Untitled idea"),
            ("hook", ""),
            ("premise", ""),
            ("format_family", family_key),
            ("primary_mechanic", primary_mechanic),
            ("variation_axis", "premise"),
            ("why_it_could_work", ""),
            ("production_notes", ""),
            ("originality_risk", "MEDIUM"),
        ):
            if row.get(key) in (None, ""):
                row[key] = fallback
        row["ai_leverage"] = clamp_rating(row.get("ai_leverage"))
        row["production_complexity"] = clamp_rating(row.get("production_complexity"))
        row["variation_potential"] = clamp_rating(row.get("variation_potential"))
        row["primary_mechanic"] = normalize_mechanic(row.get("primary_mechanic") or primary_mechanic)
        row["format_family"] = str(row.get("format_family") or family_key)
        try:
            parsed.append(FormatIdea.model_validate(row))
        except ValidationError:
            continue
    if not parsed:
        raise ValueError("No valid ideas in ideation payload")
    return parsed


def normalize_idea_text(*parts: str) -> str:
    blob = " ".join(str(p or "") for p in parts).lower().strip()
    blob = _PUNCT.sub(" ", blob)
    return " ".join(blob.split())


def dedupe_ideas(ideas: list[FormatIdea]) -> list[FormatIdea]:
    seen: set[str] = set()
    unique: list[FormatIdea] = []
    for idea in ideas:
        key = normalize_idea_text(idea.idea_title, idea.premise, idea.variation_axis)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(idea)
    return unique
