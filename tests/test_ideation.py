from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from trendforge.analysis.opportunities import aggregate_format_opportunities
from trendforge.db import get_session_factory, init_db
from trendforge.ideation.client import StubIdeation, get_ideation_provider
from trendforge.ideation.generate import UnknownFamilyError, generate_brainstorm_set
from trendforge.ideation.parse import dedupe_ideas, parse_ideation_payload
from trendforge.ideation.prompt import IDEATION_PROMPT_VERSION, IDEATION_SYSTEM_PROMPT
from trendforge.ideation.schema import FormatIdea
from trendforge.models import AnalysisStatus, ContentCandidate, FormatBrainstormSet


@pytest.fixture()
def db(tmp_path: Path) -> Session:
    path = tmp_path / "idea.db"
    init_db(path)
    session = get_session_factory(path)()
    yield session
    session.close()


def _analyzed(
    db: Session,
    *,
    slug: str,
    family: str,
    origin: str = "live",
    score: float = 40,
    labels: list[str] | None = None,
    url: str | None = None,
) -> ContentCandidate:
    row = ContentCandidate(
        platform="youtube",
        url=url or f"https://www.youtube.com/shorts/{slug}",
        title=f"clip {slug}",
        creator=slug,
        channel_id=f"UC{slug}",
        analysis_status=AnalysisStatus.ANALYZED,
        data_origin=origin,
        discovery_score=score,
        views_per_hour=500,
        acceleration=0.5,
        discovery_labels=labels or ["EMERGING"],
        is_short=True,
        analysis_json={
            "format_family": family,
            "format_key": family,
            "format_name": family.replace("-", " "),
            "primary_mechanic": "IMPOSSIBLE_POV",
            "secondary_mechanics": ["MICRO_STORY"],
            "format_hypothesis": "Familiar situation plus impossible POV and a rapid payoff.",
            "audience_signal": "Viewers stop for the perspective mismatch.",
            "variation_examples": ["parking spot", "smoke detector"],
            "ai_leverage": 5,
            "production_complexity_rating": 2,
        },
    )
    db.add(row)
    db.commit()
    return row


def test_prompt_is_separate_from_intelligence():
    assert IDEATION_PROMPT_VERSION == "format-ideation-v1"
    assert "IDEATION, not evidence" in IDEATION_SYSTEM_PROMPT
    assert "Preserve the attention mechanic" in IDEATION_SYSTEM_PROMPT


def test_parse_fills_missing_fields():
    ideas = parse_ideation_payload(
        {
            "ideas": [
                {
                    "title": "POV: last parking spot",
                    "hook": "Every car thinks you're empty.",
                    "concept": "Black Friday lot from the space's POV.",
                    "why": "Familiar hunt plus impossible perspective.",
                }
            ]
        },
        family_key="impossible-pov-micro-story",
        primary_mechanic="IMPOSSIBLE_POV",
    )
    assert len(ideas) == 1
    assert ideas[0].idea_title.startswith("POV")
    assert ideas[0].format_family == "impossible-pov-micro-story"
    assert ideas[0].primary_mechanic == "IMPOSSIBLE_POV"
    assert ideas[0].ai_leverage == 3
    assert ideas[0].variation_potential == 3


def test_malformed_payloads_raise():
    with pytest.raises(ValueError):
        parse_ideation_payload("nope", family_key="x", primary_mechanic="OTHER")
    with pytest.raises(ValueError):
        parse_ideation_payload({"ideas": []}, family_key="x", primary_mechanic="OTHER")
    with pytest.raises(ValueError):
        parse_ideation_payload({"hello": 1}, family_key="x", primary_mechanic="OTHER")


def test_dedupe_near_identical_premises():
    a = FormatIdea(
        idea_title="POV parking",
        hook="h",
        premise="Last spot in a packed lot",
        variation_axis="object",
    )
    b = FormatIdea(
        idea_title="POV parking!",
        hook="h2",
        premise="Last spot in a packed lot",
        variation_axis="object",
    )
    c = FormatIdea(
        idea_title="POV Roomba rivalry",
        hook="h3",
        premise="Second robot arrives",
        variation_axis="relationship",
    )
    unique = dedupe_ideas([a, b, c])
    assert len(unique) == 2
    assert unique[0].idea_title == "POV parking"


