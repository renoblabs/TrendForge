from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from trendforge.analysis.family_score import (
    evidence_strength,
    explain_family_status,
    family_status,
    score_family_opportunity,
)
from trendforge.analysis.origin import is_seed_record
from trendforge.analysis.opportunities import aggregate_format_opportunities
from trendforge.db import get_session_factory, init_db
from trendforge.models import AnalysisStatus, ContentCandidate
from pathlib import Path


NOW = datetime(2026, 8, 14, 18, 0, tzinfo=timezone.utc)


@pytest.fixture()
def db(tmp_path: Path) -> Session:
    path = tmp_path / "fam.db"
    init_db(path)
    session = get_session_factory(path)()
    yield session
    session.close()


def _row(
    db: Session,
    *,
    slug: str,
    url: str,
    origin: str = "live",
    channel: str = "UC1",
    creator: str = "A",
    score: float | None = 40,
    vph: float | None = 1000,
    accel: float | None = 1.0,
    labels: list[str] | None = None,
    family: str = "absurd-role-reversal-micro-story",
    title: str = "live clip",
    observed: bool = True,
) -> ContentCandidate:
    row = ContentCandidate(
        platform="youtube",
        url=url,
        title=title,
        creator=creator,
        channel_id=channel,
        channel_name=creator,
        analysis_status=AnalysisStatus.ANALYZED,
        data_origin=origin,
        discovery_score=score,
        views_per_hour=vph,
        acceleration=accel,
        discovery_labels=labels or [],
        is_short=True,
        analysis_json={
            "format_family": family,
            "format_key": family,
            "format_name": family,
            "primary_mechanic": "ROLE_REVERSAL",
            "format_hypothesis": "Invert a familiar interaction immediately.",
            "ai_leverage": 5,
            "variation_density_rating": 5,
            "production_complexity_rating": 2,
            "variation_examples": ["mosquito → shark"],
        },
    )
    db.add(row)
    db.flush()
    if observed:
        from trendforge.models import CandidateObservation

        db.add(
            CandidateObservation(
                candidate_id=row.id,
                observed_at=NOW,
                view_count=1000,
                source="test",
            )
        )
    db.commit()
    db.refresh(row)
    return row


def test_seed_records_are_excluded(db: Session):
    _row(db, slug="live", url="https://www.youtube.com/shorts/live1", origin="live", channel="UClive")
    seed = _row(
        db,
        slug="seed",
        url="https://www.youtube.com/shorts/seed-ucp-003",
        origin="seed",
        channel="UCseed",
        title="POV: traffic cone during rush hour (demo)",
    )
    assert is_seed_record(seed) is True
    clusters = aggregate_format_opportunities(db)
    assert len(clusters) == 1
    assert clusters[0].candidate_count == 1
    with_seed = aggregate_format_opportunities(db, include_seed=True)
    assert sum(c.candidate_count for c in with_seed) == 2


def test_unique_channels_and_medians(db: Session):
    _row(db, slug="a", url="https://www.youtube.com/shorts/a", channel="UC1", creator="A", score=30, vph=1000, accel=0.5)
    _row(db, slug="b", url="https://www.youtube.com/shorts/b", channel="UC2", creator="B", score=50, vph=3000, accel=1.5)
    _row(db, slug="c", url="https://www.youtube.com/shorts/c", channel="UC1", creator="A", score=40, vph=2000, accel=1.0)
    cluster = aggregate_format_opportunities(db)[0]
    assert cluster.unique_channel_count == 2
    assert cluster.unique_creator_count == 2
    assert cluster.median_discovery_score == 40
    assert cluster.median_views_per_hour == 2000
    assert cluster.median_acceleration == 1.0
    assert cluster.ai_leverage_median == 5
    assert cluster.variation_density_median == 5
    assert cluster.production_complexity_median == 2


def test_accelerating_and_emerging_counts(db: Session):
    _row(db, slug="e", url="https://www.youtube.com/shorts/e", channel="UC1", labels=["EMERGING"])
    _row(db, slug="a", url="https://www.youtube.com/shorts/a", channel="UC2", labels=["ACCELERATING", "EMERGING"])
    _row(db, slug="n", url="https://www.youtube.com/shorts/n", channel="UC3", labels=["POPULAR"])
    cluster = aggregate_format_opportunities(db)[0]
    assert cluster.emerging_count == 2
    assert cluster.accelerating_count == 1


def test_missing_performance_does_not_fabricate_medians(db: Session):
    row = ContentCandidate(
        platform="youtube",
        url="https://www.youtube.com/shorts/empty",
        analysis_status=AnalysisStatus.ANALYZED,
        data_origin="live",
        channel_id="UC9",
        analysis_json={"format_family": "sparse-family", "format_key": "sparse-family"},
    )
    db.add(row)
    db.commit()
    cluster = aggregate_format_opportunities(db)[0]
    assert cluster.median_discovery_score is None
    assert cluster.median_acceleration is None
    assert cluster.median_views_per_hour is None
    assert cluster.latest_observed_at is None
    assert cluster.family_opportunity_score >= 0


