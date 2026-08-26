from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from trendforge.acquisition.provider import ActorRunResult
from trendforge.config import load_data_sources_config
from trendforge.db import get_session_factory, init_db
from trendforge.models import (
    AcquisitionRun,
    AnalysisStatus,
    CandidateObservation,
    ContentCandidate,
    DiscoveryRun,
    ResearchCohort,
    ResearchCohortMember,
)
from trendforge.research.cohorts import (
    backfill_known_cohorts,
    classify_trajectory,
    cohort_costs,
    create_cohort_from_acquisition,
    milestone_state,
    observe_cohort,
    start_cohort,
)

UTC = timezone.utc
T0 = datetime(2026, 8, 25, 14, 0, tzinfo=UTC)


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setattr("trendforge.acquisition.service.DATA_DIR", tmp_path)
    path = tmp_path / "cohorts.db"
    init_db(path)
    session = get_session_factory(path)()
    yield session
    session.close()


def _raw(video_id: str, creator: str, views: int = 100) -> dict:
    return {
        "id": video_id,
        "text": f"test {creator} #pov",
        "textLanguage": "en",
        "createTimeISO": "2026-08-25T13:00:00.000Z",
        "webVideoUrl": f"https://www.tiktok.com/@{creator}/video/{video_id}",
        "playCount": views,
        "diggCount": 10,
        "commentCount": 2,
        "shareCount": 1,
        "authorMeta": {"id": f"author-{video_id}", "name": creator, "fans": 1000},
        "videoMeta": {"duration": 12},
        "hashtags": [{"name": "pov"}],
        "searchQuery": "POV",
    }


class FakeApify:
    def __init__(self, items: list[dict], *, cost: float = 0.02):
        self.items = items
        self.cost = cost
        self.calls: list[dict] = []

    def run_actor_result(self, actor_id, run_input, **kwargs):
        self.calls.append({"actor_id": actor_id, "input": run_input, "kwargs": kwargs})
        return ActorRunResult(
            actor_id=actor_id,
            items=deepcopy(self.items),
            run_id=f"provider-{len(self.calls)}",
            dataset_id=f"dataset-{len(self.calls)}",
            status="SUCCEEDED",
            actual_cost=self.cost,
            currency="USD",
        )


def _acquisition_cfg() -> dict:
    cfg = deepcopy(load_data_sources_config())
    cfg["apify"]["actors"]["tiktok_fresh_search"] = "test/tiktok"
    cfg["profiles"]["tiktok_fresh_search"]["actor_key"] = "tiktok_fresh_search"
    return cfg


def test_start_creates_frozen_exact_new_membership(db: Session):
    client = FakeApify([_raw("101", "one"), _raw("101", "one")], cost=0.031)
    acquisition, cohort = start_cohort(
        db,
        source="tiktok",
        profile="tiktok_fresh_search",
        limit=25,
        client=client,
        cfg=_acquisition_cfg(),
    )
    assert acquisition.items_new == 1
    assert acquisition.items_duplicate == 1
    assert (acquisition.run_metadata_json or {})["new_candidate_ids"]
    assert cohort is not None
    members = db.query(ResearchCohortMember).filter_by(cohort_id=cohort.id).all()
    assert len(members) == 1
    assert members[0].candidate.analysis_status == AnalysisStatus.SKIPPED
    assert members[0].t0_observation_id is not None
    assert cohort.target_t1_at == cohort.t0_at + timedelta(hours=3.5)
    assert cohort.target_t2_at == cohort.t0_at + timedelta(days=7)
    assert cohort.config_snapshot_json["queries"] == [
        "AI", "comedy", "funny", "POV", "character", "transformation", "animal"
    ]
    assert "token" not in str(cohort.config_snapshot_json).lower()


def test_no_new_candidates_means_no_new_cohort(db: Session):
    first = FakeApify([_raw("101", "one")])
    start_cohort(
        db,
        source="tiktok",
        profile="tiktok_fresh_search",
        limit=25,
        client=first,
        cfg=_acquisition_cfg(),
    )
    second_run, second_cohort = start_cohort(
        db,
        source="tiktok",
        profile="tiktok_fresh_search",
        limit=25,
        client=FakeApify([_raw("101", "one", 200)]),
        cfg=_acquisition_cfg(),
    )
    assert second_run.items_new == 0
    assert second_cohort is None
    assert db.query(ResearchCohort).count() == 1


