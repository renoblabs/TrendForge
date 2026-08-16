from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_script():
    path = ROOT / "scripts" / "discover_youtube.py"
    spec = importlib.util.spec_from_file_location("discover_youtube_cli", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_cli_limit_and_existing_flags():
    mod = _load_script()
    args = mod.parse_args(["--limit", "10", "--no-promote", "--live-analysis"])
    assert args.limit == 10
    assert args.no_promote is True
    assert args.live_analysis is True


def test_cli_default_limit_is_none():
    mod = _load_script()
    args = mod.parse_args([])
    assert args.limit is None
    assert args.no_promote is False
    assert args.live_analysis is False


def test_cli_rejects_zero_and_negative_limit():
    mod = _load_script()
    with pytest.raises(SystemExit):
        mod.parse_args(["--limit", "0"])
    with pytest.raises(SystemExit):
        mod.parse_args(["--limit", "-3"])


def test_cli_rejects_non_integer_limit():
    mod = _load_script()
    with pytest.raises(SystemExit):
        mod.parse_args(["--limit", "ten"])


def test_cli_broad_flag():
    mod = _load_script()
    args = mod.parse_args(["--broad", "--limit", "10", "--no-promote"])
    assert args.broad is True
    assert args.limit == 10
    assert args.no_promote is True
    default = mod.parse_args([])
    assert default.broad is False


def test_cli_profile_flag():
    mod = _load_script()
    args = mod.parse_args(
        ["--broad", "--profile", "north_america_english", "--limit", "10", "--no-promote"]
    )
    assert args.broad is True
    assert args.profile == "north_america_english"
    default = mod.parse_args(["--broad", "--limit", "10", "--no-promote"])
    assert default.profile is None
