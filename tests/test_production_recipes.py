from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from trendforge.app import app
from trendforge.db import get_db, get_session_factory, init_db
from trendforge.models import (
    ApprovalStatus,
    ContentAsset,
    ProductionApprovalDecision,
    ProductionApprovalEvent,
    ProductionApprovalGate,
    ProductionApprovalScope,
    ProductionAttempt,
    ProductionAttemptAsset,
    ProductionAttemptStatus,
    ProductionBenchmarkStatus,
    ProductionRecipe,
    ProductionRecipeShot,
    ProductionRecipeStatus,
    ProductionRecipeVersionStatus,
    ProductionSpec,
    ProductionStage,
)
from trendforge.production.recipes import (
    AttemptResultError,
    CapabilitySnapshotError,
    FrozenRecipeVersionError,
    ProductionRecipeError,
    aggregate_attempts,
    apply_attempt_result,
    attach_capability_snapshot,
    benchmark_shortlist,
    benchmark_status,
    canonical_checksum,
    clone_recipe_version,
    create_attempt,
    create_recipe_from_spec,
    exclude_benchmark_candidate,
    freeze_recipe_version,
    import_capability_snapshot,
    latest_approval,
    link_attempt_asset,
    recipe_manifest,
    record_approval_event,
    retry_attempt,
    sha256_file,
    select_quality_benchmark,
    stage_state,
    update_visual_bible,
)


@pytest.fixture()
def db(tmp_path: Path) -> Session:
    path = tmp_path / "recipes.db"
    init_db(path)
    session = get_session_factory(path)()
    yield session
    session.close()


def _spec_payload(*, title: str = "Synthetic fixture concept", duration: float = 15.0) -> dict:
    shots = []
    for number in range(1, 6):
        start = (number - 1) * 3.0
        shots.append(
            {
                "shot_number": number,
                "title": f"Shot {number}",
                "purpose": f"Beat {number}",
                "start_seconds": start,
                "end_seconds": start + 3.0,
                "duration_seconds": 3.0,
                "camera": "locked wide" if number < 4 else "locked close",
                "framing": "9:16 medium",
                "subject": "unbranded geometric test object",
                "action": f"action {number}",
                "environment": "same dark kitchen",
                "audio": f"sfx {number}",
                "continuity_notes": f"LED state {number}",
                "generation_notes": "reference conditioned",
            }
        )
    return {
        "title": title,
        "format_family": "impossible-pov-micro-story",
        "specific_format": "object POV",
        "primary_mechanic": "IMPOSSIBLE_POV",
        "secondary_mechanics": ["MICRO_STORY"],
        "format_hypothesis": "Personified object plus visual payoff.",
        "hook": "A plain shape unfolds into an unexpected color pattern.",
        "premise": "Synthetic geometry validates shot ordering and lineage.",
        "target_audience": "short-form comedy viewers",
        "duration_seconds": duration,
        "aspect_ratio": "9:16",
        "story_beats": [],
        "shots": shots,
        "characters": [{"name": "Detector", "appearance": "red LED, white shell"}],
        "environment": "one dark kitchen",
        "props": ["toaster", "toast"],
        "dialogue": [],
        "narration": "It was supposed to be a quiet night.",
        "audio_direction": "Original narration and alarm SFX",
        "sound_effects": ["beep", "toast pop"],
        "music": "none",
        "visual_style": "cinematic practical comedy",
        "camera_direction": "two locked setups",
        "continuity_requirements": ["same detector", "same LED state progression"],
        "negative_constraints": ["no logos", "no extra rooms"],
        "required_assets": [{"asset_type": "object_reference", "mandatory": True}],
        "qa_checklist": [{"check": "identity stable", "required": True}],
        "originality_notes": "Original object-POV execution.",
        "ip_considerations": "No branded appliance designs.",
        "comfyui_recommendations": [],
    }


def _add_spec(db: Session, *, title: str = "Synthetic fixture concept", duration: float = 15.0) -> ProductionSpec:
    row = ProductionSpec(
        format_family="impossible-pov-micro-story",
        source_idea_identifier="0",
        prompt_version="production-spec-v1",
        analyzer="test",
        model="test",
        spec_json=_spec_payload(title=title, duration=duration),
    )
    db.add(row)
    db.commit()
    return row


