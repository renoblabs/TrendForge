from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from trendforge.db import get_session_factory, init_db
from trendforge.discovery.pipeline import run_high_signal_analysis


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Promote and analyze high-signal YouTube discoveries (not every Short)."
    )
    parser.add_argument(
        "--live-analysis",
        action="store_true",
        help="Use OpenRouter when OPENROUTER_API_KEY is set; re-analyze stub/outdated results",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    init_db()
    db = get_session_factory()()
    try:
        stats = run_high_signal_analysis(db, live=args.live_analysis)
        print(f"prompt version: {stats.prompt_version}")
        print(f"eligible candidates: {stats.eligible}")
        print(f"candidates analyzed: {stats.analyzed}")
        print(f"candidates skipped: {stats.skipped}")
        print(f"promoted this run: {stats.promoted}")
        print(f"stub analyses refreshed: {stats.stub_refreshed}")
        print(f"live analyses appended: {stats.live_appended}")
        if stats.skipped_reason:
            print(stats.skipped_reason)
    finally:
        db.close()


if __name__ == "__main__":
    main()
