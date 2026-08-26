from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from trendforge.db import init_db
from trendforge.research.config import (
    build_config_snapshot,
    cohort_targets,
    composite_config_snapshot,
    config_drift,
    config_drift_status,
    config_fingerprint,
    format_toronto,
)


def _config() -> dict:
    return {
        "apify": {
            "api_token": "must-not-leak",
            "actors": {"tiktok_fresh_search": "clockworks/tiktok-scraper"},
        },
        "profiles": {
            "tiktok_fresh_search": {
                "source": "tiktok",
                "role": "fresh_search",
                "actor_key": "tiktok_fresh_search",
                "queries": ["AI", "comedy"],
                "window": "PAST_24_HOURS",
                "max_short_seconds": 60,
                "max_limit": 50,
                "limit_input_keys": ["resultsPerPage"],
                "actor_input": {
                    "searchQueries": ["AI", "comedy"],
                    "searchSection": "/video",
                    "videoSearchSorting": "LATEST",
                    "videoSearchDateFilter": "PAST_24_HOURS",
                    "apiKey": "nested-secret",
                },
            }
        },
        "sources": {
            "tiktok": {
                "provider": "apify",
                "default_limit": 25,
                "max_limit": 100,
                "max_short_seconds": 60,
            }
        },
        "audience": {
            "language": "en",
            "regions": ["US", "CA"],
            "keep_unknown_language": True,
        },
    }


def test_config_fingerprint_is_stable_across_dictionary_order():
    first = {"source": "tiktok", "nested": {"b": 2, "a": 1}, "queries": ["AI", "POV"]}
    second = {"queries": ["AI", "POV"], "nested": {"a": 1, "b": 2}, "source": "tiktok"}
    assert config_fingerprint(first) == config_fingerprint(second)


def test_snapshot_is_secret_safe_and_historical_copy_does_not_mutate():
    cfg = _config()
    snapshot = build_config_snapshot("tiktok", "tiktok_fresh_search", 25, cfg=cfg)
    frozen = deepcopy(snapshot)
    serialized = json.dumps(snapshot).lower()
    assert "must-not-leak" not in serialized
    assert "nested-secret" not in serialized
    assert "apikey" not in serialized
    cfg["profiles"]["tiktok_fresh_search"]["queries"].append("funny")
    assert snapshot == frozen


def test_config_drift_detects_live_profile_change():
    cfg = _config()
    snapshot = build_config_snapshot("tiktok", "tiktok_fresh_search", 25, cfg=cfg)
    fingerprint = config_fingerprint(snapshot)
    assert config_drift(snapshot, fingerprint, cfg=cfg) is False
    cfg["profiles"]["tiktok_fresh_search"]["queries"] = ["AI", "funny"]
    assert config_drift(snapshot, fingerprint, cfg=cfg) is True
    assert config_drift_status(snapshot, fingerprint, cfg=cfg) == "DRIFT"


def test_composite_snapshot_is_explicitly_not_comparable():
    first = build_config_snapshot("tiktok", "tiktok_fresh_search", 25, cfg=_config())
    second = deepcopy(first)
    second["queries"] = ["AI", "funny"]
    composite = composite_config_snapshot(
        [{"run_id": 18, "snapshot": first}, {"run_id": 22, "snapshot": second}]
    )
    assert composite["composite"] is True
    assert [row["run_id"] for row in composite["source_runs"]] == [18, 22]
    assert config_drift(composite, config_fingerprint(composite), cfg=_config()) is None
    assert config_drift_status(composite, cfg=_config()) == "NOT_COMPARABLE"


def test_timing_targets_and_toronto_display():
    t0 = datetime(2026, 8, 24, 21, 18, 48, tzinfo=timezone.utc)
    targets = cohort_targets(t0, "tiktok_fresh_search")
    assert targets["target_t1_window_start"] == datetime(
        2026, 8, 25, 0, 18, 48, tzinfo=timezone.utc
    )
    assert targets["target_t1_at"] == datetime(
        2026, 8, 25, 0, 48, 48, tzinfo=timezone.utc
    )
    assert targets["target_t1_window_end"] == datetime(
        2026, 8, 25, 1, 18, 48, tzinfo=timezone.utc
    )
    assert targets["target_t2_at"] == datetime(
        2026, 8, 31, 21, 18, 48, tzinfo=timezone.utc
    )
    assert format_toronto(t0) == "2026-08-24 17:18:48 EDT"


def test_toronto_display_treats_sqlite_naive_datetime_as_utc():
    naive_utc = datetime(2026, 12, 1, 17, 0, 0)
    assert format_toronto(naive_utc) == "2026-12-01 12:00:00 EST"


def test_init_db_completes_early_differential_table_schema(tmp_path: Path):
    path = tmp_path / "early-research.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE cohort_differential_analyses "
            "(id INTEGER PRIMARY KEY, cohort_id INTEGER NOT NULL, evidence_mode VARCHAR(64))"
        )
    init_db(path)
    with sqlite3.connect(path) as connection:
        names = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(cohort_differential_analyses)"
            ).fetchall()
        }
    assert {"evidence_available", "evidence_missing"}.issubset(names)