def test_family_opportunity_score_is_deterministic():
    a, _ = score_family_opportunity(
        candidate_count=6,
        unique_channel_count=5,
        median_discovery_score=45,
        median_views_per_hour=2000,
        median_acceleration=0.5,
        accelerating_count=1,
        ai_leverage_median=5,
        variation_density_median=5,
        production_complexity_median=2,
    )
    b, breakdown = score_family_opportunity(
        candidate_count=6,
        unique_channel_count=5,
        median_discovery_score=45,
        median_views_per_hour=2000,
        median_acceleration=0.5,
        accelerating_count=1,
        ai_leverage_median=5,
        variation_density_median=5,
        production_complexity_median=2,
    )
    assert a == b
    assert "formula" in breakdown
    assert set(breakdown["components"]) == {
        "recurrence",
        "unique_channels",
        "performance",
        "momentum",
        "production",
    }


def test_status_and_evidence_thresholds():
    assert evidence_strength(candidate_count=1, unique_channel_count=1, has_performance=True) == "LOW"
    assert evidence_strength(candidate_count=3, unique_channel_count=2, has_performance=True) == "MEDIUM"
    assert evidence_strength(candidate_count=8, unique_channel_count=5, has_performance=True) == "HIGH"
    assert (
        family_status(
            score=80,
            candidate_count=5,
            unique_channel_count=4,
            accelerating_count=2,
            evidence="HIGH",
        )
        == "BUILD"
    )
    assert (
        family_status(
            score=80,
            candidate_count=5,
            unique_channel_count=4,
            accelerating_count=2,
            evidence="LOW",
        )
        == "WATCH"
    )
    assert (
        family_status(
            score=50,
            candidate_count=3,
            unique_channel_count=2,
            accelerating_count=0,
            evidence="MEDIUM",
        )
        == "WATCH"
    )
    assert (
        family_status(
            score=10,
            candidate_count=1,
            unique_channel_count=1,
            accelerating_count=0,
            evidence="LOW",
        )
        == "REJECT"
    )


def test_explanation_and_sort_order(db: Session):
    _row(
        db,
        slug="old",
        url="https://www.youtube.com/shorts/oldfam",
        family="older-family",
        channel="UC1",
        score=20,
        vph=100,
        accel=0.2,
        observed=True,
    )
    newer = _row(
        db,
        slug="new",
        url="https://www.youtube.com/shorts/newfam",
        family="newer-family",
        channel="UC2",
        score=80,
        vph=9000,
        accel=3.0,
        labels=["ACCELERATING"],
        observed=True,
    )
    from trendforge.models import CandidateObservation

    db.add(
        CandidateObservation(
            candidate_id=newer.id,
            observed_at=NOW + timedelta(days=1),
            view_count=2000,
            source="test",
        )
    )
    db.commit()
    by_score = aggregate_format_opportunities(db, sort="score")
    assert by_score[0].format_family == "newer-family"
    by_recent = aggregate_format_opportunities(db, sort="recent")
    assert by_recent[0].format_family == "newer-family"
    text = explain_family_status(
        status="WATCH",
        evidence="MEDIUM",
        candidate_count=3,
        unique_channel_count=3,
        median_acceleration=0.53,
        accelerating_count=0,
        ai_leverage_median=5,
        variation_density_median=5,
        production_complexity_median=2,
        score=40,
    )
    assert text.startswith("WATCH")
    assert "0.53" in text


def test_representative_examples_prefer_live_high_score(db: Session):
    _row(db, slug="s", url="https://www.youtube.com/shorts/seed-x", origin="seed", channel="UCseed", score=99, title="seed (demo)")
    low = _row(db, slug="l", url="https://www.youtube.com/shorts/low", channel="UC1", score=10, title="low live")
    high = _row(db, slug="h", url="https://www.youtube.com/shorts/high", channel="UC2", score=70, title="high live")
    cluster = aggregate_format_opportunities(db)[0]
    ids = [c.id for c in cluster.examples]
    assert high.id in ids
    assert low.id in ids
    assert cluster.examples[0].id == high.id
    assert all(not is_seed_record(c) for c in cluster.examples)


def test_different_families_are_not_merged_by_mechanic(db: Session):
    _row(db, slug="a", url="https://www.youtube.com/shorts/a", family="absurd-role-reversal-micro-story", channel="UC1")
    _row(db, slug="b", url="https://www.youtube.com/shorts/b", family="ai-transformation-reveal", channel="UC2")
    clusters = aggregate_format_opportunities(db)
    assert {c.format_family for c in clusters} == {
        "absurd-role-reversal-micro-story",
        "ai-transformation-reveal",
    }
