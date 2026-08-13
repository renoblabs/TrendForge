from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from sqlalchemy.orm import Session

from trendforge.analysis.client import MissingAPIKeyError, get_analyzer
from trendforge.analysis.schema import FormatAnalysis
from trendforge.config import get_settings
from trendforge.models import (
    AnalysisStatus,
    ContentCandidate,
    Format,
    FormatStatus,
    FormatVariation,
    VariationStatus,
    utcnow,
)
from trendforge.scoring.engine import (
    ScoreInputs,
    production_cost_score_from_complexity,
    score_format,
)
from trendforge.sources import ManualSource, RawCandidate, detect_platform


def slugify_format_key(value: str) -> str:
    key = value.strip().lower()
    key = re.sub(r"[^a-z0-9]+", "-", key)
    return key.strip("-")[:128] or "unnamed-format"


def enrich_oembed(candidate: RawCandidate) -> RawCandidate:
    """Best-effort official oEmbed for YouTube (and generic). Never required."""
    url = candidate.url
    platform = candidate.platform or detect_platform(url)
    endpoints = []
    if platform == "youtube":
        endpoints.append(f"https://www.youtube.com/oembed?url={url}&format=json")
    elif platform == "tiktok":
        endpoints.append(f"https://www.tiktok.com/oembed?url={url}")

    for endpoint in endpoints:
        try:
            with httpx.Client(timeout=8.0, follow_redirects=True) as client:
                resp = client.get(endpoint)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                candidate.title = candidate.title or data.get("title")
                candidate.creator = candidate.creator or data.get("author_name")
                candidate.thumbnail_url = candidate.thumbnail_url or data.get("thumbnail_url")
                candidate.raw_metadata = {**(candidate.raw_metadata or {}), "oembed": data}
                candidate.platform = platform
                return candidate
        except Exception:
            continue
    candidate.platform = platform
    return candidate


def ingest_text(
    db: Session,
    text: str,
    *,
    fetch_oembed: bool = True,
    limit: int = 50,
) -> list[ContentCandidate]:
    source = ManualSource()
    raws = source.fetch_candidates(text, limit=limit)
    created: list[ContentCandidate] = []
    seen_urls: set[str] = set()
    for raw in raws:
        if raw.url in seen_urls:
            continue
        existing = db.query(ContentCandidate).filter_by(url=raw.url).one_or_none()
        if existing:
            continue
        seen_urls.add(raw.url)
        if fetch_oembed:
            raw = enrich_oembed(raw)
        row = ContentCandidate(
            platform=raw.platform,
            url=raw.url,
            creator=raw.creator,
            title=raw.title,
            description=raw.description,
            views=raw.views,
            likes=raw.likes,
            comments=raw.comments,
            shares=raw.shares,
            duration=raw.duration,
            thumbnail_url=raw.thumbnail_url,
            raw_metadata=raw.raw_metadata or None,
            analysis_status=AnalysisStatus.PENDING,
        )
        db.add(row)
        created.append(row)
    db.commit()
    for row in created:
        db.refresh(row)
    return created


def candidate_to_dict(c: ContentCandidate) -> dict[str, Any]:
    return {
        "id": c.id,
        "platform": c.platform,
        "url": c.url,
        "creator": c.creator,
        "title": c.title,
        "description": c.description,
        "views": c.views,
        "likes": c.likes,
        "comments": c.comments,
        "shares": c.shares,
        "duration": c.duration,
    }


