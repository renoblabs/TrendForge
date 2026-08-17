from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from trendforge.config import load_discovery_config
from trendforge.db import get_session_factory, init_db
from trendforge.discovery.gather import run_gathering_jobs
from trendforge.discovery.provider import DiscoveryError
from trendforge.discovery.schedule import gathering_status, schedule_config


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run YouTube / TikTok / Instagram gather jobs on config/discovery.json intervals"
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Keep running; sleep between checks (default check_every_seconds=60)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run due jobs once and exit (default if --loop is omitted)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run discover and observe even if they are not due",
    )
    parser.add_argument(
        "--job",
        choices=(
            "discover",
            "observe",
            "both",
            "tiktok",
            "discover_tiktok",
            "observe_tiktok",
            "instagram",
            "discover_instagram",
            "observe_instagram",
        ),
        default=None,
        help="Run a specific job now (implies --force for that job)",
    )
    return parser.parse_args(argv)


def _print_status(status: dict) -> None:
    sched = status["schedule"]
    print(
        f"schedule enabled={sched['enabled']} "
        f"discover_every={sched['discover_every_minutes']}m "
        f"observe_every={sched['observe_every_minutes']}m "
        f"mode={sched['mode']}"
    )
    for name in (
        "discover",
        "observe",
        "discover_tiktok",
        "observe_tiktok",
        "discover_instagram",
        "observe_instagram",
    ):
        job = status.get(name)
        if not job:
            continue
        last = job["last_started_at"].isoformat() if job["last_started_at"] else "never"
        print(f"  {name}: last={last} due={job['due']}")


def _print_runs(ran: list) -> None:
    if not ran:
        print("nothing due")
        return
    for name, run in ran:
        print(
            f"{name} run #{run.id} kind={run.kind} "
            f"found={run.candidates_found} new={run.new_candidates} "
            f"obs={run.observations_written} errors={run.api_errors or []}"
        )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    init_db()
    SessionLocal = get_session_factory()
    cfg = load_discovery_config()
    sched = schedule_config(cfg)
    jobs = None
    force = bool(args.force)
    if args.job == "both":
        jobs = ["discover", "observe"]
        force = True
    elif args.job == "tiktok":
        jobs = ["discover_tiktok", "observe_tiktok"]
        force = True
    elif args.job == "instagram":
        jobs = ["discover_instagram", "observe_instagram"]
        force = True
    elif args.job:
        jobs = [args.job]
        force = True

    def tick(use_force: bool, use_jobs: list[str] | None) -> None:
        db = SessionLocal()
        try:
            status = gathering_status(db, cfg=cfg)
            _print_status(status)
            ran = run_gathering_jobs(db, jobs=use_jobs, force=use_force, cfg=cfg)
            _print_runs(ran)
        finally:
            db.close()

    try:
        tick(force, jobs)
        if not args.loop:
            return 0
        interval = sched["check_every_seconds"]
        print(f"looping every {interval}s (Ctrl+C to stop)")
        while True:
            time.sleep(interval)
            tick(False, None)
    except DiscoveryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("stopped")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
