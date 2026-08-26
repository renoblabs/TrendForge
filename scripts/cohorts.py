from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from trendforge.acquisition.errors import AcquisitionError, MissingTokenError
from trendforge.analysis.client import MissingAPIKeyError
from trendforge.db import get_session_factory, init_db
from trendforge.models import ResearchCohort, ResearchCohortMember
from trendforge.research.cohorts import (
    backfill_known_cohorts,
    cohort_detail_view,
    observe_cohort,
    start_cohort,
)
from trendforge.research.differential import (
    DifferentialAnalysisError,
    run_cohort_differential_analysis,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manage frozen longitudinal research cohorts without scheduling or LLM side effects."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("backfill", help="Idempotently link the known mature and #24 cohorts")

    start = sub.add_parser("start", help="Acquire one controlled sample and freeze its new members")
    start.add_argument("--source", required=True, choices=["tiktok"])
    start.add_argument("--profile", required=True, choices=["tiktok_fresh_search"])
    start.add_argument("--limit", type=int, default=25)

    observe = sub.add_parser("observe", help="Observe exactly one cohort milestone")
    observe.add_argument("--cohort-id", required=True, type=int)
    observe.add_argument("--milestone", required=True, choices=["T1", "T2", "t1", "t2"])

    analyze = sub.add_parser(
        "analyze", help="Append one outcome differential analysis to a mature cohort"
    )
    analyze.add_argument("--cohort-id", required=True, type=int)

    sub.add_parser("list", help="Print registered cohort timing and status")
    args = parser.parse_args(argv)
    if getattr(args, "limit", 1) < 1:
        parser.error("--limit must be a positive integer")
    return args


def _print_cohort(db, cohort: ResearchCohort) -> None:
    detail = cohort_detail_view(db, cohort.id)
    if detail is None:
        return
    view = detail["cohort"]
    timing = detail["timing"]
    print(
        f"cohort_id={cohort.id} key={cohort.cohort_key} name={cohort.name!r} "
        f"members={view['member_count']} status={cohort.status}"
    )
    print(
        f"T0={timing['t0_display']} T1={timing['t1_status']} "
        f"T2={timing['t2_status']} T2_target={timing['t2_target_display']}"
    )
    print(f"config_fingerprint={cohort.config_fingerprint} drift={view['config_drift']}")


def _print_acquisition(run) -> None:
    meta = dict(run.run_metadata_json or {})
    cost = f"{run.actual_cost} {run.currency or ''}" if run.actual_cost is not None else "unknown"
    print(
        f"acquisition_run_id={run.id} apify_run_id={run.apify_run_id} "
        f"dataset_id={run.apify_dataset_id} cost={cost}"
    )
    print(
        f"raw={run.items_found} new={run.items_new} duplicates={run.items_duplicate} "
        f"rejected={run.items_rejected}"
    )
    quality = meta.get("sampling_quality") or {}
    if quality:
        print("sampling_quality=" + json.dumps(quality, sort_keys=True))
    query_yield = meta.get("query_yield") or {}
    if query_yield:
        print("query_yield=" + json.dumps(query_yield, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    init_db()
    db = get_session_factory()()
    try:
        if args.command == "backfill":
            cohorts = backfill_known_cohorts(db)
            for cohort in cohorts:
                _print_cohort(db, cohort)
            return 0

        if args.command == "start":
            try:
                acquisition, cohort = start_cohort(
                    db,
                    source=args.source,
                    profile=args.profile,
                    limit=args.limit,
                )
            except MissingTokenError as exc:
                print(f"config error: {exc}", file=sys.stderr)
                return 2
            except AcquisitionError as exc:
                print(f"acquisition failed: {exc}", file=sys.stderr)
                return 1
            _print_acquisition(acquisition)
            if cohort is None:
                print("cohort_not_created=no_new_candidates")
                return 0
            _print_cohort(db, cohort)
            print(
                "next_command="
                f"python scripts/cohorts.py observe --cohort-id {cohort.id} --milestone T1"
            )
            return 0

        if args.command == "observe":
            try:
                result = observe_cohort(db, args.cohort_id, args.milestone)
            except (ValueError, AcquisitionError) as exc:
                print(f"cohort observation failed: {exc}", file=sys.stderr)
                return 1
            if result.no_op:
                print(
                    f"cohort_id={result.cohort.id} milestone={result.milestone} "
                    "no_op=already_resolved"
                )
                return 0
            run = result.run
            print(
                f"cohort_id={result.cohort.id} milestone={result.milestone} "
                f"discovery_run_id={run.id if run else None} requested={len(result.requested_candidate_ids)} "
                f"observed={len(result.observed_candidate_ids)} censored={len(result.censored_candidate_ids)} "
                f"unavailable={len(result.unavailable_candidate_ids)}"
            )
            if run:
                print(
                    f"apify_run_id={run.apify_run_id} dataset_id={run.apify_dataset_id} "
                    f"cost={run.actual_cost if run.actual_cost is not None else 'unknown'} "
                    f"currency={run.currency or 'unknown'}"
                )
            return 0 if not result.unavailable_candidate_ids else 1

        if args.command == "analyze":
            try:
                analysis = run_cohort_differential_analysis(db, args.cohort_id)
            except (MissingAPIKeyError, DifferentialAnalysisError, ValueError) as exc:
                print(f"differential analysis pending: {exc}", file=sys.stderr)
                return 2
            print(
                f"analysis_id={analysis.id} cohort_id={analysis.cohort_id} "
                f"prompt_version={analysis.prompt_version} provider={analysis.provider} "
                f"model={analysis.model} evidence_mode={analysis.evidence_mode}"
            )
            print(
                f"cost={analysis.actual_cost if analysis.actual_cost is not None else 'unknown'} "
                f"currency={analysis.currency or 'unknown'}"
            )
            return 0

        cohorts = db.query(ResearchCohort).order_by(ResearchCohort.t0_at.asc()).all()
        if not cohorts:
            print("No research cohorts registered.")
        for cohort in cohorts:
            _print_cohort(db, cohort)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
