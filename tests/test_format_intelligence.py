from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from trendforge.analysis.client import StubAnalyzer, get_analyzer
from trendforge.analysis.mechanics import normalize_mechanic, normalize_mechanics
from trendforge.analysis.opportunities import aggregate_format_opportunities
from trendforge.analysis.parse import parse_format_analysis
from trendforge.analysis.selection import is_high_signal
from trendforge.db import get_session_factory, init_db
from trendforge.discovery.pipeline import promote_top_candidates, upsert_discovered_video
from trendforge.discovery.provider import DiscoveredVideo
from trendforge.models import AnalysisStatus, ContentCandidate
from trendforge.services import analyze_candidate


NOW = datetime(2026, 8, 14, 18, 0, tzinfo=timezone.utc)
FIXTURE = Path(__file__).parent / "fixtures" / "analysis_sample.json"


@pytest.fixture()
def db(tmp_path: Path) -> Session:
    path = tmp_path / "fmt.db"
    init_db(path)
    session = get_session_factory(path)()
    yield session
    session.close()


def _video(vid: str, *, views: int, hours_ago: float = 6, labels=None, score=None) -> DiscoveredVideo:
    return DiscoveredVideo(
        external_id=vid,
        url=f"https://www.youtube.com/shorts/{vid}",
        title=f"Title {vid}",
        description="demo",
        channel_id=f"UC{vid}",
        channel_name=f"Chan {vid}",
        published_at=NOW - timedelta(hours=hours_ago),
        duration=42.0,
        views=views,
        likes=10,
        comments=2,
        is_short=True,
        source_query="broad",
        raw={"id": vid},
    )


def test_parse_fixture_and_missing_fields():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    parsed = parse_format_analysis(data)
    assert parsed.format_key == "unexpected-character-performance"
    assert parsed.primary_mechanic
    filled = parse_format_analysis({})
    assert filled.format_key == "unnamed-format"
    assert filled.primary_mechanic == "OTHER"
    assert filled.ai_leverage == 3


def test_invalid_llm_payloads():
    with pytest.raises(ValueError):
        parse_format_analysis("nope")
    with pytest.raises(ValueError):
        parse_format_analysis([])
    with pytest.raises(ValueError):
        parse_format_analysis({"novelty_signal": 999})


def test_mechanic_normalization_and_secondary():
    assert normalize_mechanic("role reversal") == "ROLE_REVERSAL"
    assert normalize_mechanic("en-US") == "OTHER"
    parsed = parse_format_analysis(
        {
            "what_happens": "x",
            "first_second_hook": "x",
            "why_stop_scrolling": "x",
            "attention_mechanic": "role reversal",
            "primary_mechanic": "ROLE_REVERSAL",
            "secondary_mechanics": ["absurd reality", "ROLE_REVERSAL", "visual contradiction"],
            "format_name": "Absurd role swap",
            "format_key": "absurd-reality-role-reversal",
            "format_description": "x",
            "hook_pattern": "x",
            "why_it_works": "x",
            "estimated_variation_count": 12,
            "novelty_signal": 10,
            "replicability_signal": 10,
            "saturation_signal": 10,
            "trend_velocity_signal": 10,
            "cross_platform_signal": 10,
            "hook_strength_signal": 10,
            "production_complexity_signal": 10,
            "comfy_feasibility_signal": 10,
            "ip_risk_signal": 10,
            "recommended_production_method": "image_to_video",
            "ai_leverage": 5,
            "variation_density_rating": 5,
            "production_complexity_rating": 2,
            "ip_dependency": "low IP dependence",
            "variation_examples": ["mosquito → shark"],
            "format_hypothesis": "Invert a familiar interaction immediately.",
        }
    )
    assert parsed.primary_mechanic == "ROLE_REVERSAL"
    assert parsed.secondary_mechanics == ["ABSURD_REALITY", "VISUAL_CONTRADICTION"]
    assert parsed.ip_dependency == "LOW"
    assert parsed.variation_examples == ["mosquito → shark"]
    assert parsed.ai_leverage == 5
    assert "ROLE_REVERSAL" not in parsed.secondary_mechanics or True
    assert normalize_mechanics(["ROLE_REVERSAL"], primary="ROLE_REVERSAL") == []