def test_generate_persists_history_and_prompt_version(db: Session):
    _analyzed(db, slug="a", family="impossible-pov-micro-story")
    first = generate_brainstorm_set(
        db, "impossible-pov-micro-story", count=5, force_stub=True
    )
    second = generate_brainstorm_set(
        db, "impossible-pov-micro-story", count=5, force_stub=True, emphasis="more absurd"
    )
    rows = db.query(FormatBrainstormSet).order_by(FormatBrainstormSet.id.asc()).all()
    assert len(rows) == 2
    assert rows[0].id == first.id
    assert rows[1].id == second.id
    assert first.prompt_version == "format-ideation-v1"
    assert first.analyzer == "StubIdeation"
    assert first.model == "stub"
    assert first.format_family == "impossible-pov-micro-story"
    assert len(first.ideas_json or []) == 5
    assert all(idea.get("pool_status") == "pending" for idea in first.ideas_json)
    assert second.emphasis == "more absurd"
    axes = {idea["variation_axis"] for idea in first.ideas_json}
    assert len(axes) >= 4


def test_watch_and_build_families_are_eligible(db: Session):
    _analyzed(db, slug="watch1", family="watch-family", score=10, labels=[])
    for i in range(4):
        _analyzed(
            db,
            slug=f"build{i}",
            family="build-family",
            score=70,
            labels=["ACCELERATING"],
        )
    watch = generate_brainstorm_set(db, "watch-family", count=5, force_stub=True)
    build = generate_brainstorm_set(db, "build-family", count=5, force_stub=True)
    assert watch.ideas_json
    assert build.ideas_json


def test_unknown_family_raises(db: Session):
    with pytest.raises(UnknownFamilyError):
        generate_brainstorm_set(db, "does-not-exist", force_stub=True)


def test_seed_family_ideas_are_not_evidence(db: Session):
    _analyzed(
        db,
        slug="seed1",
        family="seed-only-family",
        origin="seed",
        url="https://www.youtube.com/shorts/seed-idea-001",
    )
    live = _analyzed(db, slug="live1", family="live-family", origin="live", score=40)
    before = float(live.discovery_score or 0)
    clusters_before = aggregate_format_opportunities(db)
    live_keys = {c.format_family for c in clusters_before}
    assert "seed-only-family" not in live_keys
    generate_brainstorm_set(db, "seed-only-family", count=5, force_stub=True)
    db.refresh(live)
    clusters_after = aggregate_format_opportunities(db)
    assert live.discovery_score == before
    assert {c.format_family for c in clusters_after} == live_keys
    assert db.query(ContentCandidate).count() == 2


def test_ideation_does_not_change_family_opportunity_metrics(db: Session):
    _analyzed(db, slug="m1", family="metric-family", score=45, labels=["ACCELERATING"])
    _analyzed(db, slug="m2", family="metric-family", score=40, labels=["EMERGING"])
    before = aggregate_format_opportunities(db)[0]
    generate_brainstorm_set(db, "metric-family", count=10, force_stub=True)
    after = aggregate_format_opportunities(db)[0]
    assert after.candidate_count == before.candidate_count == 2
    assert after.family_opportunity_score == before.family_opportunity_score
    assert after.family_status == before.family_status
    assert after.evidence_strength == before.evidence_strength
    assert after.median_discovery_score == before.median_discovery_score


def test_no_llm_uses_stub():
    class _Settings:
        has_openrouter = False
        openrouter_api_key = ""
        openrouter_model = "unused"

    provider = get_ideation_provider(_Settings(), force_stub=False)
    assert isinstance(provider, StubIdeation)


def test_stub_ideas_are_structured():
    ideas = StubIdeation().generate_ideas(
        {
            "format_family": "impossible-pov-micro-story",
            "primary_mechanic": "IMPOSSIBLE_POV",
        },
        count=10,
    )
    assert len(ideas) == 10
    assert ideas[0].hook
    assert ideas[0].why_it_could_work
    assert "funny and engaging" not in ideas[0].why_it_could_work.lower()
