from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from trendforge.acquisition.errors import MissingTokenError
from trendforge.acquisition.provider import ActorRunResult
from trendforge.acquisition.service import run_acquisition
from trendforge.db import get_session_factory, init_db
from trendforge.models import AcquisitionRun, AnalysisStatus, CandidateObservation, ContentCandidate

FIXTURES = Path(__file__).resolve().parent / "fixtures"

CFG = {
    "apify": {
        "enabled": True,
        "actors": {
            "tiktok_discovery": "test/tiktok-actor",
            "instagram_reels": "test/ig-actor",
            "youtube_shorts": None,
        },
    },
    "sources": {
        "tiktok": {
            "enabled": True,
            "provider": "apify",
            "actor_key": "tiktok_discovery",
            "default_limit": 25,
            "max_limit": 100,
            "max_short_seconds": 60,
            "max_charge_usd": 0.5,
            "limit_input_keys": ["resultsPerPage"],
            "actor_input": {"hashtags": ["pov"], "resultsPerPage": 25},
        },
        "instagram": {
            "enabled": True,
            "provider": "apify",
            "actor_key": "instagram_reels",
            "default_limit": 25,
            "max_limit": 100,
            "max_short_seconds": 60,
            "max_charge_usd": 0.5,
            "limit_input_keys": ["resultsLimit"],
            "actor_input": {"hashtags": ["pov"], "resultsType": "reels", "resultsLimit": 25},
        },
    },
}


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setattr("trendforge.acquisition.service.DATA_DIR", tmp_path)
    path = tmp_path / "acq.db"
    init_db(path)
    session = get_session_factory(path)()
    yield session
    session.close()


class FakeApify:
    def __init__(self, items, status="SUCCEEDED", cost=0.11):
        self.items = items
        self.status = status
        self.cost = cost
        self.calls = []

    def run_actor_result(self, actor_id, run_input, **kwargs):
        self.calls.append({"actor_id": actor_id, "input": run_input, "kwargs": kwargs})
        return ActorRunResult(
            actor_id=actor_id,
            items=list(self.items),
            run_id="apify-run-1",
            dataset_id="apify-ds-1",
            status=self.status,
            actual_cost=self.cost,
            currency="USD" if self.cost is not None else None,
            raw_run={"id": "apify-run-1"},
        )


def test_tiktok_run_persists_provenance_and_observation(db: Session):
    items = json.loads((FIXTURES / "tiktok_apify.json").read_text(encoding="utf-8"))
    client = FakeApify([items[0], items[0]])
    run = run_acquisition(db, "tiktok", limit=25, client=client, cfg=CFG)
    assert run.status == "succeeded"
    assert run.provider == "apify"
    assert run.actor_id == "test/tiktok-actor"
    assert run.apify_run_id == "apify-run-1"
    assert run.apify_dataset_id == "apify-ds-1"
    assert run.actual_cost == 0.11
    assert run.currency == "USD"
    assert run.estimated_cost is None
    assert run.items_found == 2
    assert run.items_new == 1
    assert run.items_duplicate == 1
    row = db.query(ContentCandidate).one()
    assert row.platform == "tiktok"
    assert row.external_id == "7543693751290481942"
    assert row.acquisition_run_id == run.id
    assert row.acquisition_provider == "apify"
    assert row.analysis_status == AnalysisStatus.SKIPPED
    assert (row.raw_metadata or {}).get("provider") == "apify"
    assert (row.raw_metadata or {}).get("actor_id") == "test/tiktok-actor"
    assert db.query(CandidateObservation).count() == 1
    raw_path = Path((run.run_metadata_json or {})["raw_dataset_path"])
    assert raw_path.exists()
    stored = json.loads(raw_path.read_text(encoding="utf-8"))
    assert stored[0]["id"] == "7543693751290481942"


def test_non_english_rejected_unknown_kept(db: Session):
    items = json.loads((FIXTURES / "tiktok_apify.json").read_text(encoding="utf-8"))
    client = FakeApify([items[1], items[2]])
    run = run_acquisition(db, "tiktok", limit=10, client=client, cfg=CFG)
    assert run.items_rejected >= 1
    rows = db.query(ContentCandidate).all()
    assert {r.external_id for r in rows} == {"missing-metrics"}
    assert (rows[0].raw_metadata or {}).get("language_signal") == "unknown"


def test_instagram_hidden_likes_and_missing_shares(db: Session):
    items = json.loads((FIXTURES / "instagram_apify.json").read_text(encoding="utf-8"))
    run = run_acquisition(db, "instagram", limit=10, client=FakeApify(items), cfg=CFG)
    assert run.items_rejected >= 1
    row = db.query(ContentCandidate).filter_by(external_id="DQv6GNRCPMj").one()
    assert row.likes is None
    assert row.shares == 4
    sparse = db.query(ContentCandidate).filter_by(external_id="noMetricsReel").one()
    assert sparse.views is None
    assert sparse.likes is None
    assert sparse.shares is None


def test_second_run_is_duplicate_but_new_observation(db: Session):
    items = json.loads((FIXTURES / "tiktok_apify.json").read_text(encoding="utf-8"))
    first = dict(items[0])
    later = dict(items[0])
    later["playCount"] = 400000
    run_acquisition(db, "tiktok", client=FakeApify([first]), cfg=CFG)
    run = run_acquisition(db, "tiktok", client=FakeApify([later]), cfg=CFG)
    assert run.items_new == 0
    assert run.items_duplicate == 1
    row = db.query(ContentCandidate).one()
    assert row.views == 400000
    assert db.query(CandidateObservation).count() == 2


def test_dry_run_does_not_call_apify(db: Session):
    client = FakeApify([])
    run = run_acquisition(db, "tiktok", dry_run=True, client=client, cfg=CFG)
    assert run.status == "dry_run"
    assert client.calls == []
    assert db.query(ContentCandidate).count() == 0
    assert (run.run_metadata_json or {}).get("actor_input", {}).get("hashtags") == ["pov"]


def test_missing_token_records_config_error(db: Session, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "trendforge.acquisition.service.get_settings",
        lambda: type("S", (), {"has_apify": False})(),
    )
    with pytest.raises(MissingTokenError):
        run_acquisition(db, "tiktok", cfg=CFG)
    row = db.query(AcquisitionRun).one()
    assert row.status == "config_error"


def test_actor_failure_status(db: Session):
    from trendforge.acquisition.errors import AcquisitionError

    with pytest.raises(AcquisitionError, match="FAILED"):
        run_acquisition(db, "tiktok", client=FakeApify([], status="FAILED"), cfg=CFG)
    row = db.query(AcquisitionRun).one()
    assert row.status == "failed"
    assert row.apify_run_id == "apify-run-1"
