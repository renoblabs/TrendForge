from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from trendforge.db import get_session_factory, init_db
from trendforge.discovery.instagram import run_instagram_observation
from trendforge.discovery.pipeline import run_observation_refresh
from trendforge.discovery.tiktok import run_tiktok_observation, select_tiktok_observe_candidates
from trendforge.discovery.youtube import YouTubeDiscoveryProvider
from trendforge.models import ContentCandidate


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh stored candidate observations. Does not start scheduled crawls."
    )
    parser.add_argument(
        "--source",
        required=True,
        choices=["tiktok", "instagram", "youtube"],
        help="Platform to snapshot. YouTube uses the official API; TikTok/Instagram use Apify URL refresh.",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="Optional acquisition profile filter (TikTok/Instagram), e.g. tiktok_fresh_search",
    )
    parser.add_argument("--limit", type=int, default=20, metavar="N")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List candidates that would be observed without calling Apify or YouTube.",
    )
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be a positive integer")
    return args


def _print_rows(rows: list[ContentCandidate]) -> None:
    print(f"observe candidates={len(rows)}")
    for row in rows[:20]:
        obs_note = f"id={row.id} {row.platform} views={row.views} {row.url}"
        print(obs_note.encode("ascii", "replace").decode("ascii"))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    init_db()
    db = get_session_factory()()
    try:
        if args.dry_run:
            if args.source == "tiktok":
                rows = select_tiktok_observe_candidates(
                    db, limit=args.limit, profile=args.profile
                )
            elif args.source == "instagram":
                q = db.query(ContentCandidate).filter(
                    ContentCandidate.platform == "instagram",
                    ContentCandidate.url.isnot(None),
                )
                rows = q.order_by(ContentCandidate.discovered_at.asc()).all()
                if args.profile:
                    rows = [
                        row
                        for row in rows
                        if args.profile in (row.acquisition_profiles or [])
                    ]
                rows = rows[: args.limit]
            else:
                rows = (
                    db.query(ContentCandidate)
                    .filter(
                        ContentCandidate.platform == "youtube",
                        ContentCandidate.url.isnot(None),
                    )
                    .order_by(ContentCandidate.discovered_at.asc())
                    .limit(args.limit)
                    .all()
                )
            _print_rows(rows)
            return 0

        if args.source == "youtube":
            run = run_observation_refresh(db, provider=YouTubeDiscoveryProvider())
        elif args.source == "tiktok":
            run = run_tiktok_observation(
                db, limit=args.limit, profile=args.profile
            )
        else:
            run = run_instagram_observation(db)
        print(
            f"Observe run #{run.id} kind={run.kind} candidates={run.candidates_found} "
            f"observations={run.observations_written} errors={run.api_errors or []}"
        )
        return 0 if not run.api_errors else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
