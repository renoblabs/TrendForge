from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from trendforge.analysis.opportunities import aggregate_format_opportunities
from trendforge.db import get_session_factory, init_db
from trendforge.ideation.generate import generate_brainstorm_set
from trendforge.models import AnalysisStatus, ContentCandidate, ProductionSpec
from trendforge.production.spec_client import StubSpec, get_spec_provider
from trendforge.production.spec_export import spec_to_markdown
from trendforge.production.spec_generate import UnknownIdeaError, generate_production_spec
from trendforge.production.spec_parse import parse_production_spec
from trendforge.production.spec_prompt import SPEC_PROMPT_VERSION, SPEC_SYSTEM_PROMPT
from trendforge.production.spec_validate import SpecValidationError, validate_production_spec


@pytest.fixture()
def db(tmp_path: Path) -> Session:
    path = tmp_path / "spec.db"
    init_db(path)
    session = get_session_factory(path)()
    yield session
    session.close()


def _family_row(db: Session, *, slug: str, family: str, origin: str = "live") -> ContentCandidate:
    row = ContentCandidate(
        platform="youtube",
        url=f"https://www.youtube.com/shorts/{slug}",
        title=f"clip {slug}",
        creator=slug,
        channel_id=f"UC{slug}",
        analysis_status=AnalysisStatus.ANALYZED,
        data_origin=origin,
        discovery_score=40,
        views_per_hour=400,
        acceleration=0.4,
        discovery_labels=["EMERGING"],
        is_short=True,
        analysis_json={
            "format_family": family,
            "format_key": family,
            "format_name": "Impossible POV Micro-Story",
            "primary_mechanic": "IMPOSSIBLE_POV",
            "secondary_mechanics": ["MICRO_STORY"],
            "format_hypothesis": "Familiar situation plus impossible POV and a rapid payoff.",
            "ai_leverage": 5,
            "production_complexity_rating": 2,
        },
    )
    db.add(row)
    db.commit()
    return row


def _brainstorm(db: Session, family: str = "impossible-pov-micro-story"):
    _family_row(db, slug=f"{family}-src", family=family)
    return generate_brainstorm_set(db, family, count=5, force_stub=True)


def test_prompt_version_is_separate():
    assert SPEC_PROMPT_VERSION == "production-spec-v1"
    assert "Preserve the attention mechanic" in SPEC_SYSTEM_PROMPT
    assert "NOT video generation" in SPEC_SYSTEM_PROMPT


def test_parse_fills_missing_fields():
    spec = parse_production_spec(
        {
            "hook": "Every car thinks you're empty.",
            "premise": "Parking-spot POV during a rush.",
            "story_beats": [{"start_seconds": 0, "end_seconds": 4, "purpose": "setup"}],
            "shots": [{"start_seconds": 0, "end_seconds": 4, "subject": "Spot", "purpose": "est"}],
            "characters": [{"name": "Spot"}],
        },
        family_key="impossible-pov-micro-story",
        primary_mechanic="IMPOSSIBLE_POV",
        duration_seconds=20,
    )
    assert spec.title
    assert spec.duration_seconds == 20
    assert spec.primary_mechanic == "IMPOSSIBLE_POV"
    assert spec.production_complexity >= 1
    assert spec.shots[0].shot_number == 1


def test_malformed_spec_payloads():
    with pytest.raises(ValueError):
        parse_production_spec("nope", family_key="x", primary_mechanic="OTHER")
    with pytest.raises(ValueError):
        parse_production_spec(["x"], family_key="x", primary_mechanic="OTHER")


def test_timeline_and_overlap_validation():
    spec = parse_production_spec(
        {
            "title": "t",
            "hook": "h",
            "premise": "p",
            "duration_seconds": 10,
            "story_beats": [
                {"start_seconds": 0, "end_seconds": 6, "purpose": "a"},
                {"start_seconds": 4, "end_seconds": 10, "purpose": "b"},
            ],
            "shots": [{"start_seconds": 0, "end_seconds": 10, "subject": "A"}],
            "characters": [{"name": "A"}],
            "originality_notes": "original",
            "qa_checklist": ["hook"],
            "required_assets": [{"asset_type": "character_reference"}],
            "continuity_requirements": ["same color A"],
        },
        family_key="fam",
        primary_mechanic="IMPOSSIBLE_POV",
    )
    with pytest.raises(SpecValidationError, match="overlaps"):
        validate_production_spec(spec)


