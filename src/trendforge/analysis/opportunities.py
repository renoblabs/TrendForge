from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from statistics import median
from typing import Any, Literal

from sqlalchemy.orm import Session, joinedload

from trendforge.analysis.family_score import (
    evidence_strength,
    explain_family_status,
    family_status,
    score_family_opportunity,
)
from trendforge.analysis.origin import is_seed_record
from trendforge.config import load_family_weights
from trendforge.models import AnalysisStatus, ContentCandidate
from trendforge.services import slugify_format_key

SortKey = Literal["score", "recent"]


@dataclass
class FormatOpportunity:
    format_family: str
    display_name: str
    candidate_count: int
    channel_count: int
    unique_channel_count: int
    unique_creator_count: int
    median_discovery_score: float | None
    median_acceleration: float | None
    median_views_per_hour: float | None
    accelerating_count: int
    emerging_count: int
    latest_observation: datetime | None
    latest_observed_at: datetime | None
    ai_leverage_median: float | None
    variation_density_median: float | None
    production_complexity_median: float | None
    family_opportunity_score: float
    family_status: str
    evidence_strength: str
    explanation: str
    score_breakdown: dict[str, Any] = field(default_factory=dict)
    examples: list[ContentCandidate] = field(default_factory=list)
    hypothesis: str | None = None
    primary_mechanic: str | None = None
    variation_examples: list[str] = field(default_factory=list)


def _analysis(candidate: ContentCandidate) -> dict[str, Any]:
    data = candidate.analysis_json or {}
    return data if isinstance(data, dict) else {}


def _family_key(candidate: ContentCandidate) -> str:
    data = _analysis(candidate)
    raw = data.get("format_family") or data.get("format_key") or "unnamed-format"
    return slugify_format_key(str(raw))


def _latest_obs(candidate: ContentCandidate) -> datetime | None:
    times = [o.observed_at for o in (candidate.observations or []) if o.observed_at]
    if times:
        return max(times)
    return None


def _has_label(candidate: ContentCandidate, label: str) -> bool:
    return label in {str(x).upper() for x in (candidate.discovery_labels or [])}


def _rating_median(members: list[ContentCandidate], field: str) -> float | None:
    values: list[float] = []
    for member in members:
        raw = _analysis(member).get(field)
        if raw is None or raw == "":
            continue
        try:
            values.append(float(raw))
        except (TypeError, ValueError):
            continue
    return float(median(values)) if values else None


def aggregate_format_opportunities(
    db: Session,
    *,
    limit_examples: int = 3,
    sort: SortKey = "score",
    include_seed: bool = False,
    config: dict[str, Any] | None = None,
) -> list[FormatOpportunity]:
    cfg = config or load_family_weights()
    rows = (
        db.query(ContentCandidate)
        .options(joinedload(ContentCandidate.observations))
        .filter(ContentCandidate.analysis_status == AnalysisStatus.ANALYZED)
        .all()
    )
    if not include_seed:
        rows = [row for row in rows if not is_seed_record(row)]

    groups: dict[str, list[ContentCandidate]] = {}
    for row in rows:
        groups.setdefault(_family_key(row), []).append(row)

    opportunities: list[FormatOpportunity] = []
    for key, members in groups.items():
        scores = [float(m.discovery_score) for m in members if m.discovery_score is not None]
        accels = [float(m.acceleration) for m in members if m.acceleration is not None]
        vph = [float(m.views_per_hour) for m in members if m.views_per_hour is not None]
        channels = {
            (m.channel_id or m.channel_name or m.creator)
            for m in members
            if (m.channel_id or m.channel_name or m.creator)
        }
        creators = {m.creator or m.channel_name for m in members if (m.creator or m.channel_name)}
        accelerating_count = sum(1 for m in members if _has_label(m, "ACCELERATING"))
        emerging_count = sum(1 for m in members if _has_label(m, "EMERGING"))
        ranked = sorted(
            members,
            key=lambda m: (m.discovery_score is None, -(m.discovery_score or 0), m.id),
        )
        obs_times = [_latest_obs(m) for m in members]
        latest = max((t for t in obs_times if t is not None), default=None)
        lead = _analysis(ranked[0]) if ranked else {}
        display = str(lead.get("format_name") or lead.get("format_family") or key)
        variations: list[str] = []
        for member in ranked:
            for item in _analysis(member).get("variation_examples") or []:
                text = str(item).strip()
                if text and text not in variations:
                    variations.append(text)
        med_score = float(median(scores)) if scores else None
        med_accel = float(median(accels)) if accels else None
        med_vph = float(median(vph)) if vph else None
        ai_med = _rating_median(members, "ai_leverage")
        var_med = _rating_median(members, "variation_density_rating")
        cx_med = _rating_median(members, "production_complexity_rating")
        fam_score, breakdown = score_family_opportunity(
            candidate_count=len(members),
            unique_channel_count=len(channels),
            median_discovery_score=med_score,
            median_views_per_hour=med_vph,
            median_acceleration=med_accel,
            accelerating_count=accelerating_count,
            ai_leverage_median=ai_med,
            variation_density_median=var_med,
            production_complexity_median=cx_med,
            config=cfg,
        )
        evidence = evidence_strength(
            candidate_count=len(members),
            unique_channel_count=len(channels),
            has_performance=bool(scores or vph or accels),
            config=cfg,
        )
        status = family_status(
            score=fam_score,
            candidate_count=len(members),
            unique_channel_count=len(channels),
            accelerating_count=accelerating_count,
            evidence=evidence,
            config=cfg,
        )
        opportunities.append(
            FormatOpportunity(
                format_family=key,
                display_name=display,
                candidate_count=len(members),
                channel_count=len(channels),
                unique_channel_count=len(channels),
                unique_creator_count=len(creators),
                median_discovery_score=med_score,
                median_acceleration=med_accel,
                median_views_per_hour=med_vph,
                accelerating_count=accelerating_count,
                emerging_count=emerging_count,
                latest_observation=latest,
                latest_observed_at=latest,
                ai_leverage_median=ai_med,
                variation_density_median=var_med,
                production_complexity_median=cx_med,
                family_opportunity_score=fam_score,
                family_status=status,
                evidence_strength=evidence,
                explanation=explain_family_status(
                    status=status,
                    evidence=evidence,
                    candidate_count=len(members),
                    unique_channel_count=len(channels),
                    median_acceleration=med_accel,
                    accelerating_count=accelerating_count,
                    ai_leverage_median=ai_med,
                    variation_density_median=var_med,
                    production_complexity_median=cx_med,
                    score=fam_score,
                ),
                score_breakdown=breakdown,
                examples=ranked[:limit_examples],
                hypothesis=lead.get("format_hypothesis") or lead.get("why_it_works"),
                primary_mechanic=lead.get("primary_mechanic"),
                variation_examples=variations[:8],
            )
        )
    if sort == "recent":
        opportunities.sort(
            key=lambda o: (
                o.latest_observed_at is None,
                -(o.latest_observed_at.timestamp() if o.latest_observed_at else 0),
                -o.family_opportunity_score,
            )
        )
    else:
        opportunities.sort(
            key=lambda o: (-o.family_opportunity_score, -o.candidate_count, o.format_family)
        )
    return opportunities