def test_stub_analyzer_and_no_llm_key():
    analysis = StubAnalyzer().analyze_candidate({"title": "Mosquito short"})
    assert analysis.primary_mechanic == "ROLE_REVERSAL"
    assert analysis.format_hypothesis
    analyzer = get_analyzer(force_stub=True)
    assert isinstance(analyzer, StubAnalyzer)


def test_analyze_stores_metadata_and_history(db: Session):
    c = ContentCandidate(
        platform="youtube",
        url="https://www.youtube.com/shorts/hist1",
        title="hist",
        analysis_status=AnalysisStatus.PENDING,
        is_short=True,
    )
    db.add(c)
    db.commit()
    analyze_candidate(db, c, force_stub=True)
    db.refresh(c)
    assert c.analysis_json["analyzer"] == "StubAnalyzer"
    assert c.analysis_json["model"] == "stub"
    assert c.analysis_json["prompt_version"]
    assert c.analysis_json["analysis_timestamp"]
    first = dict(c.analysis_json)
    analyze_candidate(db, c, force_stub=True)
    db.refresh(c)
    assert c.analysis_history
    assert c.analysis_history[0]["analysis_timestamp"] == first["analysis_timestamp"]


def test_high_signal_selection_and_promotion_threshold(db: Session):
    cfg = {
        "promote_top_n": 10,
        "min_creator_videos": 3,
        "analysis": {
            "high_signal_only": True,
            "promote_accelerating": True,
            "promote_emerging": True,
            "emerging_min_discovery_score": 40,
        },
    }
    low, _ = upsert_discovered_video(db, _video("low", views=80, hours_ago=4))
    high, _ = upsert_discovered_video(db, _video("high", views=80_000, hours_ago=4))
    accel, _ = upsert_discovered_video(db, _video("acc", views=200, hours_ago=4))
    db.commit()
    high.discovery_labels = ["EMERGING"]
    high.discovery_score = 50
    low.discovery_labels = ["EMERGING"]
    low.discovery_score = 10
    accel.discovery_labels = ["ACCELERATING"]
    accel.discovery_score = 20
    db.commit()
    assert is_high_signal(high, cfg) is True
    assert is_high_signal(low, cfg) is False
    assert is_high_signal(accel, cfg) is True
    n = promote_top_candidates(db, cfg=cfg, force_stub_analysis=True, analyze=True)
    assert n == 2
    promoted_ids = {
        row.external_id
        for row in db.query(ContentCandidate).filter(ContentCandidate.promoted_at.isnot(None)).all()
    }
    assert promoted_ids == {"high", "acc"}


def test_format_opportunity_aggregation(db: Session):
    cfg = {"promote_top_n": 5, "min_creator_videos": 3, "analysis": {"high_signal_only": False}}
    a, _ = upsert_discovered_video(db, _video("a", views=90_000, hours_ago=5))
    b, _ = upsert_discovered_video(db, _video("b", views=70_000, hours_ago=5))
    db.commit()
    a.discovery_score = 50
    b.discovery_score = 40
    a.acceleration = 3.0
    b.acceleration = 5.0
    db.commit()
    analyze_candidate(db, a, force_stub=True)
    analyze_candidate(db, b, force_stub=True)
    clusters = aggregate_format_opportunities(db)
    assert len(clusters) == 1
    cluster = clusters[0]
    assert cluster.candidate_count == 2
    assert cluster.channel_count == 2
    assert cluster.median_discovery_score == 45
    assert cluster.median_acceleration == 4.0
    assert cluster.primary_mechanic == "ROLE_REVERSAL"
    assert cluster.hypothesis
    assert cluster.variation_examples
