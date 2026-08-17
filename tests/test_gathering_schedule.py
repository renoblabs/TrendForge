from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from trendforge.db import get_session_factory, init_db
from trendforge.discovery.gather import run_gathering_jobs
from trendforge.discovery.provider import DiscoveryError
from trendforge.discovery.schedule import gathering_status, jobs_due, schedule_config
from trendforge.models import DiscoveryRun

import pytest


NOW = datetime(2026, 8, 15, 18, 0, tzinfo=timezone.utc)


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "sched.db"
    init_db(path)
    session = get_session_factory(path)()
    yield session
    session.close()


def test_schedule_defaults():
    sched = schedule_config({})
    assert sched["discover_every_minutes"] == 1440
    assert sched["observe_every_minutes"] == 1440
    assert sched["enabled"] is True


def test_due_when_never_run(db: Session):
    status = gathering_status(db, now=NOW, cfg={"schedule": {"enabled": True}})
    assert status["discover"]["due"] is True
    assert status["observe"]["due"] is True
    assert jobs_due(status) == ["discover", "observe"]


def test_not_due_inside_interval(db: Session):
    db.add(DiscoveryRun(kind="discover", started_at=NOW - timedelta(minutes=10)))
    db.add(DiscoveryRun(kind="observe", started_at=NOW - timedelta(minutes=10)))
    db.commit()
    status = gathering_status(
        db,
        now=NOW,
        cfg={"schedule": {"discover_every_minutes": 360, "observe_every_minutes": 90}},
    )
    assert status["discover"]["due"] is False
    assert status["observe"]["due"] is False
    assert jobs_due(status) == []
    assert jobs_due(status, force=True) == ["discover", "observe"]


def test_observe_due_independently(db: Session):
    db.add(DiscoveryRun(kind="discover", started_at=NOW - timedelta(minutes=30)))
    db.add(DiscoveryRun(kind="observe", started_at=NOW - timedelta(minutes=100)))
    db.commit()
    status = gathering_status(
        db,
        now=NOW,
        cfg={"schedule": {"discover_every_minutes": 360, "observe_every_minutes": 90}},
    )
    assert status["discover"]["due"] is False
    assert status["observe"]["due"] is True
    assert jobs_due(status) == ["observe"]


def test_disabled_schedule_is_never_due(db: Session):
    status = gathering_status(db, now=NOW, cfg={"schedule": {"enabled": False}})
    assert jobs_due(status) == []


def test_gather_requires_youtube_key_without_provider(db: Session, monkeypatch):
    monkeypatch.setattr(
        "trendforge.discovery.gather.get_settings",
        lambda: type("S", (), {"has_youtube": False})(),
    )
    with pytest.raises(DiscoveryError):
        run_gathering_jobs(db, jobs=["observe"], force=True)


def test_gather_runs_injected_provider(db: Session, monkeypatch):
    calls = []

    class _Prov:
        def get_videos(self, ids):
            calls.append(("videos", ids))
            return []

        def get_channels(self, ids):
            return {}

    monkeypatch.setattr(
        "trendforge.discovery.gather.get_settings",
        lambda: type("S", (), {"has_youtube": True})(),
    )
    ran = run_gathering_jobs(db, jobs=["observe"], force=True, provider=_Prov())
    assert [name for name, _ in ran] == ["observe"]
    assert calls[0][0] == "videos"
