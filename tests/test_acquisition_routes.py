from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trendforge.app import app
from trendforge.db import get_db, get_session_factory, init_db
from trendforge.models import AcquisitionRun


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "acq-routes.db"
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


def test_acquisition_page_loads_without_token(client: TestClient):
    response = client.get("/acquisition")
    assert response.status_code == 200
    body = response.text
    assert "Data Sources" in body
    assert "YouTube" in body
    assert "TikTok" in body
    assert "Instagram" in body
    assert "Run TikTok" in body
    assert "Emerging Breakout" in body
    assert "Profile comparison" in body
    assert "TikTok Trending" in body
    assert "apify_api_" not in body.lower()


def test_acquisition_run_detail(client: TestClient, tmp_path: Path):
    SessionLocal = get_session_factory(tmp_path / "acq-routes.db")
    db = SessionLocal()
    try:
        run = AcquisitionRun(
            source="tiktok",
            provider="apify",
            actor_id="test/tiktok-actor",
            status="succeeded",
            items_found=2,
            items_new=1,
            items_duplicate=1,
            items_rejected=0,
            actual_cost=0.08,
            currency="USD",
            apify_run_id="run-x",
            apify_dataset_id="ds-x",
            run_metadata_json={
                "quality_sample": [
                    {
                        "platform": "tiktok",
                        "title": "sample caption",
                        "creator": "alice",
                        "views": 1000,
                        "likes": 10,
                        "comments": 1,
                        "url": "https://www.tiktok.com/@alice/video/1",
                    }
                ]
            },
        )
        db.add(run)
        db.commit()
        run_id = run.id
    finally:
        db.close()

    response = client.get(f"/acquisition/runs/{run_id}")
    assert response.status_code == 200
    assert "run-x" in response.text
    assert "sample caption" in response.text
    assert "alice" in response.text
    assert "0.08" in response.text
    assert "apify_api_" not in response.text
