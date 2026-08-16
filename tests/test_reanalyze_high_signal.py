from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from trendforge.analysis.client import OpenRouterAnalyzer, StubAnalyzer
from trendforge.analysis.opportunities import aggregate_format_opportunities
from trendforge.analysis.origin import is_seed_record
from trendforge.analysis.parse import parse_format_analysis
from trendforge.analysis.prompt import ANALYSIS_SYSTEM_PROMPT, PROMPT_VERSION
from trendforge.analysis.selection import is_high_signal, is_stub_analysis, needs_live_reanalysis
from trendforge.config import get_settings
from trendforge.db import get_session_factory, init_db
from trendforge.discovery.pipeline import (
    run_high_signal_analysis,
    upsert_discovered_video,
)
from trendforge.discovery.provider import DiscoveredVideo
from trendforge.models import AnalysisStatus, ContentCandidate
from trendforge.services import analyze_candidate


NOW = datetime(2026, 8, 14, 18, 0, tzinfo=timezone.utc)

CFG = {
    "promote_top_n": 10,
    "min_creator_videos": 3,
    "analysis": {
        "high_signal_only": True,
        "promote_accelerating": True,
        "promote_emerging": True,
        "emerging_min_discovery_score": 20,
        "max_analyze_per_run": 10,
    },
}


@pytest.fixture()
def db(tmp_path: Path) -> Session:
    path = tmp_path / "reanalyze.db"
    init_db(path)
    session = get_session_factory(path)()
    yield session
    session.close()


def _video(vid: str, *, views: int = 80_000, hours_ago: float = 4) -> DiscoveredVideo:
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


def _high_signal_stub(
    db: Session,
    vid: str,
    *,
    score: float = 50,
    labels: list[str] | None = None,
    origin: str = "live",
    url: str | None = None,
) -> ContentCandidate:
    row, _ = upsert_discovered_video(db, _video(vid))
    row.discovery_labels = labels or ["EMERGING"]
    row.discovery_score = score
    row.data_origin = origin
    if url:
        row.url = url
    db.commit()
    analyze_candidate(db, row, force_stub=True)
    db.refresh(row)
    return row


def _live_family(family: str):
    data = StubAnalyzer().analyze_candidate({"title": "live"}).model_dump()
    data["format_family"] = family
    data["format_key"] = family
    data["format_name"] = family.replace("-", " ")
    data["surface_content"] = "Specific depicted action"
    data["primary_mechanic"] = "ROLE_REVERSAL"
    data["secondary_mechanics"] = ["ABSURD_REALITY"]
    return parse_format_analysis(data)


@pytest.fixture()
def openrouter_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-real")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def no_openrouter_key(monkeypatch):
    class _Settings:
        has_openrouter = False
        openrouter_api_key = ""
        openrouter_model = "unused"

    monkeypatch.setattr("trendforge.discovery.pipeline.get_settings", lambda: _Settings())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_prompt_version_and_family_not_mechanic():
    assert PROMPT_VERSION == "format-intelligence-v1.1"
    assert "repeatable structural mechanism" in ANALYSIS_SYSTEM_PROMPT
    assert "Mechanic is not family" in ANALYSIS_SYSTEM_PROMPT


def test_stub_live_candidate_is_eligible_for_reanalysis(db: Session):
    row = _high_signal_stub(db, "kittu")
    assert is_high_signal(row, CFG)
    assert is_stub_analysis(row)
    assert needs_live_reanalysis(row)
    assert row.analysis_json["format_family"] == "unexpected-character-performance"


def test_live_reanalysis_appends_history_and_updates_family(
    db: Session, openrouter_key, monkeypatch
):
    row = _high_signal_stub(db, "kittu")
    first = dict(row.analysis_json)
    monkeypatch.setattr(
        OpenRouterAnalyzer,
        "analyze_candidate",
        lambda self, candidate: _live_family("absurd-role-reversal-micro-story"),
    )
    stats = run_high_signal_analysis(db, live=True, cfg=CFG)
    db.refresh(row)
    assert stats.analyzed == 1
    assert stats.stub_refreshed == 1
    assert stats.live_appended == 1
    assert row.analysis_history
    assert row.analysis_history[0]["analysis_timestamp"] == first["analysis_timestamp"]
    assert row.analysis_history[0]["analyzer"] == "StubAnalyzer"
    assert row.analysis_history[0]["format_family"] == "unexpected-character-performance"
    assert row.analysis_json["prompt_version"] == "format-intelligence-v1.1"
    assert row.analysis_json["analyzer"] == "OpenRouterAnalyzer"
    assert row.analysis_json["format_family"] == "absurd-role-reversal-micro-story"
    assert row.analysis_json["primary_mechanic"] == "ROLE_REVERSAL"
    assert row.analysis_json["format_family"] != row.analysis_json["primary_mechanic"]


