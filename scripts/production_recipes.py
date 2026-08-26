from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from trendforge.db import get_session_factory, init_db
from trendforge.production.recipes import (
    ProductionRecipeError,
    apply_attempt_result,
    attach_capability_snapshot,
    attempt_handoff_manifest,
    clone_recipe_version,
    create_attempt,
    exclude_benchmark_candidate,
    freeze_recipe_version,
    import_capability_snapshot,
    recipe_manifest,
    recipe_manifest_markdown,
    select_quality_benchmark,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Manage quality-first production recipes and external-agent contracts. "
            "These commands never launch a generation provider."
        )
    )
    sub = parser.add_subparsers(dest="command", required=True)

    select = sub.add_parser(
        "select-benchmark",
        help="Explicitly user-select one ProductionSpec and create its recipe v1",
    )
    select.add_argument("--production-spec-id", type=int, required=True)
    select.add_argument("--selected-by", required=True)
    select.add_argument("--reason", required=True)

    exclude = sub.add_parser(
        "exclude-benchmark",
        help="Record an explicit user exclusion without modifying the legacy ProductionSpec",
    )
    exclude.add_argument("--production-spec-id", type=int, required=True)
    exclude.add_argument("--reviewer", required=True)
    exclude.add_argument("--reason", required=True)

    clone = sub.add_parser("clone-version", help="Clone a recipe version for further tuning")
    clone.add_argument("--version-id", type=int, required=True)

    freeze = sub.add_parser("freeze-version", help="Freeze a version after all five approvals")
    freeze.add_argument("--version-id", type=int, required=True)

    capabilities = sub.add_parser(
        "import-capabilities", help="Import a secret-safe native MCP capability snapshot"
    )
    capabilities.add_argument("--file", type=Path, required=True)
    capabilities.add_argument("--version-id", type=int)

    attempt = sub.add_parser(
        "create-attempt", help="Create a PLANNED attempt; no agent or Cloud job is launched"
    )
    attempt.add_argument("--version-id", type=int, required=True)
    attempt.add_argument("--stage", required=True)
    attempt.add_argument("--shot-id", type=int)
    attempt.add_argument("--provider")
    attempt.add_argument("--agent")
    attempt.add_argument("--model")
    attempt.add_argument("--template-id")
    attempt.add_argument("--workflow-id")

    handoff = sub.add_parser("export-attempt", help="Export one external-agent handoff manifest")
    handoff.add_argument("--attempt-id", type=int, required=True)
    handoff.add_argument("--output", type=Path)

    result = sub.add_parser("apply-attempt-result", help="Validate and import an agent result")
    result.add_argument("--attempt-id", type=int, required=True)
    result.add_argument("--result-file", type=Path, required=True)

    export = sub.add_parser("export", help="Export secret-safe recipe_manifest.json and .md")
    export.add_argument("--recipe-id", type=int, required=True)
    export.add_argument("--version-id", type=int)
    export.add_argument("--output-dir", type=Path, required=True)

    return parser.parse_args(argv)


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise ProductionRecipeError(f"JSON file not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductionRecipeError(f"invalid JSON file: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProductionRecipeError("JSON contract root must be an object")
    return payload


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    init_db()
    db = get_session_factory()()
    try:
        if args.command == "select-benchmark":
            selection, recipe, version, created = select_quality_benchmark(
                db,
                args.production_spec_id,
                selected_by=args.selected_by,
                reason=args.reason,
            )
            print(
                f"benchmark_status={selection.status.value} selection_id={selection.id} "
                f"recipe_id={recipe.id} version_id={version.id} "
                f"version={version.version_number} created={str(created).lower()}"
            )
            return 0

        if args.command == "exclude-benchmark":
            decision, created = exclude_benchmark_candidate(
                db,
                args.production_spec_id,
                reviewer=args.reviewer,
                reason=args.reason,
            )
            print(
                f"benchmark_status={decision.status.value} decision_id={decision.id} "
                f"production_spec_id={decision.production_spec_id} "
                f"created={str(created).lower()} recipe_created=false"
            )
            return 0

        if args.command == "clone-version":
            version = clone_recipe_version(db, args.version_id)
            print(
                f"recipe_id={version.recipe_id} version_id={version.id} "
                f"version={version.version_number} status={version.status.value}"
            )
            return 0

        if args.command == "freeze-version":
            version = freeze_recipe_version(db, args.version_id)
            print(
                f"recipe_id={version.recipe_id} version_id={version.id} "
                f"version={version.version_number} status={version.status.value}"
            )
            return 0

        if args.command == "import-capabilities":
            snapshot, created = import_capability_snapshot(db, _read_json(args.file))
            if args.version_id is not None:
                attach_capability_snapshot(db, args.version_id, snapshot.id)
            print(
                f"capability_snapshot_id={snapshot.id} checksum={snapshot.content_checksum} "
                f"created={str(created).lower()} linked_version_id={args.version_id or 'none'}"
            )
            return 0

        if args.command == "create-attempt":
            row = create_attempt(
                db,
                args.version_id,
                args.stage,
                shot_id=args.shot_id,
                provider=args.provider,
                agent=args.agent,
                model=args.model,
                template_id=args.template_id,
                workflow_id=args.workflow_id,
            )
            print(
                f"attempt_id={row.id} attempt_number={row.attempt_number} "
                f"status={row.status.value} execution=AWAITING_AGENT_EXECUTION"
            )
            return 0

        if args.command == "export-attempt":
            payload = attempt_handoff_manifest(db, args.attempt_id)
            text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(text, encoding="utf-8")
                print(f"attempt_manifest={args.output.resolve()}")
            else:
                print(text, end="")
            return 0

        if args.command == "apply-attempt-result":
            attempt, assets, applied = apply_attempt_result(
                db, args.attempt_id, _read_json(args.result_file)
            )
            print(
                f"attempt_id={attempt.id} status={attempt.status.value} "
                f"assets={len(assets)} applied={str(applied).lower()}"
            )
            return 0

        if args.command == "export":
            payload = recipe_manifest(db, args.recipe_id, args.version_id)
            args.output_dir.mkdir(parents=True, exist_ok=True)
            json_path = args.output_dir / "recipe_manifest.json"
            md_path = args.output_dir / "recipe_manifest.md"
            json_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            md_path.write_text(recipe_manifest_markdown(payload), encoding="utf-8")
            print(f"json={json_path.resolve()}")
            print(f"markdown={md_path.resolve()}")
            return 0

        raise ProductionRecipeError(f"unsupported command: {args.command}")
    except ProductionRecipeError as exc:
        db.rollback()
        print(f"production recipe error: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
