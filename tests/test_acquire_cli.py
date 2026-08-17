from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_script():
    path = ROOT / "scripts" / "acquire.py"
    spec = importlib.util.spec_from_file_location("acquire_cli", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_cli_requires_source():
    mod = _load_script()
    with pytest.raises(SystemExit):
        mod.parse_args([])


def test_cli_tiktok_limit_and_dry_run():
    mod = _load_script()
    args = mod.parse_args(["--source", "tiktok", "--limit", "25", "--dry-run"])
    assert args.source == "tiktok"
    assert args.limit == 25
    assert args.dry_run is True
    assert args.mode == "default"


def test_cli_emerging_mode():
    mod = _load_script()
    args = mod.parse_args(["--source", "tiktok", "--mode", "emerging", "--limit", "40"])
    assert args.mode == "emerging"
    assert args.limit == 40
    with pytest.raises(SystemExit):
        mod.parse_args(["--source", "instagram", "--mode", "emerging"])


def test_cli_profile_flags():
    mod = _load_script()
    args = mod.parse_args(
        ["--source", "tiktok", "--profile", "tiktok_trending", "--limit", "25", "--dry-run"]
    )
    assert args.profile == "tiktok_trending"
    assert args.limit == 25
    with pytest.raises(SystemExit):
        mod.parse_args(["--source", "instagram", "--profile", "tiktok_trending"])
    args = mod.parse_args(
        ["--source", "instagram", "--profile", "instagram_creator_reels", "--limit", "25"]
    )
    assert args.profile == "instagram_creator_reels"
    mod = _load_script()
    args = mod.parse_args(["--source", "instagram", "--limit", "10"])
    assert args.source == "instagram"
    assert args.dry_run is False


def test_cli_rejects_youtube_and_zero():
    mod = _load_script()
    with pytest.raises(SystemExit):
        mod.parse_args(["--source", "youtube"])
    with pytest.raises(SystemExit):
        mod.parse_args(["--source", "tiktok", "--limit", "0"])
