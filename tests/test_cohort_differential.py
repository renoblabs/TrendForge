from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from trendforge.analysis.client import MissingAPIKeyError
from trendforge.config import Settings
from trendforge.db import get_session_factory, init_db
from trendforge.models import (
    AnalysisStatus,
    CandidateObservation,
    CohortDifferentialAnalysis,
    ContentCandidate,
    ResearchCohort,
    ResearchCohortMember,
)
from trendforge.research.differential import (
    CandidateLevelNote,
    DifferentialProviderResponse,
    DifferentialResult,
    SupportedFinding,
    build_differential_context,
    parse_differential_result,
    run_cohort_differential_analysis,
    validate_result_support,
)


T0 = datetime(2026, 8, 16, 23, 45, 40, tzinfo=timezone.utc)
T1 = T0 + timedelta(hours=3.5)
T2 = T0 + timedelta(days=7)
WEIGHTS = {
    "labels": {
        "emerging_min_views_per_hour": 5_000,
        "accelerating_min_factor": 1.4,
    }
}


@pytest.fixture()
def db(tmp_path: Path) -> Session:
    path = tmp_path / "differential.db"
    init_db(path)
    session = get_session_factory(path)()
    yield session
    session.close()


def _add_member(
    db: Session,
    cohort: ResearchCohort,
    *,
    creator: str,
    t0_views: int,
    t1_views: int,
    t2_views: int | None,
    outcome: str,
    right_censored: bool = False,
    score: float = 42.5,
) -> ResearchCohortMember:
    candidate = ContentCandidate(
        platform="tiktok",
        external_id=f"video-{creator}",
        url=f"https://www.tiktok.com/@{creator}/video/{creator}",
        creator=creator,
        title=f"Caption for {creator} #pov",
        hashtags=["pov"],
        source_query="POV",
        published_at=T0 - timedelta(hours=1),
        discovered_at=T0,
        duration=12,
        discovery_score=score,
        discovery_labels=["EMERGING"] if creator == "fast_breakout" else [],
        analysis_status=AnalysisStatus.SKIPPED,
        analysis_json=None,
        raw_metadata={
            "audio": f"original sound - {creator}",
            "language_signal": "en",
            "acquisition_profile": "tiktok_fresh_search",
            "creator_history": {"baseline_sample_size": 0, "creator_lift": None},
        },
    )
    db.add(candidate)
    db.flush()
    t0 = CandidateObservation(
        candidate_id=candidate.id,
        observed_at=T0,
        view_count=t0_views,
        like_count=max(1, t0_views // 10),
        comment_count=1,
        channel_subscriber_count=1_000,
        source="tiktok",
    )
    t1 = CandidateObservation(
        candidate_id=candidate.id,
        observed_at=T1,
        view_count=t1_views,
        like_count=max(1, t1_views // 10),
        comment_count=2,
        channel_subscriber_count=1_000,
        source="tiktok_observe",
    )
    t2 = CandidateObservation(
        candidate_id=candidate.id,
        observed_at=T2,
        view_count=t2_views,
        like_count=None if t2_views is None else max(1, t2_views // 10),
        comment_count=None if t2_views is None else 3,
        channel_subscriber_count=1_000,
        source="tiktok_observe",
    )
    db.add_all([t0, t1, t2])
    db.flush()
    member = ResearchCohortMember(
        cohort_id=cohort.id,
        candidate_id=candidate.id,
        t0_observation_id=t0.id,
        t1_observation_id=t1.id,
        t2_observation_id=t2.id,
        included_at=T0,
        outcome_label=outcome,
        outcome_rule_version="trajectory-v1",
        right_censored=right_censored,
        censor_reason="provider_returned_no_metrics" if right_censored else None,
    )
    db.add(member)
    db.flush()
    return member


@pytest.fixture()
def mature_cohort(db: Session) -> ResearchCohort:
    cohort = ResearchCohort(
        cohort_key="test-mature-reference",
        name="Test mature reference",
        source="tiktok",
        provider="apify",
        profile="tiktok_fresh_search",
        acquisition_run_id=None,
        source_run_ids_json=[18, 22],
        created_at=T2,
        t0_at=T0,
        target_t1_window_start=T0 + timedelta(hours=3),
        target_t1_at=T1,
        target_t1_window_end=T0 + timedelta(hours=4),
        target_t2_at=T2,
        status="COMPLETED",
        config_fingerprint="a" * 64,
        config_snapshot_json={"source": "tiktok", "profile": "tiktok_fresh_search"},
        outcome_rule_version="trajectory-v1",
    )
    db.add(cohort)
    db.flush()
    _add_member(
        db,
        cohort,
        creator="fast_breakout",
        t0_views=1_000,
        t1_views=101_000,
        t2_views=1_500_000,
        outcome="BREAKOUT",
    )
    _add_member(
        db,
        cohort,
        creator="missed_breakout",
        t0_views=1_000,
        t1_views=4_000,
        t2_views=150_000,
        outcome="BREAKOUT",
    )
    _add_member(
        db,
        cohort,
        creator="clean_stall",
        t0_views=500,
        t1_views=600,
        t2_views=900,
        outcome="STALLED",
    )
    _add_member(
        db,
        cohort,
        creator="censored_stall",
        t0_views=600,
        t1_views=700,
        t2_views=None,
        outcome="STALLED",
        right_censored=True,
    )
    db.commit()
    db.refresh(cohort)
    return cohort


def _supported(
    hypothesis: str,
    candidate_ids: list[int],
    creators: list[str] | None = None,
) -> SupportedFinding:
    return SupportedFinding(
        hypothesis=hypothesis,
        evidence="Stored observation deltas support this research hypothesis.",
        candidate_ids=candidate_ids,
        creators=creators or [],
        confidence="medium",
    )


def _valid_result(context) -> DifferentialResult:
    breakout = context.groups["breakout"]
    stalled = context.groups["stalled"]
    false_negatives = context.groups["false_negative_breakouts"]
    return DifferentialResult(
        breakout_commonalities=[_supported("Breakouts separated early", breakout)],
        stall_commonalities=[_supported("The clean stall moved slowly", stalled)],
        candidate_level_notes=[
            CandidateLevelNote(
                candidate_id=candidate_id,
                creator=context.creators_by_id[candidate_id],
                outcome=context.outcomes_by_id[candidate_id],
                note="Metadata-only candidate note.",
            )
            for candidate_id in context.input_candidate_ids
        ],
        possible_discriminators=[
            _supported("T0 to T1 velocity may discriminate", breakout + stalled)
        ],
        counterexamples=[_supported("One breakout began below the gate", false_negatives)],
        false_negative_hypotheses=[
            _supported("The absolute velocity gate missed relative growth", [candidate_id])
            for candidate_id in false_negatives
        ],
        features_worth_collecting=["share velocity", "retention"],
        confidence="medium",
        limitations=["Captions do not establish what happened on screen."],
    )


class FakeProvider:
    def __init__(self):
        self.calls = 0

    def analyze(self, context) -> DifferentialProviderResponse:
        self.calls += 1
        return DifferentialProviderResponse(
            result=_valid_result(context),
            provider="fake-openrouter",
            model="test-model",
            usage={"prompt_tokens": 10, "completion_tokens": 20},
            actual_cost=None,
            currency=None,
        )


def test_groups_exclude_censored_negative_and_find_false_negative(
    db: Session,
    mature_cohort: ResearchCohort,
):
    context = build_differential_context(
        db,
        mature_cohort,
        weights_config=WEIGHTS,
    )
    by_creator = {creator: candidate_id for candidate_id, creator in context.creators_by_id.items()}
    assert set(context.groups["breakout"]) == {
        by_creator["fast_breakout"],
        by_creator["missed_breakout"],
    }
    assert context.groups["stalled"] == [by_creator["clean_stall"]]
    assert context.groups["right_censored_context"] == [by_creator["censored_stall"]]
    assert context.groups["false_negative_breakouts"] == [by_creator["missed_breakout"]]
    assert context.evidence_mode == "metadata_plus_observations"
    assert "video_frames_or_visual_inspection" in context.evidence_missing
    assert all(
        candidate["metadata"]["thumbnail_was_inspected"] is False
        for candidate in context.input_evidence["candidates"]
    )


def test_analysis_appends_history_records_evidence_and_does_not_mutate_candidates(
    db: Session,
    mature_cohort: ResearchCohort,
):
    candidates = [member.candidate for member in mature_cohort.members]
    before = {
        candidate.id: (
            candidate.discovery_score,
            list(candidate.discovery_labels or []),
            candidate.analysis_status,
            candidate.analysis_json,
            candidate.format_id,
        )
        for candidate in candidates
    }
    provider = FakeProvider()
    first = run_cohort_differential_analysis(
        db,
        mature_cohort.id,
        provider=provider,
        weights_config=WEIGHTS,
    )
    second = run_cohort_differential_analysis(
        db,
        mature_cohort.id,
        provider=provider,
        weights_config=WEIGHTS,
    )
    assert first.id != second.id
    assert provider.calls == 2
    assert db.query(CohortDifferentialAnalysis).filter_by(cohort_id=mature_cohort.id).count() == 2
    assert first.prompt_version == "cohort-differential-v1"
    assert first.evidence_mode == "metadata_plus_observations"
    assert "creator_and_caption_metadata" in first.evidence_available
    assert "audio_playback_or_transcript" in first.evidence_missing
    assert first.input_evidence_json["group_candidate_ids"]["stalled"]
    assert first.result_json["false_negative_hypotheses"]
    assert any("not proven causal" in item for item in first.result_json["limitations"])
    for candidate in candidates:
        db.refresh(candidate)
        assert before[candidate.id] == (
            candidate.discovery_score,
            list(candidate.discovery_labels or []),
            candidate.analysis_status,
            candidate.analysis_json,
            candidate.format_id,
        )


def test_missing_openrouter_key_leaves_analysis_pending_without_row(
    db: Session,
    mature_cohort: ResearchCohort,
):
    settings = Settings(openrouter_api_key="", openrouter_model="unused")
    with pytest.raises(MissingAPIKeyError, match="OPENROUTER_API_KEY"):
        run_cohort_differential_analysis(
            db,
            mature_cohort.id,
            settings=settings,
            weights_config=WEIGHTS,
        )
    assert db.query(CohortDifferentialAnalysis).count() == 0


def test_support_validation_rejects_unknown_candidates_and_missing_false_negative(
    db: Session,
    mature_cohort: ResearchCohort,
):
    context = build_differential_context(db, mature_cohort, weights_config=WEIGHTS)
    raw = _valid_result(context).model_dump()
    raw["possible_discriminators"][0]["candidate_ids"] = [999_999]
    with pytest.raises(ValueError, match="outside the cohort"):
        validate_result_support(parse_differential_result(raw), context)

    raw = _valid_result(context).model_dump()
    raw["false_negative_hypotheses"] = []
    with pytest.raises(ValueError, match="omit candidates"):
        validate_result_support(parse_differential_result(raw), context)


def test_parser_rejects_malformed_or_unsupported_payloads():
    with pytest.raises(ValueError, match="JSON object"):
        parse_differential_result("not-json")
    with pytest.raises(ValueError, match="Invalid differential"):
        parse_differential_result(
            {
                "breakout_commonalities": [
                    {
                        "hypothesis": "unsupported claim",
                        "evidence": "none",
                        "candidate_ids": [],
                        "creators": [],
                    }
                ],
                "confidence": "medium",
            }
        )