def _recipe(db: Session):
    spec = _add_spec(db)
    recipe, version, created = create_recipe_from_spec(db, spec.id)
    assert created
    shots = db.query(ProductionRecipeShot).filter_by(recipe_version_id=version.id).order_by(ProductionRecipeShot.shot_number).all()
    return spec, recipe, version, shots


def _succeed(db: Session, version_id: int, stage: ProductionStage, *, shot_id: int | None = None, cost: float | None = None, currency: str | None = None) -> ProductionAttempt:
    row = create_attempt(db, version_id, stage, shot_id=shot_id)
    row.status = ProductionAttemptStatus.SUCCEEDED
    row.reported_cost = cost
    row.currency = currency
    row.elapsed_seconds = 2.5
    db.commit()
    return row


def _approve_all(db: Session, version_id: int, shots: list[ProductionRecipeShot]) -> None:
    record_approval_event(
        db,
        version_id,
        gate="VISUAL_BIBLE_APPROVAL",
        scope_type="VISUAL_BIBLE",
        decision="APPROVE",
        reviewer="human",
    )
    keyframes = [_succeed(db, version_id, ProductionStage.KEYFRAMES, shot_id=shot.id) for shot in shots]
    for shot, attempt in zip(shots, keyframes, strict=True):
        record_approval_event(
            db, version_id, gate="KEYFRAME_APPROVAL", scope_type="SHOT_KEYFRAME",
            scope_id=shot.id, decision="APPROVE", reviewer="human", approved_attempt_id=attempt.id,
        )
    motions = [_succeed(db, version_id, ProductionStage.MOTION, shot_id=shot.id) for shot in shots]
    for shot, attempt in zip(shots, motions, strict=True):
        record_approval_event(
            db, version_id, gate="MOTION_APPROVAL", scope_type="SHOT_MOTION",
            scope_id=shot.id, decision="APPROVE", reviewer="human", approved_attempt_id=attempt.id,
        )
    rough = _succeed(db, version_id, ProductionStage.ASSEMBLY)
    record_approval_event(
        db, version_id, gate="ROUGH_CUT_APPROVAL", scope_type="ROUGH_CUT",
        decision="APPROVE", reviewer="human", approved_attempt_id=rough.id,
    )
    final = _succeed(db, version_id, ProductionStage.FINAL_QA)
    record_approval_event(
        db, version_id, gate="FINAL_RENDER_APPROVAL", scope_type="FINAL_RENDER",
        decision="APPROVE", reviewer="human", approved_attempt_id=final.id,
    )


def test_create_recipe_snapshot_order_clone_and_idempotency(db: Session):
    spec, recipe, version, shots = _recipe(db)
    assert recipe.current_version_id == version.id
    assert version.version_number == 1
    assert [shot.shot_number for shot in shots] == [1, 2, 3, 4, 5]
    assert sum(shot.duration_seconds for shot in shots) == 15
    original_snapshot = version.production_spec_snapshot_json
    spec.spec_json = {**spec.spec_json, "title": "Changed later"}
    db.commit()
    db.refresh(version)
    assert version.production_spec_snapshot_json == original_snapshot
    again_recipe, again_version, created = create_recipe_from_spec(db, spec.id)
    assert not created and again_recipe.id == recipe.id and again_version.id == version.id
    clone = clone_recipe_version(db, version.id)
    assert clone.version_number == 2
    assert clone.production_spec_snapshot_json == original_snapshot
    clone_shots = db.query(ProductionRecipeShot).filter_by(recipe_version_id=clone.id).order_by(ProductionRecipeShot.shot_number).all()
    assert [(s.start_seconds, s.end_seconds) for s in clone_shots] == [(s.start_seconds, s.end_seconds) for s in shots]
    with pytest.raises(IntegrityError):
        db.add(type(clone)(
            recipe_id=recipe.id, version_number=2, status=ProductionRecipeVersionStatus.DRAFT,
            source_production_spec_id=spec.id, production_spec_snapshot_json=original_snapshot,
            content_checksum="duplicate",
        ))
        db.commit()
    db.rollback()


def test_duration_budget_validation_rejects_overflow(db: Session):
    spec = _add_spec(db, duration=14)
    with pytest.raises(ProductionRecipeError, match="exceeds the duration budget"):
        create_recipe_from_spec(db, spec.id)