def test_exact_observation_has_no_global_limit_and_is_idempotent(db: Session):
    acquisition, cohort = start_cohort(
        db,
        source="tiktok",
        profile="tiktok_fresh_search",
        limit=25,
        client=FakeApify([_raw("101", "one"), _raw("102", "two")]),
        cfg=_acquisition_cfg(),
    )
    assert cohort is not None
    later_items = [_raw("101", "one", 500), _raw("102", "two", 900)]
    observer = FakeApify(later_items, cost=0.044)
    result = observe_cohort(
        db,
        cohort.id,
        "T1",
        client=observer,
        now=cohort.target_t1_at,
    )
    assert sorted(result.requested_candidate_ids) == sorted(
        member.candidate_id for member in cohort.members
    )
    assert len(observer.calls) == 1
    assert observer.calls[0]["input"]["postURLs"] == [
        member.candidate.url for member in cohort.members
    ]
    assert observer.calls[0]["kwargs"]["max_items"] == 2
    assert result.run is not None and result.run.actual_cost == 0.044
    assert result.run.apify_run_id == "provider-1"
    assert all(member.t1_observation_id for member in cohort.members)
    observation_count = db.query(CandidateObservation).count()

    repeated = observe_cohort(
        db,
        cohort.id,
        "T1",
        client=observer,
        now=cohort.target_t1_at + timedelta(minutes=5),
    )
    assert repeated.no_op is True
    assert len(observer.calls) == 1
    assert db.query(CandidateObservation).count() == observation_count
    assert milestone_state(db, cohort, "T1") == "COMPLETE"


def test_explicit_provider_error_right_censors_without_erasing_metrics(db: Session):
    _, cohort = start_cohort(
        db,
        source="tiktok",
        profile="tiktok_fresh_search",
        limit=25,
        client=FakeApify([_raw("101", "one", 100)]),
        cfg=_acquisition_cfg(),
    )
    assert cohort is not None
    candidate = cohort.members[0].candidate
    observer = FakeApify(
        [
            {
                "url": candidate.url,
                "errorCode": "POST_NOT_FOUND_OR_PRIVATE",
                "error": "Post not found or private",
            }
        ],
        cost=0.01,
    )
    result = observe_cohort(
        db,
        cohort.id,
        "T1",
        client=observer,
        now=cohort.target_t1_at,
    )
    member = cohort.members[0]
    assert result.observed_candidate_ids == []
    assert result.censored_candidate_ids == [candidate.id]
    assert member.right_censored is True
    assert "POST_NOT_FOUND_OR_PRIVATE" in member.censor_reason
    assert candidate.views == 100
    assert member.t1_observation_id is None
    assert db.query(CandidateObservation).filter_by(candidate_id=candidate.id).count() == 1
    assert milestone_state(db, cohort, "T1") == "RIGHT_CENSORED"
    assert observe_cohort(
        db,
        cohort.id,
        "T1",
        client=observer,
        now=cohort.target_t1_at,
    ).no_op
    assert len(observer.calls) == 1


def test_cost_aggregation_and_trajectory_rules(db: Session):
    _, cohort = start_cohort(
        db,
        source="tiktok",
        profile="tiktok_fresh_search",
        limit=25,
        client=FakeApify([_raw("101", "one", 100)], cost=0.03),
        cfg=_acquisition_cfg(),
    )
    assert cohort is not None
    observe_cohort(
        db,
        cohort.id,
        "T1",
        client=FakeApify([_raw("101", "one", 1000)], cost=0.02),
        now=cohort.target_t1_at,
    )
    costs = cohort_costs(db, cohort)
    assert costs["acquisition"] == 0.03
    assert costs["t1"] == 0.02
    assert costs["known_spend"] == 0.05
    assert classify_trajectory(2_630, 3_200_000) == "BREAKOUT"
    assert classify_trajectory(753, 92_200) == "STRONG_RISER"
    assert classify_trajectory(5_000, 8_000) == "MODERATE"
    assert classify_trajectory(250, 892) == "STALLED"