def apply_analysis_to_format(
    db: Session,
    analysis: FormatAnalysis,
    *,
    extra_cross_platform: Optional[float] = None,
) -> Format:
    format_key = slugify_format_key(analysis.format_key)
    fmt = db.query(Format).filter_by(format_key=format_key).one_or_none()
    signals = analysis.to_score_signals()
    if extra_cross_platform is not None:
        signals["cross_platform"] = max(signals["cross_platform"], extra_cross_platform)

    cost_score = production_cost_score_from_complexity(signals["production_complexity"])
    result = score_format(
        ScoreInputs(
            trend_velocity=signals["trend_velocity"],
            novelty=signals["novelty"],
            replicability=signals["replicability"],
            variation_density=signals["variation_density"],
            cross_platform=signals["cross_platform"],
            production_cost=cost_score,
            hook_strength=signals["hook_strength"],
            ip_risk=signals["ip_risk"],
            saturation=signals["saturation"],
        )
    )

    if fmt is None:
        fmt = Format(format_key=format_key, name=analysis.format_name)
        db.add(fmt)

    fmt.name = analysis.format_name
    fmt.description = analysis.format_description
    fmt.attention_mechanic = analysis.attention_mechanic
    fmt.format_category = analysis.format_category
    fmt.hook_pattern = analysis.hook_pattern
    fmt.why_it_works = analysis.why_it_works
    fmt.variables = analysis.variables
    fmt.recommended_production_method = analysis.recommended_production_method
    fmt.production_notes = analysis.production_notes
    fmt.ip_notes = analysis.ip_notes
    fmt.novelty_score = signals["novelty"]
    fmt.replicability_score = signals["replicability"]
    fmt.variation_density = signals["variation_density"]
    fmt.cross_platform_score = signals["cross_platform"]
    fmt.production_complexity = signals["production_complexity"]
    fmt.estimated_generation_cost = analysis.estimated_generation_cost_usd
    fmt.ip_risk = signals["ip_risk"]
    fmt.comfy_feasibility = signals["comfy_feasibility"]
    fmt.saturation_score = signals["saturation"]
    fmt.trend_velocity_score = signals["trend_velocity"]
    fmt.hook_strength = signals["hook_strength"]
    fmt.overall_score = result.overall
    fmt.score_breakdown = {
        **result.breakdown,
        "overall": result.overall,
        "weighted_sum": result.weighted_sum,
        "saturation_penalty_factor": result.saturation_penalty_factor,
        "production_cost_score": cost_score,
    }
    # Auto-set lifecycle status from score when unset or still in early stages
    if fmt.status is None or fmt.status in {
        FormatStatus.DISCOVERED,
        FormatStatus.ANALYZING,
        FormatStatus.WATCH,
        FormatStatus.BUILD,
    }:
        fmt.status = FormatStatus(result.suggested_status)
    fmt.updated_at = utcnow()

    db.flush()

    # Replace idea variations with latest suggestions if none selected yet
    existing_ideas = [v for v in fmt.variations if v.status == VariationStatus.IDEA]
    if not existing_ideas:
        for idea in analysis.original_variations:
            db.add(
                FormatVariation(
                    format_id=fmt.id,
                    concept=idea.concept,
                    character=idea.character,
                    scenario=idea.scenario,
                    hook=idea.hook,
                    production_method=idea.production_method
                    or analysis.recommended_production_method,
                    notes=idea.notes,
                    estimated_cost=analysis.estimated_generation_cost_usd,
                    status=VariationStatus.IDEA,
                )
            )
    return fmt


def analyze_candidate(
    db: Session,
    candidate: ContentCandidate,
    *,
    force_stub: bool = False,
    require_live: bool = False,
) -> ContentCandidate:
    settings = get_settings()
    if require_live and not settings.has_openrouter:
        raise MissingAPIKeyError(
            "OPENROUTER_API_KEY is not set. Add a key to .env for live analysis."
        )

    candidate.analysis_status = AnalysisStatus.ANALYZING
    db.commit()

    try:
        if require_live:
            from trendforge.analysis.client import OpenRouterAnalyzer

            analyzer = OpenRouterAnalyzer(settings)
        else:
            analyzer = get_analyzer(
                settings, force_stub=force_stub or not settings.has_openrouter
            )
        analysis = analyzer.analyze_candidate(candidate_to_dict(candidate))

        # Cross-platform boost if other platforms already share this format_key
        format_key = slugify_format_key(analysis.format_key)
        existing = db.query(Format).filter_by(format_key=format_key).one_or_none()
        extra_cross = None
        if existing and existing.candidates:
            platforms = {c.platform for c in existing.candidates} | {candidate.platform}
            if len(platforms) > 1:
                extra_cross = min(100.0, 40.0 + 20.0 * len(platforms))

        fmt = apply_analysis_to_format(db, analysis, extra_cross_platform=extra_cross)
        candidate.format_id = fmt.id
        candidate.analysis_json = analysis.model_dump()
        candidate.analysis_status = AnalysisStatus.ANALYZED
        candidate.analysis_error = None
        db.commit()
        db.refresh(candidate)
        return candidate
    except MissingAPIKeyError:
        candidate.analysis_status = AnalysisStatus.FAILED
        candidate.analysis_error = "Missing OPENROUTER_API_KEY"
        db.commit()
        raise
    except Exception as exc:
        candidate.analysis_status = AnalysisStatus.FAILED
        candidate.analysis_error = str(exc)
        db.commit()
        raise


def analyze_pending(
    db: Session,
    *,
    limit: int = 50,
    force_stub: bool = False,
    require_live: bool = False,
) -> list[ContentCandidate]:
    rows = (
        db.query(ContentCandidate)
        .filter(ContentCandidate.analysis_status.in_([AnalysisStatus.PENDING, AnalysisStatus.FAILED]))
        .order_by(ContentCandidate.id.asc())
        .limit(limit)
        .all()
    )
    results = []
    for row in rows:
        try:
            results.append(
                analyze_candidate(db, row, force_stub=force_stub, require_live=require_live)
            )
        except MissingAPIKeyError:
            raise
        except Exception:
            continue
    return results