def test_freeze_rules_and_frozen_mutation_guards(db: Session):
    _, recipe, version, shots = _recipe(db)
    with pytest.raises(ProductionRecipeError, match="five approval gates"):
        freeze_recipe_version(db, version.id)
    _approve_all(db, version.id, shots)
    frozen = freeze_recipe_version(db, version.id)
    db.refresh(recipe)
    assert frozen.status == ProductionRecipeVersionStatus.FROZEN
    assert recipe.status == ProductionRecipeStatus.SUCCESSFUL
    with pytest.raises(FrozenRecipeVersionError):
        update_visual_bible(db, frozen.id, {"style": "changed"})
    frozen.visual_bible_json = {"style": "direct mutation"}
    with pytest.raises(ValueError, match="frozen"):
        db.commit()
    db.rollback()
    shot = db.query(ProductionRecipeShot).filter_by(recipe_version_id=frozen.id).first()
    shot.title = "direct shot mutation"
    with pytest.raises(ValueError, match="frozen"):
        db.commit()
    db.rollback()
    clone = clone_recipe_version(db, frozen.id)
    assert clone.version_number == 2 and clone.status == ProductionRecipeVersionStatus.DRAFT


def test_attempt_numbering_retry_history_and_aggregation(db: Session):
    _, _, version, shots = _recipe(db)
    first = create_attempt(db, version.id, "KEYFRAMES", shot_id=shots[0].id)
    first.status = ProductionAttemptStatus.FAILED
    first.reported_cost = 0.1
    first.currency = "USD"
    first.elapsed_seconds = 4
    db.commit()
    second = retry_attempt(db, first.id)
    second.status = ProductionAttemptStatus.REJECTED
    second.reported_cost = None
    second.currency = "USD"
    second.elapsed_seconds = None
    db.commit()
    third = retry_attempt(db, second.id)
    third.status = ProductionAttemptStatus.SUCCEEDED
    third.reported_cost = 0.2
    third.currency = "CAD"
    third.elapsed_seconds = 3
    db.commit()
    assert [first.attempt_number, second.attempt_number, third.attempt_number] == [1, 2, 3]
    summary = aggregate_attempts(db, version.id)
    assert summary["attempt_count"] == 3
    assert summary["failed_attempts"] == 1 and summary["rejected_attempts"] == 1
    assert summary["known_cost_total"] == 0.3
    assert summary["total_cost"] is None
    assert summary["cost_unknown_attempts"] == 1 and summary["mixed_currency"]
    assert db.get(ProductionAttempt, first.id).status == ProductionAttemptStatus.FAILED


def test_asset_checksum_lineage_and_legacy_compatibility(db: Session, tmp_path: Path):
    _, _, version, shots = _recipe(db)
    attempt = create_attempt(db, version.id, "KEYFRAMES", shot_id=shots[0].id)
    ref_file = tmp_path / "reference.bin"
    ref_file.write_bytes(b"reference")
    asset = ContentAsset(
        asset_type="image", file_reference=str(ref_file), approval_status=ApprovalStatus.DRAFT,
        source="legacy", sha256=sha256_file(ref_file),
    )
    db.add(asset)
    db.commit()
    link = link_attempt_asset(db, attempt.id, asset.id, "REFERENCE")
    assert link.asset_id == asset.id and link.role.value == "REFERENCE"
    assert db.query(ProductionAttemptAsset).count() == 1
    same = link_attempt_asset(db, attempt.id, asset.id, "REFERENCE")
    assert same.id == link.id and db.query(ProductionAttemptAsset).count() == 1
    legacy = ContentAsset(asset_type="video", file_reference=None, source="legacy")
    db.add(legacy)
    db.commit()
    assert legacy.sha256 is None and legacy.asset_role is None
    with pytest.raises(FileNotFoundError):
        sha256_file(tmp_path / "missing.bin")


