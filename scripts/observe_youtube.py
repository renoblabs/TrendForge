from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from trendforge.db import get_session_factory, init_db
from trendforge.discovery.pipeline import run_observation_refresh
from trendforge.discovery.youtube import YouTubeDiscoveryProvider


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh YouTube Shorts observations")
    parser.parse_args()

    init_db()
    db = get_session_factory()()
    try:
        run = run_observation_refresh(db, provider=YouTubeDiscoveryProvider())
        print(
            f"Observe run #{run.id}: candidates={run.candidates_found} "
            f"observations={run.observations_written} errors={run.api_errors or []}"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
