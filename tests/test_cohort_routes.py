from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trendforge.app import app
from trendforge.db import get_db, get_session_factory, init_db
from trendforge.models import (
    CandidateObservation,
    CohortDifferentialAnalysis,
    ContentCandidate,
    ResearchCohort,
    ResearchCohortMember,
)

T0 = datetime(2026, 8, 25, 14, 0, tzinfo=timezone.utc)


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "cohort-routes.db"
    init_db(db_path)
    SessionLocal = get_session_factory(db_path)

    def _override_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_experiments_empty_state_and_nav(client: TestClient):
    response = client.get("/experiments")
    assert response.status_code == 200
    assert "Cohort Experiments" in response.text
    assert "No active research cohorts" in response.text
    assert 'href="/experiments"' in response.text


def _seed_detail(db_path: Path) -> int:
    db = get_session_factory(db_path)()
    try:
        cohort = ResearchCohort(
            cohort_key="route-test",
            name="Route Test Cohort",
            source="tiktok",
            provider="apify",
            profile="tiktok_fresh_search",
            source_run_ids_json=[99],
            created_at=T0,
            t0_at=T0,
            target_t1_window_start=T0 + timedelta(hours=3),
            target_t1_at=T0 + timedelta(hours=3.5),
            target_t1_window_end=T0 + timedelta(hours=4),
            target_t2_at=T0 + timedelta(days=7),
            status="COMPLETED",
            config_fingerprint="a" * 64,
            config_snapshot_json={
                "source": "tiktok",
                "profile": "tiktok_fresh_search",
                "result_limit": 25,
                "actor_input": {"videoSearchSorting": "LATEST"},
            },
            outcome_rule_version="trajectory-v1",
        )
        db.add(cohort)
        db.flush()
        candidate = ContentCandidate(
            platform="tiktok",
            external_id="route-video",
            url="https://www.tiktok.com/@route_creator/video/1",
            creator="route_creator",
            source_query="POV",
            discovery_labels=["EMERGING"],
        )
        db.add(candidate)
        db.flush()
        observations = [
            CandidateObservation(
                candidate_id=candidate.id,
                observed_at=T0 + elapsed,
                view_count=views,
                source=source,
            )
            for elapsed, views, source in (
                (timedelta(), 100, "tiktok"),
                (timedelta(hours=3.5), 2_000, "tiktok_cohort_t1"),
                (timedelta(days=7), 150_000, "tiktok_cohort_t2"),
            )
        ]
        db.add_all(observations)
        db.flush()
        db.add(
            ResearchCohortMember(
                cohort_id=cohort.id,
                candidate_id=candidate.id,
                t0_observation_id=observations[0].id,
                t1_observation_id=observations[1].id,
                t2_observation_id=observations[2].id,
                outcome_label="BREAKOUT",
                outcome_rule_version="trajectory-v1",
            )
        )
        db.add(
            CohortDifferentialAnalysis(
                cohort_id=cohort.id,
                prompt_version="cohort-differential-v1",
                provider="openrouter",
                model="test/model",
                evidence_mode="metadata_plus_observations",
                evidence_available=["captions", "observations"],
                evidence_missing=["watched_video"],
                input_candidate_ids=[candidate.id],
                result_json={
                    "breakout_commonalities": [
                        {
                            "hypothesis": "Fast movement may discriminate.",
                            "candidate_ids": [candidate.id],
                        }
                    ],
                    "stall_commonalities": [],
                    "false_negative_hypotheses": [],
                    "counterexamples": [],
                    "candidate_level_notes": [],
                    "possible_discriminators": [],
                    "features_worth_collecting": ["retention"],
                    "limitations": ["No clip was watched or heard."],
                    "confidence": "low",
                },
            )
        )
        db.commit()
        return cohort.id
    finally:
        db.close()


def test_experiment_detail_renders_members_and_analysis(client: TestClient, tmp_path: Path):
    cohort_id = _seed_detail(tmp_path / "cohort-routes.db")
    listed = client.get("/experiments")
    assert listed.status_code == 200
    assert "Route Test Cohort" in listed.text
    assert "1</strong> breakout" in listed.text
    response = client.get(f"/experiments/{cohort_id}")
    assert response.status_code == 200
    assert "route_creator" in response.text
    assert "cohort-differential-v1" in response.text
    assert "Fast movement may discriminate" in response.text
    assert "hypotheses, not proven causal" in response.text
    assert "LATEST" in response.text


def test_experiment_detail_404(client: TestClient):
    assert client.get("/experiments/999").status_code == 404