def test_attempt_result_contract_success_failure_idempotency_and_rejections(db: Session, tmp_path: Path):
    _, _, version, shots = _recipe(db)
    output = tmp_path / "keyframe.png"
    output.write_bytes(b"not-generated-test-fixture")
    attempt = create_attempt(db, version.id, "KEYFRAMES", shot_id=shots[0].id, provider="comfy-cloud")
    payload = {
        "contract_version": "production-attempt-result-v1",
        "attempt_id": attempt.id,
        "status": "completed",
        "execution_target": "comfy_cloud",
        "local_comfy_used": False,
        "provider": "comfy-cloud",
        "agent": "cursor-agent",
        "cloud_job_id": "cloud-test-1",
        "workflow_id": "captured-not-hard-coded",
        "model": "reported-model",
        "parameters": {"seed": 7},
        "output_files": [{"path": str(output), "role": "KEYFRAME", "dimensions": {"width": 1080, "height": 1920}}],
        "cost": 0.03,
        "currency": "USD",
        "started_at": "2026-08-25T16:00:00Z",
        "completed_at": "2026-08-25T16:00:05Z",
        "qa": {"passed": True},
    }
    applied, assets, changed = apply_attempt_result(db, attempt.id, payload)
    assert changed and applied.status == ProductionAttemptStatus.SUCCEEDED
    assert applied.elapsed_seconds == 5 and assets[0].sha256 == sha256_file(output)
    assert assets[0].width == 1080 and assets[0].height == 1920
    _, same_assets, changed = apply_attempt_result(db, attempt.id, payload)
    assert not changed and same_assets[0].id == assets[0].id
    with pytest.raises(AttemptResultError, match="different applied result"):
        apply_attempt_result(db, attempt.id, {**payload, "cost": 0.04})

    local = create_attempt(db, version.id, "KEYFRAMES", shot_id=shots[1].id, provider="comfy-cloud")
    with pytest.raises(AttemptResultError, match="local Comfy"):
        apply_attempt_result(db, local.id, {**payload, "attempt_id": local.id, "local_comfy_used": True})
    missing = create_attempt(db, version.id, "KEYFRAMES", shot_id=shots[2].id, provider="comfy-cloud")
    with pytest.raises(AttemptResultError, match="output file missing"):
        apply_attempt_result(db, missing.id, {**payload, "attempt_id": missing.id, "output_files": [str(tmp_path / "absent.mp4")]})
    db.refresh(missing)
    assert missing.status == ProductionAttemptStatus.PLANNED
    assert not (missing.metadata_json or {}).get("result_checksum")
    failed = create_attempt(db, version.id, "KEYFRAMES", shot_id=shots[3].id, provider="comfy-cloud")
    failed_result = {
        "contract_version": "production-attempt-result-v1", "attempt_id": failed.id,
        "status": "failed", "execution_target": "comfy_cloud", "local_comfy_used": False,
        "error": {"code": "CLOUD_FAILED", "message": "reported failure"},
    }
    row, no_assets, _ = apply_attempt_result(db, failed.id, failed_result)
    assert row.status == ProductionAttemptStatus.FAILED and not no_assets


