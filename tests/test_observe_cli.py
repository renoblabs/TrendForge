from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_script():
    path = ROOT / "scripts" / "observe.py"
    spec = importlib.util.spec_from_file_location("observe_cli", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_observe_cli_requires_source():
    mod = _load_script()
    with pytest.raises(SystemExit):
        mod.parse_args([])


def test_observe_cli_tiktok_dry_run_flags():
    mod = _load_script()
    args = mod.parse_args(
        ["--source", "tiktok", "--profile", "tiktok_fresh_search", "--limit", "15", "--dry-run"]
    )
    assert args.source == "tiktok"
    assert args.profile == "tiktok_fresh_search"
    assert args.limit == 15
    assert args.dry_run is True
    with pytest.raises(SystemExit):
        mod.parse_args(["--source", "tiktok", "--limit", "0"])
