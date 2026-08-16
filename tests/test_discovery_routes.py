from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trendforge.app import app
from trendforge.db import get_db, get_session_factory, init_db


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "routes.db"
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


def test_discovery_opportunities_ok_when_empty(client: TestClient):
    response = client.get("/discovery/opportunities")
    assert response.status_code == 200
    body = response.text
    assert "Format Opportunities" in body
    assert "No live analyzed families yet" in body


def test_discovery_home_links_to_opportunities(client: TestClient):
    response = client.get("/discovery")
    assert response.status_code == 200
    assert "/discovery/opportunities" in response.text
    assert "/generation" in response.text
    assert "Gathering schedule" in response.text
    assert "Run discover now" in response.text


def test_gather_route_without_youtube_key(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "trendforge.discovery.gather.get_settings",
        lambda: type("S", (), {"has_youtube": False})(),
    )
    response = client.post("/discovery/gather", data={"job": "observe"}, follow_redirects=False)
    assert response.status_code == 303
    assert "error=" in response.headers.get("location", "")
    assert "YOUTUBE" in response.headers.get("location", "")


def test_brainstorm_route_persists_stub_ideas(client: TestClient, tmp_path: Path, monkeypatch):
    from trendforge.ideation.client import StubIdeation
    from trendforge.models import AnalysisStatus, ContentCandidate

    monkeypatch.setattr(
        "trendforge.ideation.generate.get_ideation_provider",
        lambda settings=None, force_stub=False: StubIdeation(),
    )
    monkeypatch.setattr(
        "trendforge.production.spec_generate.get_spec_provider",
        lambda settings=None, force_stub=False: __import__(
            "trendforge.production.spec_client", fromlist=["StubSpec"]
        ).StubSpec(),
    )

    SessionLocal = get_session_factory(tmp_path / "routes.db")
    db = SessionLocal()
    try:
        db.add(
            ContentCandidate(
                platform="youtube",
                url="https://www.youtube.com/shorts/idearoute",
                title="live idea clip",
                analysis_status=AnalysisStatus.ANALYZED,
                data_origin="live",
                is_short=True,
                discovery_score=40,
                analysis_json={
                    "format_family": "impossible-pov-micro-story",
                    "format_key": "impossible-pov-micro-story",
                    "format_name": "Impossible POV Micro-Story",
                    "primary_mechanic": "IMPOSSIBLE_POV",
                    "format_hypothesis": "Impossible POV plus rapid payoff.",
                },
            )
        )
        db.commit()
    finally:
        db.close()

    listed = client.get("/discovery/opportunities")
    assert listed.status_code == 200
    assert "Generate ideas" in listed.text
    posted = client.post(
        "/discovery/opportunities/impossible-pov-micro-story/brainstorm",
        data={"count": "5", "emphasis": "", "sort": "score", "force_stub": "true"},
        follow_redirects=True,
    )
    assert posted.status_code == 200
    assert "last parking spot" in posted.text
    assert "format-ideation-v1" in posted.text
    assert "Generation" in posted.text
    assert "Create Production Spec" in posted.text
    specced = client.post(
        "/discovery/opportunities/brainstorm/1/ideas/0/spec",
        data={"sort": "score", "duration_seconds": "20", "force_stub": "true"},
        follow_redirects=True,
    )
    assert specced.status_code == 200
    assert "Production spec" in specced.text
    assert "production-spec-v1" in specced.text
    assert "Shot 1" in specced.text
    export = client.get("/production/specs/1/export.md")
    assert export.status_code == 200
    assert "production_spec_1.md" in export.headers.get("content-disposition", "")
    assert "QA" in export.text
