from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from trendforge.db import get_session_factory, init_db
from trendforge.generation.agent import apply_result_file
from trendforge.models import GenerationJob


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply an agent result.json to a TrendForge generation job. Does not call Comfy."
    )
    parser.add_argument("--job-id", type=int, required=True)
    parser.add_argument(
        "--apply-result",
        action="store_true",
        help="Register result.json written by a Cursor/Claude agent that used comfy-cloud MCP.",
    )
    args = parser.parse_args()
    if not args.apply_result:
        raise SystemExit("FastAPI/this script does not run Comfy. Use --apply-result after the agent writes result.json.")
    init_db()
    db = get_session_factory()()
    try:
        job = db.get(GenerationJob, args.job_id)
        if job is None:
            raise SystemExit(f"job {args.job_id} not found")
        apply_result_file(db, job)
        db.refresh(job)
        print(f"status={job.status} error={job.error_message or ''} assets={job.output_asset_ids}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
