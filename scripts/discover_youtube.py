from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from trendforge.db import get_session_factory, init_db
from trendforge.discovery.pipeline import format_discovery_diagnostics, run_discovery
from trendforge.discovery.youtube import YouTubeDiscoveryProvider


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover recent YouTube Shorts")
    parser.add_argument("--no-promote", action="store_true", help="Skip promotion/analysis")
    parser.add_argument("--live-analysis", action="store_true", help="Use OpenRouter if configured")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Max candidates to collect and persist this run",
    )
    parser.add_argument(
        "--broad",
        action="store_true",
        help="Non-semantic mode: recent Shorts ranked by viewCount, not topic queries",
    )
    parser.add_argument(
        "--profile",
        default=None,
        metavar="NAME",
        help="Discovery profile (broad defaults to north_america_english)",
    )
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be a positive integer")
    return args


def main() -> None:
    args = parse_args()

    init_db()
    db = get_session_factory()()
    try:
        run = run_discovery(
            db,
            provider=YouTubeDiscoveryProvider(),
            promote=not args.no_promote,
            force_stub_analysis=not args.live_analysis,
            limit=args.limit,
            mode="broad" if args.broad else "topics",
            profile_name=args.profile,
        )
        print(
            f"Discovery run #{run.id}: found={run.candidates_found} "
            f"new={run.new_candidates} dupes={run.duplicates} "
            f"promoted={run.promoted_candidates} errors={run.api_errors or []}"
        )
        print(format_discovery_diagnostics(run))
    finally:
        db.close()


if __name__ == "__main__":
    main()