def test_duration_overflow_rejected():
    spec = parse_production_spec(
        {
            "title": "t",
            "hook": "h",
            "premise": "p",
            "duration_seconds": 10,
            "story_beats": [{"start_seconds": 0, "end_seconds": 12, "purpose": "late"}],
            "shots": [{"start_seconds": 0, "end_seconds": 10, "subject": "A"}],
            "characters": [{"name": "A"}],
            "originality_notes": "original",
            "qa_checklist": ["hook"],
            "required_assets": [{"asset_type": "character_reference"}],
            "continuity_requirements": ["same lamp color"],
        },
        family_key="fam",
        primary_mechanic="IMPOSSIBLE_POV",
    )
    with pytest.raises(SpecValidationError, match="after target duration"):
        validate_production_spec(spec)


def test_stub_spec_is_valid_and_exportable(db: Session):
    bset = _brainstorm(db)
    record = generate_production_spec(
        db, brainstorm_set_id=bset.id, idea_index=0, duration_seconds=20, force_stub=True
    )
    assert record.prompt_version == "production-spec-v1"
    assert record.analyzer == "StubSpec"
    assert record.source_brainstorm_set_id == bset.id
    assert record.source_idea_identifier == "0"
    spec = record.spec_json
    assert spec["duration_seconds"] == 20
    assert spec["aspect_ratio"] == "9:16"
    assert len(spec["shots"]) == 5
    assert spec["shots"][-1]["end_seconds"] <= 20.05
    assert spec["production_complexity"] == 2
    assert spec["qa_checklist"]
    assert spec["required_assets"]
    assert spec["continuity_requirements"]
    assert spec["comfyui_recommendations"]
    assert spec["format_family"] == "impossible-pov-micro-story"
    assert spec["primary_mechanic"]
    md = spec_to_markdown(spec)
    assert "Shot 1" in md
    assert "QA" in md
    validate_production_spec(parse_production_spec(spec, family_key="x", primary_mechanic="OTHER"))


def test_multiple_specs_for_same_idea_are_kept(db: Session):
    bset = _brainstorm(db)
    a = generate_production_spec(db, brainstorm_set_id=bset.id, idea_index=1, force_stub=True)
    b = generate_production_spec(db, brainstorm_set_id=bset.id, idea_index=1, force_stub=True)
    rows = db.query(ProductionSpec).all()
    assert len(rows) == 2
    assert {a.id, b.id} == {row.id for row in rows}


def test_unknown_idea_raises(db: Session):
    bset = _brainstorm(db)
    with pytest.raises(UnknownIdeaError):
        generate_production_spec(db, brainstorm_set_id=bset.id, idea_index=99, force_stub=True)


def test_spec_does_not_change_family_metrics(db: Session):
    bset = _brainstorm(db, family="metric-family")
    before = aggregate_format_opportunities(db)[0]
    generate_production_spec(db, brainstorm_set_id=bset.id, idea_index=0, force_stub=True)
    after = aggregate_format_opportunities(db)[0]
    assert after.candidate_count == before.candidate_count
    assert after.family_opportunity_score == before.family_opportunity_score
    assert after.evidence_strength == before.evidence_strength
    assert db.query(ContentCandidate).count() == 1


def test_seed_family_spec_is_not_evidence(db: Session):
    _family_row(db, slug="seedx", family="seed-spec-family", origin="seed")
    live = _family_row(db, slug="livex", family="live-spec-family", origin="live")
    bset = generate_brainstorm_set(db, "seed-spec-family", count=5, force_stub=True)
    score = live.discovery_score
    generate_production_spec(db, brainstorm_set_id=bset.id, idea_index=0, force_stub=True)
    db.refresh(live)
    keys = {c.format_family for c in aggregate_format_opportunities(db)}
    assert "seed-spec-family" not in keys
    assert live.discovery_score == score


def test_no_llm_uses_stub_provider():
    class _Settings:
        has_openrouter = False
        openrouter_api_key = ""
        openrouter_model = "unused"

    assert isinstance(get_spec_provider(_Settings(), force_stub=False), StubSpec)


def test_generic_continuity_rejected():
    spec = parse_production_spec(
        {
            "title": "t",
            "hook": "h",
            "premise": "p",
            "duration_seconds": 8,
            "story_beats": [{"start_seconds": 0, "end_seconds": 8, "purpose": "all"}],
            "shots": [{"start_seconds": 0, "end_seconds": 8, "subject": "A", "action": "A waits"}],
            "characters": [{"name": "A"}],
            "originality_notes": "original",
            "qa_checklist": ["hook"],
            "required_assets": [{"asset_type": "character_reference"}],
            "continuity_requirements": ["keep it consistent"],
        },
        family_key="fam",
        primary_mechanic="IMPOSSIBLE_POV",
    )
    with pytest.raises(SpecValidationError, match="concrete"):
        validate_production_spec(spec)