def test_approvals_are_append_only_reasoned_gated_and_invalidate_downstream(db: Session):
    _, _, version, shots = _recipe(db)
    early = _succeed(db, version.id, ProductionStage.KEYFRAMES, shot_id=shots[0].id)
    with pytest.raises(ProductionRecipeError, match="visual bible"):
        record_approval_event(
            db, version.id, gate="KEYFRAME_APPROVAL", scope_type="SHOT_KEYFRAME",
            scope_id=shots[0].id, decision="APPROVE", reviewer="human", approved_attempt_id=early.id,
        )
    with pytest.raises(ProductionRecipeError, match="reason"):
        record_approval_event(
            db, version.id, gate="VISUAL_BIBLE_APPROVAL", scope_type="VISUAL_BIBLE",
            decision="REJECT", reviewer="human",
        )
    visual = record_approval_event(
        db, version.id, gate="VISUAL_BIBLE_APPROVAL", scope_type="VISUAL_BIBLE",
        decision="APPROVE", reviewer="human",
    )
    visual.reason = "mutate"
    with pytest.raises(ValueError, match="immutable"):
        db.commit()
    db.rollback()
    for index, shot in enumerate(shots):
        attempt = early if index == 0 else _succeed(db, version.id, ProductionStage.KEYFRAMES, shot_id=shot.id)
        record_approval_event(
            db, version.id, gate="KEYFRAME_APPROVAL", scope_type="SHOT_KEYFRAME",
            scope_id=shot.id, decision="APPROVE", reviewer="human", approved_attempt_id=attempt.id,
        )
    motion = _succeed(db, version.id, ProductionStage.MOTION, shot_id=shots[0].id)
    record_approval_event(
        db, version.id, gate="MOTION_APPROVAL", scope_type="SHOT_MOTION",
        scope_id=shots[0].id, decision="APPROVE", reviewer="human", approved_attempt_id=motion.id,
    )
    replacement = _succeed(db, version.id, ProductionStage.KEYFRAMES, shot_id=shots[0].id)
    record_approval_event(
        db, version.id, gate="KEYFRAME_APPROVAL", scope_type="SHOT_KEYFRAME",
        scope_id=shots[0].id, decision="APPROVE", reviewer="human", approved_attempt_id=replacement.id,
    )
    latest_motion = latest_approval(
        db, version.id, ProductionApprovalGate.MOTION_APPROVAL,
        scope_type=ProductionApprovalScope.SHOT_MOTION, scope_id=shots[0].id,
    )
    assert latest_motion.decision == ProductionApprovalDecision.REVOKE
    assert (latest_motion.metadata_json or {}).get("automatic_invalidation") is True
    assert db.get(ProductionAttempt, early.id).status == ProductionAttemptStatus.SUPERSEDED
    assert stage_state(db, version.id)["current_stage"] == "MOTION"
    update_visual_bible(db, version.id, {"overall_style": "revised after review"})
    latest_visual = latest_approval(
        db, version.id, ProductionApprovalGate.VISUAL_BIBLE_APPROVAL,
        scope_type=ProductionApprovalScope.VISUAL_BIBLE,
    )
    assert latest_visual.decision == ProductionApprovalDecision.REVOKE
    assert stage_state(db, version.id)["current_stage"] == "VISUAL_BIBLE"


def test_capability_snapshot_contract_checksum_secret_rejection_and_linkage(db: Session):
    _, _, version, _ = _recipe(db)
    base = {
        "contract_version": "production-capability-snapshot-v1",
        "captured_at": "2026-08-25T12:00:00Z",
        "provider": "Comfy Cloud",
        "mcp_server": "comfy-cloud",
        "agent": "cursor-agent",
        "agent_version": "1",
        "raw_snapshot": {"tools": [{"name": "list_templates"}]},
        "normalized_capabilities": {"video": {"templates": []}},
    }
    reordered = {
        "normalized_capabilities": base["normalized_capabilities"],
        "raw_snapshot": base["raw_snapshot"],
        "agent_version": "1", "agent": "cursor-agent", "mcp_server": "comfy-cloud",
        "provider": "Comfy Cloud", "captured_at": "2026-08-25T12:00:00Z",
        "contract_version": "production-capability-snapshot-v1",
    }
    assert canonical_checksum(base) == canonical_checksum(reordered)
    snapshot, created = import_capability_snapshot(db, base)
    same, created_again = import_capability_snapshot(db, reordered)
    assert created and not created_again and same.id == snapshot.id
    attach_capability_snapshot(db, version.id, snapshot.id)
    assert recipe_manifest(db, version.recipe_id, version.id)["version"]["capability_status"] == "CAPTURED"
    snapshot.notes = "change"
    with pytest.raises(ValueError, match="immutable"):
        db.commit()
    db.rollback()
    with pytest.raises(CapabilitySnapshotError, match="secret"):
        import_capability_snapshot(db, {**base, "api_key": "never-store-this"})
    with pytest.raises(CapabilitySnapshotError, match="required"):
        import_capability_snapshot(db, {"provider": "Comfy Cloud"})


def test_manifest_not_captured_and_secret_safe(db: Session):
    _, recipe, version, _ = _recipe(db)
    payload = recipe_manifest(db, recipe.id, version.id)
    text = str(payload).lower()
    assert payload["version"]["capability_status"] == "NOT_CAPTURED"
    assert "api_key" not in text and "bearer " not in text
    assert payload["stage_state"]["current_gate"] == "VISUAL_BIBLE_APPROVAL"


