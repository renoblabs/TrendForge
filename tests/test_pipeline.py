from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from trendforge.analysis.client import StubAnalyzer
from trendforge.analysis.schema import FormatAnalysis
from trendforge.db import get_session_factory, init_db
from trendforge.models import AnalysisStatus, ContentCandidate, Format
from trendforge.services import analyze_candidate, apply_analysis_to_format, ingest_text, slugify_format_key
from trendforge.sources import ManualSource, detect_platform


FIXTURE = Path(__file__).parent / "fixtures" / "analysis_sample.json"


@pytest.fixture()
def db(tmp_path: Path) -> Session:
    path = tmp_path / "test.db"
    init_db(path)
    SessionLocal = get_session_factory(path)
    session = SessionLocal()
    yield session
    session.close()


def test_analysis_fixture_validates():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    analysis = FormatAnalysis.model_validate(data)
    assert analysis.format_key
    assert "overall_score" not in data
    assert analysis.novelty_signal >= 0


def test_stub_analyzer_returns_schema():
    analysis = StubAnalyzer().analyze_candidate(
        {"url": "https://example.com/x", "title": "Demo", "platform": "tiktok"}
    )
    assert isinstance(analysis, FormatAnalysis)
    assert analysis.format_key == "unexpected-character-performance"


def test_manual_ingest_and_duplicate(db: Session):
    text = "\n".join(
        [
            "https://www.tiktok.com/@a/video/1",
            "https://www.youtube.com/shorts/abc",
            "https://www.tiktok.com/@a/video/1",
        ]
    )
    created = ingest_text(db, text, fetch_oembed=False)
    assert len(created) == 2
    again = ingest_text(db, "https://www.tiktok.com/@a/video/1", fetch_oembed=False)
    assert again == []
    assert db.query(ContentCandidate).count() == 2


def test_manual_source_json():
    source = ManualSource()
    rows = source.fetch_candidates(
        '[{"url":"https://www.instagram.com/reel/x","title":"Hi","views":10}]'
    )
    assert len(rows) == 1
    assert rows[0].platform == "instagram"
    assert rows[0].views == 10


def test_format_key_matching(db: Session):
    a = FormatAnalysis.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))
    fmt1 = apply_analysis_to_format(db, a)
    db.commit()
    fmt2 = apply_analysis_to_format(db, a)
    db.commit()
    assert fmt1.id == fmt2.id
    assert db.query(Format).filter_by(format_key=a.format_key).count() == 1


def test_analyze_candidate_stub_links_format(db: Session):
    c = ContentCandidate(
        platform="tiktok",
        url="https://www.tiktok.com/@x/video/99",
        title="Kid performs demo",
        analysis_status=AnalysisStatus.PENDING,
    )
    db.add(c)
    db.commit()
    analyze_candidate(db, c, force_stub=True)
    db.refresh(c)
    assert c.analysis_status == AnalysisStatus.ANALYZED
    assert c.format_id is not None
    fmt = db.get(Format, c.format_id)
    assert fmt is not None
    assert fmt.overall_score is not None
    assert fmt.score_breakdown is not None


def test_detect_platform_and_slugify():
    assert detect_platform("https://www.tiktok.com/@x/video/1") == "tiktok"
    assert detect_platform("https://youtu.be/abc") == "youtube"
    assert slugify_format_key("Unexpected Character + Performance!") == (
        "unexpected-character-performance"
    )