def test_family_aggregation_uses_latest_classification(
    db: Session, openrouter_key, monkeypatch
):
    a = _high_signal_stub(db, "oggy")
    b = _high_signal_stub(db, "sting")
    families = {
        "https://www.youtube.com/shorts/oggy": "ai-character-replacement",
        "https://www.youtube.com/shorts/sting": "absurd-role-reversal-micro-story",
    }

    def fake_analyze(self, candidate):
        url = candidate.get("url") or ""
        return _live_family(families[url])

    monkeypatch.setattr(OpenRouterAnalyzer, "analyze_candidate", fake_analyze)
    run_high_signal_analysis(db, live=True, cfg=CFG)
    db.refresh(a)
    db.refresh(b)
    clusters = aggregate_format_opportunities(db)
    keys = {c.format_family for c in clusters}
    assert "ai-character-replacement" in keys
    assert "absurd-role-reversal-micro-story" in keys
    assert "unexpected-character-performance" not in keys


def test_seed_records_not_reanalyzed(db: Session, openrouter_key, monkeypatch):
    seed = _high_signal_stub(
        db,
        "seed1",
        origin="seed",
        url="https://www.youtube.com/shorts/seed-ucp-001",
    )
    live = _high_signal_stub(db, "live1")
    called = []

    def fake_analyze(self, candidate):
        called.append(candidate.get("url"))
        return _live_family("absurd-role-reversal-micro-story")

    monkeypatch.setattr(OpenRouterAnalyzer, "analyze_candidate", fake_analyze)
    stats = run_high_signal_analysis(db, live=True, cfg=CFG)
    db.refresh(seed)
    db.refresh(live)
    assert is_seed_record(seed)
    assert seed.analysis_json["analyzer"] == "StubAnalyzer"
    assert live.analysis_json["analyzer"] == "OpenRouterAnalyzer"
    assert stats.eligible == 1
    assert all("seed-" not in (url or "") for url in called)


def test_below_threshold_not_analyzed(db: Session, openrouter_key, monkeypatch):
    low = _high_signal_stub(db, "low", score=5, labels=["EMERGING"])
    high = _high_signal_stub(db, "high", score=50, labels=["EMERGING"])
    called = []

    def fake_analyze(self, candidate):
        called.append(candidate.get("url"))
        return _live_family("ai-transformation")

    monkeypatch.setattr(OpenRouterAnalyzer, "analyze_candidate", fake_analyze)
    run_high_signal_analysis(db, live=True, cfg=CFG)
    db.refresh(low)
    db.refresh(high)
    assert is_high_signal(low, CFG) is False
    assert low.analysis_json["analyzer"] == "StubAnalyzer"
    assert high.analysis_json["analyzer"] == "OpenRouterAnalyzer"
    assert called == ["https://www.youtube.com/shorts/high"]


def test_missing_openrouter_key_leaves_stub_intact(db: Session, no_openrouter_key):
    row = _high_signal_stub(db, "kittu")
    stats = run_high_signal_analysis(db, live=True, cfg=CFG)
    db.refresh(row)
    assert stats.analyzed == 0
    assert stats.live_appended == 0
    assert "OPENROUTER_API_KEY" in (stats.skipped_reason or "")
    assert row.analysis_json["analyzer"] == "StubAnalyzer"
    assert not row.analysis_history
    assert row.analysis_json["format_family"] == "unexpected-character-performance"


def test_already_current_live_analysis_not_repeated(
    db: Session, openrouter_key, monkeypatch
):
    row = _high_signal_stub(db, "once")
    monkeypatch.setattr(
        OpenRouterAnalyzer,
        "analyze_candidate",
        lambda self, candidate: _live_family("anthropomorphic-scenario"),
    )
    first = run_high_signal_analysis(db, live=True, cfg=CFG)
    assert first.analyzed == 1
    second = run_high_signal_analysis(db, live=True, cfg=CFG)
    db.refresh(row)
    assert second.analyzed == 0
    assert len(row.analysis_history) == 1
    assert row.analysis_json["prompt_version"] == PROMPT_VERSION
