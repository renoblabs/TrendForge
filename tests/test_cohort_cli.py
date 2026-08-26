from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_script():
    path = ROOT / "scripts" / "cohorts.py"
    spec = importlib.util.spec_from_file_location("cohorts_cli", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_exact_observation_command_shape():
    args = _load_script().parse_args(
        ["observe", "--cohort-id", "3", "--milestone", "T1"]
    )
    assert args.command == "observe"
    assert args.cohort_id == 3
    assert args.milestone == "T1"
    assert not hasattr(args, "limit")


def test_start_is_restricted_to_validated_profile_and_positive_limit():
    mod = _load_script()
    args = mod.parse_args(
        [
            "start",
            "--source",
            "tiktok",
            "--profile",
            "tiktok_fresh_search",
            "--limit",
            "25",
        ]
    )
    assert args.limit == 25
    with pytest.raises(SystemExit):
        mod.parse_args(
            [
                "start",
                "--source",
                "tiktok",
                "--profile",
                "tiktok_fresh_search",
                "--limit",
                "0",
            ]
        )


def test_backfill_and_analysis_subcommands():
    mod = _load_script()
    assert mod.parse_args(["backfill"]).command == "backfill"
    args = mod.parse_args(["analyze", "--cohort-id", "1"])
    assert args.command == "analyze"
    assert args.cohort_id == 1