def test_benchmark_selection_is_explicit_generic_and_idempotent(db: Session):
    chosen = _add_spec(db, title="Synthetic candidate A")
    excluded = _add_spec(db, title="Synthetic candidate B")
    assert benchmark_status(db) == "AWAITING_USER_SELECTION"
    initial = benchmark_shortlist(db)
    assert initial["status"] == "AWAITING_USER_SELECTION"
    assert db.query(ProductionRecipe).count() == 0
    assert {row["benchmark_state"] for row in initial["rows"]} == {"READY_FOR_BENCHMARK_REVIEW"}

    decision, created = exclude_benchmark_candidate(
        db, excluded.id, reviewer="fixture-user", reason="Synthetic exclusion coverage"
    )
    assert created and decision.status == ProductionBenchmarkStatus.EXCLUDED_BY_USER
    excluded_state = next(row for row in benchmark_shortlist(db)["rows"] if row["spec"].id == excluded.id)
    assert excluded_state["benchmark_state"] == "EXCLUDED_BY_USER" and not excluded_state["can_select"]
    with pytest.raises(ProductionRecipeError, match="not available"):
        select_quality_benchmark(
            db, excluded.id, selected_by="fixture-user", reason="Must remain excluded"
        )

    with pytest.raises(ProductionRecipeError, match="reason"):
        select_quality_benchmark(db, chosen.id, selected_by="fixture-user", reason="")
    selection, recipe, version, created = select_quality_benchmark(
        db,
        chosen.id,
        selected_by="fixture-user",
        reason="Synthetic selection validates the explicit workflow",
    )
    assert created and selection.status == ProductionBenchmarkStatus.USER_SELECTED
    assert selection.recipe_id == recipe.id and version.version_number == 1
    assert benchmark_status(db) == "USER_SELECTED"
    assert db.query(ProductionRecipeShot).filter_by(recipe_version_id=version.id).count() == 5
    assert recipe_manifest(db, recipe.id, version.id)["benchmark_selection"]["selected_by"] == "fixture-user"
    same = select_quality_benchmark(
        db, chosen.id, selected_by="fixture-user", reason="Idempotent retry"
    )
    assert not same[-1] and same[0].id == selection.id and same[1].id == recipe.id
    selection.selection_reason = "forbidden mutation"
    with pytest.raises(ValueError, match="immutable"):
        db.commit()
    db.rollback()


def test_production_routes_empty_create_detail_forms_exports_and_regressions(tmp_path: Path):
    db_path = tmp_path / "routes.db"
    init_db(db_path)
    SessionLocal = get_session_factory(db_path)
    session = SessionLocal()
    spec = _add_spec(session)
    spec_id = spec.id
    session.close()

    def _override_db():
        route_db = SessionLocal()
        try:
            yield route_db
        finally:
            route_db.close()

    app.dependency_overrides[get_db] = _override_db
    try:
        with TestClient(app) as client:
            listed = client.get("/production/recipes")
            assert listed.status_code == 200 and "AWAITING_USER_SELECTION" in listed.text
            shortlist = client.get("/production/benchmarks")
            assert shortlist.status_code == 200
            assert "SELECT AS QUALITY BENCHMARK" in shortlist.text
            spec_page = client.get(f"/production/specs/{spec_id}")
            assert spec_page.status_code == 200 and "Review benchmark shortlist" in spec_page.text
            assert client.post(f"/production/specs/{spec_id}/recipes").status_code == 404
            made = client.post(
                f"/production/benchmarks/{spec_id}/select",
                data={"selected_by": "route-user", "reason": "Explicit route selection"},
                follow_redirects=False,
            )
            assert made.status_code == 303
            location = made.headers["location"]
            detail = client.get(location)
            assert detail.status_code == 200
            for phrase in ["Production stages", "Visual bible", "Shot matrix", "Approval history", "Awaiting agent execution"]:
                assert phrase in detail.text
            recipe_id = int(location.split("/")[3])
            version_id = int(location.split("/")[5])
            assert client.get(f"/production/recipes/{recipe_id}").status_code == 200
            assert client.get(f"/production/recipes/{recipe_id}/versions/{version_id}/export.json").status_code == 200
            assert client.get(f"/production/recipes/{recipe_id}/versions/{version_id}/export.md").status_code == 200
            assert client.get("/generation").status_code == 200
            assert client.get("/experiments").status_code == 200
    finally:
        app.dependency_overrides.clear()
