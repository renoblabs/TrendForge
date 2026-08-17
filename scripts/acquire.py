from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from trendforge.acquisition.errors import AcquisitionError, MissingTokenError
from trendforge.acquisition.service import run_acquisition
from trendforge.db import get_session_factory, init_db

PROFILES = [
    "tiktok_trending",
    "tiktok_fresh_search",
    "tiktok_hashtag",
    "tiktok_emerging",
    "instagram_creator_reels",
    "instagram_hashtag",
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Acquire TikTok or Instagram samples via Apify and normalize into TrendForge."
    )
    parser.add_argument(
        "--source",
        required=True,
        choices=["tiktok", "instagram"],
        help="Acquisition source (YouTube stays on the official API)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Max items to retrieve (clamped by config/data_sources.json, typically 10–100)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show Actor ID and input without calling Apify",
    )
    parser.add_argument(
        "--mode",
        default="default",
        choices=["default", "emerging"],
        help="legacy: emerging maps to tiktok_emerging. Prefer --profile.",
    )
    parser.add_argument(
        "--profile",
        default=None,
        choices=PROFILES,
        help="Sampling profile (tiktok_trending, tiktok_fresh_search, instagram_creator_reels, ...)",
    )
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be a positive integer")
    profile = args.profile
    if args.source == "instagram" and args.mode == "emerging" and not profile:
        parser.error("emerging mode is TikTok-only")
    if profile:
        if profile.startswith("tiktok_") and args.source != "tiktok":
            parser.error(f"{profile} is a TikTok profile")
        if profile.startswith("instagram_") and args.source != "instagram":
            parser.error(f"{profile} is an Instagram profile")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    init_db()
    db = get_session_factory()()
    try:
        try:
            run = run_acquisition(
                db,
                args.source,
                limit=args.limit,
                dry_run=args.dry_run,
                mode=args.mode,
                profile=args.profile,
            )
        except MissingTokenError as exc:
            print(f"config error: {exc}", file=sys.stderr)
            return 2
        except AcquisitionError as exc:
            print(f"acquisition failed: {exc}", file=sys.stderr)
            return 1

        meta = dict(run.run_metadata_json or {})
        cost = f"{run.actual_cost} {run.currency}" if run.actual_cost is not None else "unknown"
        print(
            f"Acquisition run #{run.id} source={run.source} provider={run.provider} "
            f"profile={run.profile or meta.get('profile')} status={run.status} actor={run.actor_id}"
        )
        print(
            f"found={run.items_found} new={run.items_new} dupes={run.items_duplicate} "
            f"rejected={run.items_rejected} cost={cost}"
        )
        quality = meta.get("sampling_quality") or {}
        if quality:
            print(
                "sampling_quality "
                f"recent={quality.get('recent_rate')} short={quality.get('short_rate')} "
                f"english={quality.get('english_rate')} unknown_lang={quality.get('unknown_language_rate')} "
                f"na={quality.get('north_america_signal_rate')} "
                f"new={quality.get('new_candidate_rate')} dup={quality.get('duplicate_rate')}"
            )
        pop = meta.get("population") or {}
        if pop.get("discovery_yield") is not None or pop.get("recentness_yield") is not None:
            print(
                "yields "
                f"discovery={pop.get('discovery_yield')} "
                f"recentness={pop.get('recentness_yield')} "
                f"high_signal={pop.get('high_signal_yield')}"
            )
        reasons = meta.get("reject_reasons") or {}
        if reasons:
            print("reject_reasons=" + " ".join(f"{k}={v}" for k, v in reasons.items()))
        qy = meta.get("query_yield") or {}
        if qy:
            parts = [
                f"{name}:raw={row.get('raw')} new={row.get('new')} yield={row.get('new_rate')}"
                for name, row in qy.items()
            ]
            print("query_yield " + " ".join(parts))
        baselines = meta.get("creator_baselines") or {}
        if baselines.get("creators_processed"):
            print(
                "creator_baselines "
                f"processed={baselines.get('creators_processed')} "
                f"with_baseline={baselines.get('creators_with_baseline')} "
                f"lift_reels={baselines.get('reels_with_creator_lift')} "
                f"median_lift={baselines.get('median_creator_lift')}"
            )
        if run.apify_run_id:
            print(f"apify_run_id={run.apify_run_id} dataset_id={run.apify_dataset_id}")
        if args.dry_run:
            print(f"actor_input={meta.get('actor_input')}")
        sample = meta.get("quality_sample") or []
        if sample:
            print("sample:")
            for row in sample[:8]:
                line = (
                    f"  {row.get('platform')} {row.get('views')} views "
                    f"{(row.get('title') or '')[:60]!r} {row.get('url')}"
                )
                print(line.encode("ascii", "replace").decode("ascii"))
        return 0 if run.status in {"succeeded", "dry_run"} else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