def _seed_known_backfill(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    run_times = {
        18: (T0 - timedelta(hours=1), T0 - timedelta(minutes=50)),
        22: (T0 - timedelta(minutes=20), T0),
        24: (T0 + timedelta(days=8), T0 + timedelta(days=8, minutes=1)),
    }
    acquisitions = {}
    for run_id, (started, completed) in run_times.items():
        run = AcquisitionRun(
            id=run_id,
            source="tiktok",
            provider="apify",
            actor_id="clockworks/tiktok-scraper",
            profile="tiktok_fresh_search",
            started_at=started,
            completed_at=completed,
            status="succeeded",
            actual_cost=0.01,
            currency="USD",
            run_metadata_json={
                "limit": 25,
                "queries": ["POV"],
                "window": "PAST_24_HOURS",
                "actor_input": {
                    "searchQueries": ["POV"],
                    "searchSection": "/video",
                    "videoSearchSorting": "LATEST",
                    "videoSearchDateFilter": "PAST_24_HOURS",
                    "resultsPerPage": 4,
                },
            },
        )
        db.add(run)
        acquisitions[run_id] = run
    discovery_times = {
        13: (T0 + timedelta(hours=3), T0 + timedelta(hours=3, minutes=1)),
        15: (T0 + timedelta(days=7), T0 + timedelta(days=7, minutes=1)),
        16: (T0 + timedelta(days=8, hours=3), T0 + timedelta(days=8, hours=3, minutes=1)),
    }
    discoveries = {}
    for run_id, (started, completed) in discovery_times.items():
        run = DiscoveryRun(
            id=run_id,
            kind="observe_tiktok",
            started_at=started,
            completed_at=completed,
            candidates_found=13 if run_id != 16 else 20,
            observations_written=13 if run_id != 16 else 7,
        )
        db.add(run)
        discoveries[run_id] = run
    db.flush()

    ids_by_run: dict[int, list[int]] = {18: [], 22: [], 24: []}
    for index in range(13):
        candidate = ContentCandidate(
            platform="tiktok",
            external_id=f"mature-{index}",
            url=f"https://www.tiktok.com/@m{index}/video/{1000 + index}",
            creator=f"m{index}",
            source_query="POV",
            analysis_status=AnalysisStatus.SKIPPED,
        )
        db.add(candidate)
        db.flush()
        source_run = acquisitions[18] if index < 7 else acquisitions[22]
        ids_by_run[source_run.id].append(candidate.id)
        t0 = CandidateObservation(
            candidate_id=candidate.id,
            observed_at=source_run.completed_at,
            view_count=100 + index,
            source="tiktok",
        )
        t1 = CandidateObservation(
            candidate_id=candidate.id,
            observed_at=discoveries[13].completed_at,
            view_count=500 + index,
            source="tiktok_observe",
        )
        t2 = CandidateObservation(
            candidate_id=candidate.id,
            observed_at=discoveries[15].completed_at,
            view_count=None if index in {5, 9} else 2_000 + index,
            source="tiktok_observe",
        )
        db.add_all([t0, t1, t2])

    for index in range(7):
        candidate = ContentCandidate(
            platform="tiktok",
            external_id=f"cohort24-{index}",
            url=f"https://www.tiktok.com/@c{index}/video/{2000 + index}",
            creator=f"c{index}",
            source_query="AI",
            acquisition_run_id=24,
            analysis_status=AnalysisStatus.SKIPPED,
        )
        db.add(candidate)
        db.flush()
        ids_by_run[24].append(candidate.id)
        db.add_all(
            [
                CandidateObservation(
                    candidate_id=candidate.id,
                    observed_at=acquisitions[24].completed_at,
                    view_count=100,
                    source="tiktok",
                ),
                CandidateObservation(
                    candidate_id=candidate.id,
                    observed_at=discoveries[16].completed_at,
                    view_count=200,
                    source="tiktok_observe",
                ),
            ]
        )
    db.commit()
    monkeypatch.setattr(
        "trendforge.research.cohorts._raw_candidate_ids_for_run",
        lambda _db, run: ids_by_run[run.id],
    )


def test_backfill_is_idempotent_and_preserves_composite_and_censoring(
    db: Session, monkeypatch: pytest.MonkeyPatch
):
    _seed_known_backfill(db, monkeypatch)
    first = backfill_known_cohorts(db)
    second = backfill_known_cohorts(db)
    assert [cohort.id for cohort in first] == [cohort.id for cohort in second]
    assert db.query(ResearchCohort).count() == 2
    assert db.query(ResearchCohortMember).count() == 20
    mature = db.query(ResearchCohort).filter_by(cohort_key="legacy-tiktok-fresh-search-mature-v1").one()
    assert mature.acquisition_run_id is None
    assert mature.source_run_ids_json == [18, 22]
    assert mature.config_snapshot_json["composite"] is True
    assert len(mature.members) == 13
    assert sum(member.right_censored for member in mature.members) == 2
    cohort24 = db.query(ResearchCohort).filter_by(cohort_key="acquisition-run-24").one()
    assert len(cohort24.members) == 7
    assert all(member.t0_observation_id and member.t1_observation_id for member in cohort24.members)
    assert all(member.t2_observation_id is None for member in cohort24.members)
