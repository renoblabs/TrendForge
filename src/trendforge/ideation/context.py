from __future__ import annotations

from typing import Any

from trendforge.models import AnalysisStatus, ContentCandidate
from trendforge.services import slugify_format_key


def family_key_for(candidate: ContentCandidate) -> str:
    data = candidate.analysis_json if isinstance(candidate.analysis_json, dict) else {}
    raw = data.get("format_family") or data.get("format_key") or "unnamed-format"
    return slugify_format_key(str(raw))


def family_members(rows: list[ContentCandidate], family_key: str) -> list[ContentCandidate]:
    want = slugify_format_key(family_key)
    return [row for row in rows if family_key_for(row) == want]


def analyzed_candidates(db) -> list[ContentCandidate]:
    return (
        db.query(ContentCandidate)
        .filter(ContentCandidate.analysis_status == AnalysisStatus.ANALYZED)
        .all()
    )


def build_family_context(members: list[ContentCandidate], family_key: str) -> dict[str, Any]:
    lead: dict[str, Any] = {}
    for member in members:
        data = member.analysis_json if isinstance(member.analysis_json, dict) else {}
        if data:
            lead = data
            break
    examples: list[str] = []
    secondary: list[str] = []
    for member in members:
        data = member.analysis_json if isinstance(member.analysis_json, dict) else {}
        for item in data.get("variation_examples") or []:
            text = str(item).strip()
            if text and text not in examples:
                examples.append(text)
        for item in data.get("secondary_mechanics") or []:
            text = str(item).strip()
            if text and text not in secondary:
                secondary.append(text)
    return {
        "format_family": family_key,
        "format_name": lead.get("format_name") or family_key,
        "format_key": lead.get("format_key") or family_key,
        "primary_mechanic": lead.get("primary_mechanic") or "",
        "secondary_mechanics": secondary,
        "format_hypothesis": lead.get("format_hypothesis") or lead.get("why_it_works") or "",
        "audience_signal": lead.get("audience_signal") or "",
        "variation_examples": examples[:8],
        "ai_leverage": lead.get("ai_leverage"),
        "production_complexity": lead.get("production_complexity_rating"),
    }
