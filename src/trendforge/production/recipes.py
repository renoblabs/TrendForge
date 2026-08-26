from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from trendforge.models import (
    ApprovalStatus,
    ContentAsset,
    FormatBrainstormSet,
    GenerationJob,
    ProductionApprovalDecision,
    ProductionApprovalEvent,
    ProductionApprovalGate,
    ProductionApprovalScope,
    ProductionAssetRole,
    ProductionAttempt,
    ProductionAttemptAsset,
    ProductionAttemptStatus,
    ProductionCapabilitySnapshot,
    ProductionCapabilityStatus,
    ProductionBenchmarkDecision,
    ProductionBenchmarkStatus,
    ProductionRecipe,
    ProductionRecipeShot,
    ProductionRecipeStatus,
    ProductionRecipeVersion,
    ProductionRecipeVersionStatus,
    ProductionShotStatus,
    ProductionSpec,
    ProductionStage,
    utcnow,
)

CAPABILITY_CONTRACT_VERSION = "production-capability-snapshot-v1"
ATTEMPT_RESULT_CONTRACT_VERSION = "production-attempt-result-v1"
RECIPE_MANIFEST_VERSION = "production-recipe-manifest-v1"
ATTEMPT_HANDOFF_VERSION = "production-attempt-handoff-v1"
BENCHMARK_SELECTION_KEY = "quality-benchmark-v1"

SECRET_KEY_PARTS = (
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
)
SECRET_VALUE_PATTERNS = (
    re.compile(r"\bBearer\s+\S+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"\bcomfyui-[A-Za-z0-9_-]{16,}", re.IGNORECASE),
)

GATE_SCOPE = {
    ProductionApprovalGate.VISUAL_BIBLE_APPROVAL: ProductionApprovalScope.VISUAL_BIBLE,
    ProductionApprovalGate.KEYFRAME_APPROVAL: ProductionApprovalScope.SHOT_KEYFRAME,
    ProductionApprovalGate.MOTION_APPROVAL: ProductionApprovalScope.SHOT_MOTION,
    ProductionApprovalGate.ROUGH_CUT_APPROVAL: ProductionApprovalScope.ROUGH_CUT,
    ProductionApprovalGate.FINAL_RENDER_APPROVAL: ProductionApprovalScope.FINAL_RENDER,
}

GATE_STAGE = {
    ProductionApprovalGate.VISUAL_BIBLE_APPROVAL: ProductionStage.VISUAL_BIBLE,
    ProductionApprovalGate.KEYFRAME_APPROVAL: ProductionStage.KEYFRAMES,
    ProductionApprovalGate.MOTION_APPROVAL: ProductionStage.MOTION,
    ProductionApprovalGate.ROUGH_CUT_APPROVAL: ProductionStage.ASSEMBLY,
    ProductionApprovalGate.FINAL_RENDER_APPROVAL: ProductionStage.FINAL_QA,
}

STAGE_ORDER = (
    ProductionStage.PREPARATION,
    ProductionStage.VISUAL_BIBLE,
    ProductionStage.KEYFRAMES,
    ProductionStage.MOTION,
    ProductionStage.AUDIO,
    ProductionStage.ASSEMBLY,
    ProductionStage.FINAL_QA,
    ProductionStage.COMPLETE,
)


class ProductionRecipeError(ValueError):
    pass


class FrozenRecipeVersionError(ProductionRecipeError):
    pass


class CapabilitySnapshotError(ProductionRecipeError):
    pass


class AttemptResultError(ProductionRecipeError):
    pass


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_checksum(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _secret_key(key: Any) -> bool:
    text = str(key).strip().lower().replace("-", "_")
    return any(part in text for part in SECRET_KEY_PARTS)


def secret_safe(value: Any, *, reject: bool = False) -> Any:
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            if _secret_key(key):
                if reject:
                    raise CapabilitySnapshotError(f"secret-like key rejected: {key}")
                output[str(key)] = "[redacted]"
            else:
                output[str(key)] = secret_safe(item, reject=reject)
        return output
    if isinstance(value, (list, tuple)):
        return [secret_safe(item, reject=reject) for item in value]
    if isinstance(value, str) and any(pattern.search(value) for pattern in SECRET_VALUE_PATTERNS):
        if reject:
            raise CapabilitySnapshotError("secret-like value rejected")
        return "[redacted]"
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return value[:140] or "production-recipe"


def _coerce_stage(value: ProductionStage | str) -> ProductionStage:
    try:
        return value if isinstance(value, ProductionStage) else ProductionStage(str(value).upper())
    except ValueError as exc:
        raise ProductionRecipeError(f"unknown production stage: {value}") from exc


def _coerce_attempt_status(value: ProductionAttemptStatus | str) -> ProductionAttemptStatus:
    try:
        return value if isinstance(value, ProductionAttemptStatus) else ProductionAttemptStatus(str(value).upper())
    except ValueError as exc:
        raise ProductionRecipeError(f"unknown attempt status: {value}") from exc


def _coerce_gate(value: ProductionApprovalGate | str) -> ProductionApprovalGate:
    try:
        return value if isinstance(value, ProductionApprovalGate) else ProductionApprovalGate(str(value).upper())
    except ValueError as exc:
        raise ProductionRecipeError(f"unknown approval gate: {value}") from exc


def _coerce_scope(value: ProductionApprovalScope | str) -> ProductionApprovalScope:
    try:
        return value if isinstance(value, ProductionApprovalScope) else ProductionApprovalScope(str(value).upper())
    except ValueError as exc:
        raise ProductionRecipeError(f"unknown approval scope: {value}") from exc


def _coerce_decision(value: ProductionApprovalDecision | str) -> ProductionApprovalDecision:
    try:
        return value if isinstance(value, ProductionApprovalDecision) else ProductionApprovalDecision(str(value).upper())
    except ValueError as exc:
        raise ProductionRecipeError(f"unknown approval decision: {value}") from exc


def _coerce_role(value: ProductionAssetRole | str) -> ProductionAssetRole:
    try:
        return value if isinstance(value, ProductionAssetRole) else ProductionAssetRole(str(value).upper())
    except ValueError as exc:
        raise ProductionRecipeError(f"unknown asset role: {value}") from exc


def assert_version_mutable(version: ProductionRecipeVersion) -> None:
    if version.status == ProductionRecipeVersionStatus.FROZEN or version.frozen_at is not None:
        raise FrozenRecipeVersionError("frozen recipe versions are immutable; clone a new version")


def _source_shots(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    raw = snapshot.get("shots")
    shots = [dict(item) for item in raw] if isinstance(raw, list) and all(isinstance(item, dict) for item in raw) else []
    shots.sort(key=lambda row: (int(row.get("shot_number") or 0), float(row.get("start_seconds") or 0)))
    return shots


def validate_shot_plan(snapshot: dict[str, Any]) -> dict[str, float]:
    shots = _source_shots(snapshot)
    if not shots:
        raise ProductionRecipeError("production spec must contain at least one shot")
    target = float(snapshot.get("duration_seconds") or 0)
    if target <= 0:
        raise ProductionRecipeError("production spec duration must be positive")
    seen: set[int] = set()
    total = 0.0
    previous_end = 0.0
    for expected, shot in enumerate(shots, start=1):
        number = int(shot.get("shot_number") or expected)
        if number in seen:
            raise ProductionRecipeError("shot numbers must be unique")
        seen.add(number)
        start = float(shot.get("start_seconds") or 0)
        end = float(shot.get("end_seconds") or 0)
        duration = float(shot.get("duration_seconds") or (end - start))
        if start < 0 or end <= start or duration <= 0:
            raise ProductionRecipeError(f"shot {number} has invalid timing")
        if abs(duration - (end - start)) > 0.05:
            raise ProductionRecipeError(f"shot {number} duration does not match its time range")
        if start + 0.05 < previous_end:
            raise ProductionRecipeError(f"shot {number} overlaps the preceding shot")
        if end > target + 0.05:
            raise ProductionRecipeError(f"shot {number} exceeds the duration budget")
        previous_end = end
        total += duration
    if total > target + 0.05:
        raise ProductionRecipeError("combined shot durations exceed the duration budget")
    return {"target_seconds": target, "shot_total_seconds": round(total, 6), "tolerance_seconds": 0.05}


def _initial_visual_bible(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "overall_style": snapshot.get("visual_style") or "",
        "lead_identity": (snapshot.get("characters") or [{}])[0] if snapshot.get("characters") else {},
        "appearance_rules": snapshot.get("characters") or [],
        "wardrobe_material_color_rules": [],
        "environment": snapshot.get("environment") or "",
        "props": snapshot.get("props") or [],
        "lighting": "",
        "camera_language": snapshot.get("camera_direction") or "",
        "aspect_ratio": snapshot.get("aspect_ratio") or "9:16",
        "continuity_rules": snapshot.get("continuity_requirements") or [],
        "prohibited_changes": snapshot.get("negative_constraints") or [],
        "reference_asset_ids": [],
    }


def _version_content(version: ProductionRecipeVersion, shots: list[ProductionRecipeShot] | None = None) -> dict[str, Any]:
    shot_rows = shots or []
    return {
        "production_spec_snapshot": version.production_spec_snapshot_json,
        "visual_bible": version.visual_bible_json,
        "duration_budget": version.duration_budget_json,
        "audio_plan": version.audio_plan_json,
        "caption_plan": version.caption_plan_json,
        "assembly_settings": version.assembly_settings_json,
        "capability_snapshot_id": version.capability_snapshot_id,
        "shots": [
            {
                "shot_number": row.shot_number,
                "stable_key": row.stable_key,
                "title": row.title,
                "purpose": row.purpose,
                "start_seconds": row.start_seconds,
                "end_seconds": row.end_seconds,
                "duration_seconds": row.duration_seconds,
                "camera": row.camera_json,
                "framing": row.framing_json,
                "subject": row.subject_json,
                "action": row.action_json,
                "environment": row.environment_json,
                "audio": row.audio_json,
                "generation_notes": row.generation_notes_json,
                "continuity_inputs": row.continuity_inputs_json,
                "continuity_outputs": row.continuity_outputs_json,
                "required_asset_roles": row.required_asset_roles_json,
                "negative_constraints": row.negative_constraints_json,
                "qa_requirements": row.qa_requirements_json,
            }
            for row in sorted(shot_rows, key=lambda item: (item.shot_number, item.id or 0))
        ],
    }


def refresh_version_checksum(db: Session, version: ProductionRecipeVersion) -> str:
    shots = (
        db.query(ProductionRecipeShot)
        .filter_by(recipe_version_id=version.id)
        .order_by(ProductionRecipeShot.shot_number, ProductionRecipeShot.id)
        .all()
    )
    version.content_checksum = canonical_checksum(_version_content(version, shots))
    version.updated_at = utcnow()
    return version.content_checksum


def _copy_shots(db: Session, version: ProductionRecipeVersion, snapshot: dict[str, Any]) -> list[ProductionRecipeShot]:
    rows: list[ProductionRecipeShot] = []
    global_negatives = list(snapshot.get("negative_constraints") or [])
    global_qa = list(snapshot.get("qa_checklist") or [])
    for index, raw in enumerate(_source_shots(snapshot), start=1):
        number = int(raw.get("shot_number") or index)
        start = float(raw.get("start_seconds") or 0)
        end = float(raw.get("end_seconds") or 0)
        duration = float(raw.get("duration_seconds") or (end - start))
        continuity = raw.get("continuity_notes")
        continuity_values = [continuity] if continuity else []
        row = ProductionRecipeShot(
            recipe_version_id=version.id,
            shot_number=number,
            stable_key=f"shot-{number:02d}",
            title=str(raw.get("title") or raw.get("purpose") or f"Shot {number}"),
            purpose=str(raw.get("purpose") or ""),
            start_seconds=start,
            end_seconds=end,
            duration_seconds=duration,
            camera_json=_json_clone(raw.get("camera") or {}),
            framing_json=_json_clone(raw.get("framing") or {}),
            subject_json=_json_clone(raw.get("subject") or {}),
            action_json=_json_clone(raw.get("action") or {}),
            environment_json=_json_clone(raw.get("environment") or {}),
            audio_json=_json_clone({"audio": raw.get("audio") or "", "dialogue": raw.get("dialogue") or ""}),
            generation_notes_json=_json_clone(raw.get("generation_notes") or {}),
            continuity_inputs_json=_json_clone(continuity_values),
            continuity_outputs_json=_json_clone(continuity_values),
            required_asset_roles_json=_json_clone(raw.get("required_asset_roles") or []),
            negative_constraints_json=_json_clone(global_negatives),
            qa_requirements_json=_json_clone(global_qa),
            status=ProductionShotStatus.PLANNED,
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def create_recipe_from_spec(
    db: Session,
    production_spec_id: int,
    *,
    name: str | None = None,
    notes: str | None = None,
    commit: bool = True,
) -> tuple[ProductionRecipe, ProductionRecipeVersion, bool]:
    spec = db.get(ProductionSpec, production_spec_id)
    if spec is None:
        raise ProductionRecipeError("production spec not found")
    existing = db.query(ProductionRecipe).filter_by(source_production_spec_id=spec.id).one_or_none()
    if existing is not None:
        version = db.get(ProductionRecipeVersion, existing.current_version_id) if existing.current_version_id else None
        if version is None:
            raise ProductionRecipeError("existing recipe has no current version")
        return existing, version, False
    snapshot = _json_clone(spec.spec_json or {})
    budget = validate_shot_plan(snapshot)
    title = str(name or snapshot.get("title") or f"Production recipe {spec.id}").strip()
    base_slug = _slug(title)
    slug = base_slug
    suffix = 2
    while db.query(ProductionRecipe).filter_by(slug=slug).first() is not None:
        slug = f"{base_slug}-{suffix}"
        suffix += 1
    recipe = ProductionRecipe(
        name=title,
        slug=slug,
        format_family=spec.format_family,
        status=ProductionRecipeStatus.DRAFT,
        source_production_spec_id=spec.id,
        notes=notes,
        metadata_json={"created_from": "production_spec", "source_prompt_version": spec.prompt_version},
    )
    db.add(recipe)
    db.flush()
    version = ProductionRecipeVersion(
        recipe_id=recipe.id,
        version_number=1,
        status=ProductionRecipeVersionStatus.DRAFT,
        source_production_spec_id=spec.id,
        production_spec_snapshot_json=snapshot,
        visual_bible_json=_initial_visual_bible(snapshot),
        duration_budget_json=budget,
        audio_plan_json={
            "dialogue": snapshot.get("dialogue") or [],
            "narration": snapshot.get("narration") or "",
            "audio_direction": snapshot.get("audio_direction") or "",
            "sound_effects": snapshot.get("sound_effects") or [],
            "music": snapshot.get("music") or "",
            "audio_priority": snapshot.get("audio_priority") or "",
        },
        caption_plan_json={"status": "NOT_PLANNED", "lines": []},
        assembly_settings_json={
            "tool": "ffmpeg",
            "aspect_ratio": snapshot.get("aspect_ratio") or "9:16",
            "target_duration_seconds": budget["target_seconds"],
            "transitions": "CUT",
            "final_encoding": "NOT_CONFIGURED",
        },
        capability_snapshot_id=None,
        recipe_metadata_json={"capability_status": "NOT_CAPTURED"},
        content_checksum="pending",
    )
    db.add(version)
    db.flush()
    shots = _copy_shots(db, version, snapshot)
    version.content_checksum = canonical_checksum(_version_content(version, shots))
    recipe.current_version_id = version.id
    if commit:
        db.commit()
        db.refresh(recipe)
        db.refresh(version)
    else:
        db.flush()
    return recipe, version, True


def clone_recipe_version(db: Session, version_id: int) -> ProductionRecipeVersion:
    source = db.get(ProductionRecipeVersion, version_id)
    if source is None:
        raise ProductionRecipeError("recipe version not found")
    recipe = db.get(ProductionRecipe, source.recipe_id)
    if recipe is None:
        raise ProductionRecipeError("recipe not found")
    next_number = int(
        db.query(func.max(ProductionRecipeVersion.version_number))
        .filter_by(recipe_id=recipe.id)
        .scalar()
        or 0
    ) + 1
    clone = ProductionRecipeVersion(
        recipe_id=recipe.id,
        version_number=next_number,
        status=ProductionRecipeVersionStatus.DRAFT,
        source_production_spec_id=source.source_production_spec_id,
        production_spec_snapshot_json=_json_clone(source.production_spec_snapshot_json),
        visual_bible_json=_json_clone(source.visual_bible_json or {}),
        duration_budget_json=_json_clone(source.duration_budget_json or {}),
        audio_plan_json=_json_clone(source.audio_plan_json or {}),
        caption_plan_json=_json_clone(source.caption_plan_json or {}),
        assembly_settings_json=_json_clone(source.assembly_settings_json or {}),
        capability_snapshot_id=source.capability_snapshot_id,
        recipe_metadata_json={
            **_json_clone(source.recipe_metadata_json or {}),
            "cloned_from_version_id": source.id,
        },
        content_checksum="pending",
    )
    db.add(clone)
    db.flush()
    source_shots = (
        db.query(ProductionRecipeShot)
        .filter_by(recipe_version_id=source.id)
        .order_by(ProductionRecipeShot.shot_number, ProductionRecipeShot.id)
        .all()
    )
    cloned_shots: list[ProductionRecipeShot] = []
    for row in source_shots:
        copied = ProductionRecipeShot(
            recipe_version_id=clone.id,
            shot_number=row.shot_number,
            stable_key=row.stable_key,
            title=row.title,
            purpose=row.purpose,
            start_seconds=row.start_seconds,
            end_seconds=row.end_seconds,
            duration_seconds=row.duration_seconds,
            camera_json=_json_clone(row.camera_json),
            framing_json=_json_clone(row.framing_json),
            subject_json=_json_clone(row.subject_json),
            action_json=_json_clone(row.action_json),
            environment_json=_json_clone(row.environment_json),
            audio_json=_json_clone(row.audio_json),
            generation_notes_json=_json_clone(row.generation_notes_json),
            continuity_inputs_json=_json_clone(row.continuity_inputs_json or []),
            continuity_outputs_json=_json_clone(row.continuity_outputs_json or []),
            required_asset_roles_json=_json_clone(row.required_asset_roles_json or []),
            negative_constraints_json=_json_clone(row.negative_constraints_json or []),
            qa_requirements_json=_json_clone(row.qa_requirements_json or []),
            status=ProductionShotStatus.PLANNED,
        )
        db.add(copied)
        cloned_shots.append(copied)
    db.flush()
    clone.content_checksum = canonical_checksum(_version_content(clone, cloned_shots))
    recipe.current_version_id = clone.id
    recipe.status = ProductionRecipeStatus.TUNING
    recipe.updated_at = utcnow()
    db.commit()
    db.refresh(clone)
    return clone


def update_visual_bible(db: Session, version_id: int, visual_bible: dict[str, Any]) -> ProductionRecipeVersion:
    version = db.get(ProductionRecipeVersion, version_id)
    if version is None:
        raise ProductionRecipeError("recipe version not found")
    assert_version_mutable(version)
    if not isinstance(visual_bible, dict):
        raise ProductionRecipeError("visual bible must be a JSON object")
    secret_safe(visual_bible, reject=True)
    if is_approved(db, version.id, ProductionApprovalGate.VISUAL_BIBLE_APPROVAL):
        reason = "Invalidated because the approved visual bible content changed"
        _invalidate_downstream(
            db,
            version,
            ProductionApprovalGate.VISUAL_BIBLE_APPROVAL,
            None,
            reason,
        )
        _append_approval(
            db,
            version=version,
            scope_type=ProductionApprovalScope.VISUAL_BIBLE,
            scope_id=None,
            gate=ProductionApprovalGate.VISUAL_BIBLE_APPROVAL,
            decision=ProductionApprovalDecision.REVOKE,
            reviewer="system",
            reason=reason,
            metadata={"automatic_invalidation": True},
        )
    version.visual_bible_json = _json_clone(visual_bible)
    refresh_version_checksum(db, version)
    db.commit()
    db.refresh(version)
    return version


def attach_capability_snapshot(
    db: Session, version_id: int, capability_snapshot_id: int | None
) -> ProductionRecipeVersion:
    version = db.get(ProductionRecipeVersion, version_id)
    if version is None:
        raise ProductionRecipeError("recipe version not found")
    assert_version_mutable(version)
    if capability_snapshot_id is not None and db.get(ProductionCapabilitySnapshot, capability_snapshot_id) is None:
        raise ProductionRecipeError("capability snapshot not found")
    version.capability_snapshot_id = capability_snapshot_id
    meta = dict(version.recipe_metadata_json or {})
    meta["capability_status"] = "CAPTURED" if capability_snapshot_id else "NOT_CAPTURED"
    version.recipe_metadata_json = meta
    refresh_version_checksum(db, version)
    db.commit()
    db.refresh(version)
    return version


def import_capability_snapshot(db: Session, payload: dict[str, Any]) -> tuple[ProductionCapabilitySnapshot, bool]:
    if not isinstance(payload, dict):
        raise CapabilitySnapshotError("capability snapshot must be a JSON object")
    version = str(payload.get("contract_version") or CAPABILITY_CONTRACT_VERSION)
    if version != CAPABILITY_CONTRACT_VERSION:
        raise CapabilitySnapshotError("unsupported capability snapshot contract version")
    provider = str(payload.get("provider") or "").strip()
    mcp_server = str(payload.get("mcp_server") or "").strip()
    raw = payload.get("raw_snapshot")
    normalized = payload.get("normalized_capabilities")
    if not provider or not mcp_server or not isinstance(raw, dict) or not isinstance(normalized, dict):
        raise CapabilitySnapshotError(
            "provider, mcp_server, raw_snapshot, and normalized_capabilities are required"
        )
    safe = secret_safe(payload, reject=True)
    checksum = canonical_checksum(safe)
    existing = db.query(ProductionCapabilitySnapshot).filter_by(content_checksum=checksum).one_or_none()
    if existing is not None:
        return existing, False
    captured = payload.get("captured_at")
    captured_at = _parse_datetime(captured) if captured else utcnow()
    row = ProductionCapabilitySnapshot(
        captured_at=captured_at,
        provider=provider,
        mcp_server=mcp_server,
        agent=str(payload.get("agent") or "").strip() or None,
        agent_version=str(payload.get("agent_version") or "").strip() or None,
        raw_snapshot_json=_json_clone(raw),
        normalized_capabilities_json=_json_clone(normalized),
        content_checksum=checksum,
        status=ProductionCapabilityStatus.CAPTURED,
        notes=str(payload.get("notes") or "").strip() or None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, True


def create_attempt(
    db: Session,
    version_id: int,
    stage: ProductionStage | str,
    *,
    shot_id: int | None = None,
    generation_job_id: int | None = None,
    provider: str | None = None,
    agent: str | None = None,
    model: str | None = None,
    template_id: str | None = None,
    workflow_id: str | None = None,
    workflow_version: str | None = None,
    parameters: dict[str, Any] | None = None,
    prompt_components: dict[str, Any] | None = None,
    negative_constraints: list[Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> ProductionAttempt:
    version = db.get(ProductionRecipeVersion, version_id)
    if version is None:
        raise ProductionRecipeError("recipe version not found")
    assert_version_mutable(version)
    stage_value = _coerce_stage(stage)
    shot = db.get(ProductionRecipeShot, shot_id) if shot_id is not None else None
    if shot_id is not None and (shot is None or shot.recipe_version_id != version.id):
        raise ProductionRecipeError("shot does not belong to recipe version")
    if stage_value in {ProductionStage.KEYFRAMES, ProductionStage.MOTION} and shot is None:
        raise ProductionRecipeError(f"{stage_value.value} attempts require a shot")
    if generation_job_id is not None and db.get(GenerationJob, generation_job_id) is None:
        raise ProductionRecipeError("generation job not found")
    query = db.query(func.max(ProductionAttempt.attempt_number)).filter(
        ProductionAttempt.recipe_version_id == version.id,
        ProductionAttempt.stage == stage_value,
    )
    query = query.filter(ProductionAttempt.shot_id == shot_id) if shot_id is not None else query.filter(ProductionAttempt.shot_id.is_(None))
    attempt_number = int(query.scalar() or 0) + 1
    row = ProductionAttempt(
        recipe_id=version.recipe_id,
        recipe_version_id=version.id,
        generation_job_id=generation_job_id,
        shot_id=shot_id,
        stage=stage_value,
        attempt_number=attempt_number,
        status=ProductionAttemptStatus.PLANNED,
        provider=provider,
        agent=agent,
        model=model,
        template_id=template_id,
        workflow_id=workflow_id,
        workflow_version=workflow_version,
        parameters_json=_json_clone(parameters or {}),
        prompt_components_json=_json_clone(prompt_components or {}),
        negative_constraints_json=_json_clone(negative_constraints or []),
        metadata_json={"execution_state": "AWAITING_AGENT_EXECUTION", **_json_clone(metadata or {})},
    )
    db.add(row)
    if shot is not None:
        shot.status = ProductionShotStatus.IN_PROGRESS
    db.commit()
    db.refresh(row)
    return row


def retry_attempt(db: Session, attempt_id: int) -> ProductionAttempt:
    source = db.get(ProductionAttempt, attempt_id)
    if source is None:
        raise ProductionRecipeError("attempt not found")
    return create_attempt(
        db,
        source.recipe_version_id,
        source.stage,
        shot_id=source.shot_id,
        generation_job_id=source.generation_job_id,
        provider=source.provider,
        agent=source.agent,
        model=source.model,
        template_id=source.template_id,
        workflow_id=source.workflow_id,
        workflow_version=source.workflow_version,
        parameters=source.parameters_json or {},
        prompt_components=source.prompt_components_json or {},
        negative_constraints=source.negative_constraints_json or [],
        metadata={"retry_of_attempt_id": source.id},
    )


def link_attempt_asset(
    db: Session,
    attempt_id: int,
    asset_id: int,
    role: ProductionAssetRole | str,
    *,
    ordinal: int = 0,
    metadata: dict[str, Any] | None = None,
    commit: bool = True,
) -> ProductionAttemptAsset:
    attempt = db.get(ProductionAttempt, attempt_id)
    asset = db.get(ContentAsset, asset_id)
    if attempt is None or asset is None:
        raise ProductionRecipeError("attempt and asset must exist")
    role_value = _coerce_role(role)
    existing = (
        db.query(ProductionAttemptAsset)
        .filter_by(attempt_id=attempt.id, asset_id=asset.id, role=role_value)
        .one_or_none()
    )
    if existing is not None:
        return existing
    row = ProductionAttemptAsset(
        attempt_id=attempt.id,
        asset_id=asset.id,
        role=role_value,
        ordinal=max(0, int(ordinal)),
        metadata_json=_json_clone(metadata or {}),
    )
    db.add(row)
    if not asset.asset_role:
        asset.asset_role = role_value.value
    if commit:
        db.commit()
        db.refresh(row)
    else:
        db.flush()
    return row


def latest_approval(
    db: Session,
    version_id: int,
    gate: ProductionApprovalGate | str,
    *,
    scope_type: ProductionApprovalScope | str | None = None,
    scope_id: int | None = None,
) -> ProductionApprovalEvent | None:
    gate_value = _coerce_gate(gate)
    scope_value = _coerce_scope(scope_type) if scope_type is not None else GATE_SCOPE[gate_value]
    query = db.query(ProductionApprovalEvent).filter_by(
        recipe_version_id=version_id,
        gate=gate_value,
        scope_type=scope_value,
    )
    query = query.filter(ProductionApprovalEvent.scope_id == scope_id) if scope_id is not None else query.filter(ProductionApprovalEvent.scope_id.is_(None))
    return query.order_by(ProductionApprovalEvent.created_at.desc(), ProductionApprovalEvent.id.desc()).first()


def is_approved(
    db: Session,
    version_id: int,
    gate: ProductionApprovalGate | str,
    *,
    scope_type: ProductionApprovalScope | str | None = None,
    scope_id: int | None = None,
) -> bool:
    row = latest_approval(db, version_id, gate, scope_type=scope_type, scope_id=scope_id)
    return row is not None and row.decision == ProductionApprovalDecision.APPROVE


def _shots_for_version(db: Session, version_id: int) -> list[ProductionRecipeShot]:
    return (
        db.query(ProductionRecipeShot)
        .filter_by(recipe_version_id=version_id)
        .order_by(ProductionRecipeShot.shot_number, ProductionRecipeShot.id)
        .all()
    )


def _all_shots_approved(db: Session, version_id: int, gate: ProductionApprovalGate) -> bool:
    shots = _shots_for_version(db, version_id)
    scope = GATE_SCOPE[gate]
    return bool(shots) and all(is_approved(db, version_id, gate, scope_type=scope, scope_id=shot.id) for shot in shots)


def stage_state(db: Session, version_id: int) -> dict[str, Any]:
    version = db.get(ProductionRecipeVersion, version_id)
    if version is None:
        raise ProductionRecipeError("recipe version not found")
    visual = is_approved(db, version.id, ProductionApprovalGate.VISUAL_BIBLE_APPROVAL)
    keyframes = _all_shots_approved(db, version.id, ProductionApprovalGate.KEYFRAME_APPROVAL)
    motion = _all_shots_approved(db, version.id, ProductionApprovalGate.MOTION_APPROVAL)
    rough = is_approved(db, version.id, ProductionApprovalGate.ROUGH_CUT_APPROVAL)
    final = is_approved(db, version.id, ProductionApprovalGate.FINAL_RENDER_APPROVAL)
    if not visual:
        current_stage, current_gate = ProductionStage.VISUAL_BIBLE, ProductionApprovalGate.VISUAL_BIBLE_APPROVAL
    elif not keyframes:
        current_stage, current_gate = ProductionStage.KEYFRAMES, ProductionApprovalGate.KEYFRAME_APPROVAL
    elif not motion:
        current_stage, current_gate = ProductionStage.MOTION, ProductionApprovalGate.MOTION_APPROVAL
    elif not rough:
        current_stage, current_gate = ProductionStage.ASSEMBLY, ProductionApprovalGate.ROUGH_CUT_APPROVAL
    elif not final:
        current_stage, current_gate = ProductionStage.FINAL_QA, ProductionApprovalGate.FINAL_RENDER_APPROVAL
    else:
        current_stage, current_gate = ProductionStage.COMPLETE, None
    return {
        "current_stage": current_stage.value,
        "current_gate": current_gate.value if current_gate else None,
        "visual_bible_approved": visual,
        "keyframes_approved": keyframes,
        "motion_approved": motion,
        "rough_cut_approved": rough,
        "final_render_approved": final,
    }


def _append_approval(
    db: Session,
    *,
    version: ProductionRecipeVersion,
    scope_type: ProductionApprovalScope,
    scope_id: int | None,
    gate: ProductionApprovalGate,
    decision: ProductionApprovalDecision,
    reviewer: str,
    reason: str | None,
    generation_job_id: int | None = None,
    approved_asset_id: int | None = None,
    approved_attempt_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> ProductionApprovalEvent:
    row = ProductionApprovalEvent(
        recipe_id=version.recipe_id,
        recipe_version_id=version.id,
        generation_job_id=generation_job_id,
        scope_type=scope_type,
        scope_id=scope_id,
        gate=gate,
        decision=decision,
        reviewer=reviewer,
        reason=reason,
        approved_asset_id=approved_asset_id,
        approved_attempt_id=approved_attempt_id,
        metadata_json=_json_clone(metadata or {}),
    )
    db.add(row)
    db.flush()
    return row


def _invalidate_if_approved(
    db: Session,
    version: ProductionRecipeVersion,
    gate: ProductionApprovalGate,
    scope: ProductionApprovalScope,
    scope_id: int | None,
    reason: str,
) -> None:
    if is_approved(db, version.id, gate, scope_type=scope, scope_id=scope_id):
        _append_approval(
            db,
            version=version,
            scope_type=scope,
            scope_id=scope_id,
            gate=gate,
            decision=ProductionApprovalDecision.REVOKE,
            reviewer="system",
            reason=reason,
            metadata={"automatic_invalidation": True},
        )


def _invalidate_downstream(
    db: Session,
    version: ProductionRecipeVersion,
    gate: ProductionApprovalGate,
    scope_id: int | None,
    reason: str,
) -> None:
    shots = _shots_for_version(db, version.id)
    if gate == ProductionApprovalGate.VISUAL_BIBLE_APPROVAL:
        for shot in shots:
            _invalidate_if_approved(db, version, ProductionApprovalGate.KEYFRAME_APPROVAL, ProductionApprovalScope.SHOT_KEYFRAME, shot.id, reason)
            _invalidate_if_approved(db, version, ProductionApprovalGate.MOTION_APPROVAL, ProductionApprovalScope.SHOT_MOTION, shot.id, reason)
    elif gate == ProductionApprovalGate.KEYFRAME_APPROVAL and scope_id is not None:
        _invalidate_if_approved(db, version, ProductionApprovalGate.MOTION_APPROVAL, ProductionApprovalScope.SHOT_MOTION, scope_id, reason)
    if gate in {
        ProductionApprovalGate.VISUAL_BIBLE_APPROVAL,
        ProductionApprovalGate.KEYFRAME_APPROVAL,
        ProductionApprovalGate.MOTION_APPROVAL,
    }:
        _invalidate_if_approved(db, version, ProductionApprovalGate.ROUGH_CUT_APPROVAL, ProductionApprovalScope.ROUGH_CUT, None, reason)
        _invalidate_if_approved(db, version, ProductionApprovalGate.FINAL_RENDER_APPROVAL, ProductionApprovalScope.FINAL_RENDER, None, reason)
    elif gate == ProductionApprovalGate.ROUGH_CUT_APPROVAL:
        _invalidate_if_approved(db, version, ProductionApprovalGate.FINAL_RENDER_APPROVAL, ProductionApprovalScope.FINAL_RENDER, None, reason)


def _validate_gate_prerequisite(
    db: Session,
    version: ProductionRecipeVersion,
    gate: ProductionApprovalGate,
    scope_id: int | None,
) -> None:
    if gate == ProductionApprovalGate.KEYFRAME_APPROVAL and not is_approved(
        db, version.id, ProductionApprovalGate.VISUAL_BIBLE_APPROVAL
    ):
        raise ProductionRecipeError("visual bible approval is required first")
    if gate == ProductionApprovalGate.MOTION_APPROVAL:
        if scope_id is None or not _all_shots_approved(
            db, version.id, ProductionApprovalGate.KEYFRAME_APPROVAL
        ):
            raise ProductionRecipeError("all shot keyframes must be approved first")
    if gate == ProductionApprovalGate.ROUGH_CUT_APPROVAL and not _all_shots_approved(
        db, version.id, ProductionApprovalGate.MOTION_APPROVAL
    ):
        raise ProductionRecipeError("all shot motion outputs must be approved first")
    if gate == ProductionApprovalGate.FINAL_RENDER_APPROVAL and not is_approved(
        db, version.id, ProductionApprovalGate.ROUGH_CUT_APPROVAL
    ):
        raise ProductionRecipeError("rough cut approval is required first")


def record_approval_event(
    db: Session,
    version_id: int,
    *,
    gate: ProductionApprovalGate | str,
    scope_type: ProductionApprovalScope | str,
    decision: ProductionApprovalDecision | str,
    reviewer: str,
    reason: str | None = None,
    scope_id: int | None = None,
    generation_job_id: int | None = None,
    approved_asset_id: int | None = None,
    approved_attempt_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> ProductionApprovalEvent:
    version = db.get(ProductionRecipeVersion, version_id)
    if version is None:
        raise ProductionRecipeError("recipe version not found")
    assert_version_mutable(version)
    gate_value = _coerce_gate(gate)
    scope_value = _coerce_scope(scope_type)
    decision_value = _coerce_decision(decision)
    reviewer_value = str(reviewer or "").strip()
    reason_value = str(reason or "").strip() or None
    if not reviewer_value:
        raise ProductionRecipeError("reviewer is required")
    if decision_value != ProductionApprovalDecision.APPROVE and not reason_value:
        raise ProductionRecipeError("a reason is required for reject, request changes, or revoke")
    if GATE_SCOPE[gate_value] != scope_value:
        raise ProductionRecipeError("approval scope does not match gate")
    if scope_value in {ProductionApprovalScope.SHOT_KEYFRAME, ProductionApprovalScope.SHOT_MOTION}:
        shot = db.get(ProductionRecipeShot, scope_id) if scope_id is not None else None
        if shot is None or shot.recipe_version_id != version.id:
            raise ProductionRecipeError("approval shot does not belong to recipe version")
    elif scope_id is not None:
        raise ProductionRecipeError("recipe-level approval scopes must not have a scope ID")
    attempt = db.get(ProductionAttempt, approved_attempt_id) if approved_attempt_id else None
    if approved_attempt_id and (attempt is None or attempt.recipe_version_id != version.id):
        raise ProductionRecipeError("approved attempt does not belong to recipe version")
    if attempt and scope_id is not None and attempt.shot_id != scope_id:
        raise ProductionRecipeError("approved attempt does not belong to approval shot")
    if decision_value == ProductionApprovalDecision.APPROVE and gate_value != ProductionApprovalGate.VISUAL_BIBLE_APPROVAL:
        if attempt is None:
            raise ProductionRecipeError("output approval requires a stored production attempt")
        if attempt.stage != GATE_STAGE[gate_value]:
            raise ProductionRecipeError("approved attempt stage does not match approval gate")
        if attempt.status not in {
            ProductionAttemptStatus.SUCCEEDED,
            ProductionAttemptStatus.APPROVED,
        }:
            raise ProductionRecipeError("only a succeeded attempt can be approved")
    if approved_asset_id and db.get(ContentAsset, approved_asset_id) is None:
        raise ProductionRecipeError("approved asset not found")
    previous = latest_approval(db, version.id, gate_value, scope_type=scope_value, scope_id=scope_id)
    if decision_value == ProductionApprovalDecision.APPROVE:
        _validate_gate_prerequisite(db, version, gate_value, scope_id)
        if attempt is not None:
            prior_attempts = db.query(ProductionAttempt).filter(
                ProductionAttempt.recipe_version_id == version.id,
                ProductionAttempt.stage == GATE_STAGE[gate_value],
                ProductionAttempt.shot_id == attempt.shot_id,
                ProductionAttempt.status == ProductionAttemptStatus.APPROVED,
                ProductionAttempt.id != attempt.id,
            ).all()
            for old in prior_attempts:
                old.status = ProductionAttemptStatus.SUPERSEDED
            attempt.status = ProductionAttemptStatus.APPROVED
        if (
            previous
            and previous.decision == ProductionApprovalDecision.APPROVE
            and (
                previous.approved_attempt_id != approved_attempt_id
                or previous.approved_asset_id != approved_asset_id
            )
        ):
            _invalidate_downstream(
                db,
                version,
                gate_value,
                scope_id,
                f"Invalidated by replacement approval at {gate_value.value}",
            )
    else:
        if attempt is not None and decision_value in {
            ProductionApprovalDecision.REJECT,
            ProductionApprovalDecision.REQUEST_CHANGES,
        }:
            attempt.status = ProductionAttemptStatus.REJECTED
        _invalidate_downstream(
            db,
            version,
            gate_value,
            scope_id,
            f"Invalidated by {decision_value.value} at {gate_value.value}",
        )
    if scope_id is not None:
        shot = db.get(ProductionRecipeShot, scope_id)
        if shot is not None:
            shot.status = (
                ProductionShotStatus.APPROVED
                if decision_value == ProductionApprovalDecision.APPROVE
                else ProductionShotStatus.REJECTED
            )
    row = _append_approval(
        db,
        version=version,
        scope_type=scope_value,
        scope_id=scope_id,
        gate=gate_value,
        decision=decision_value,
        reviewer=reviewer_value,
        reason=reason_value,
        generation_job_id=generation_job_id,
        approved_asset_id=approved_asset_id,
        approved_attempt_id=approved_attempt_id,
        metadata=metadata,
    )
    if version.status == ProductionRecipeVersionStatus.DRAFT:
        version.status = ProductionRecipeVersionStatus.ACTIVE
    recipe = db.get(ProductionRecipe, version.recipe_id)
    if recipe and recipe.status == ProductionRecipeStatus.DRAFT:
        recipe.status = ProductionRecipeStatus.TUNING
    state = stage_state(db, version.id)
    for job in db.query(GenerationJob).filter_by(recipe_version_id=version.id).all():
        old_stage = job.current_stage
        job.current_stage = state["current_stage"]
        job.current_gate = state["current_gate"]
        if old_stage != job.current_stage:
            job.last_stage_transition_at = utcnow()
    db.commit()
    db.refresh(row)
    return row


def freeze_recipe_version(db: Session, version_id: int) -> ProductionRecipeVersion:
    version = db.get(ProductionRecipeVersion, version_id)
    if version is None:
        raise ProductionRecipeError("recipe version not found")
    assert_version_mutable(version)
    state = stage_state(db, version.id)
    if state["current_stage"] != ProductionStage.COMPLETE.value:
        raise ProductionRecipeError("all five approval gates must be complete before freezing")
    refresh_version_checksum(db, version)
    version.status = ProductionRecipeVersionStatus.FROZEN
    version.frozen_at = utcnow()
    recipe = db.get(ProductionRecipe, version.recipe_id)
    if recipe is None:
        raise ProductionRecipeError("recipe not found")
    recipe.current_version_id = version.id
    recipe.status = ProductionRecipeStatus.SUCCESSFUL
    recipe.updated_at = utcnow()
    db.commit()
    db.refresh(version)
    return version


def aggregate_attempts(db: Session, version_id: int) -> dict[str, Any]:
    attempts = db.query(ProductionAttempt).filter_by(recipe_version_id=version_id).all()
    counts = {status.value.lower(): 0 for status in ProductionAttemptStatus}
    stage_names = {
        ProductionStage.KEYFRAMES: "keyframe",
        ProductionStage.MOTION: "motion",
        ProductionStage.AUDIO: "audio",
        ProductionStage.ASSEMBLY: "assembly",
    }
    stages: dict[str, dict[str, Any]] = {
        name: {"attempts": 0, "known_cost": None, "cost_unknown": 0, "currency": None, "mixed_currency": False, "elapsed_seconds": None, "elapsed_unknown": 0}
        for name in stage_names.values()
    }
    all_costs: list[tuple[float, str | None]] = []
    all_elapsed: list[float] = []
    unknown_cost = 0
    unknown_elapsed = 0
    for attempt in attempts:
        counts[_enum_value(attempt.status).lower()] += 1
        bucket = stages.get(stage_names.get(attempt.stage, ""))
        if bucket is not None:
            bucket["attempts"] += 1
        if attempt.reported_cost is None:
            unknown_cost += 1
            if bucket is not None:
                bucket["cost_unknown"] += 1
        else:
            all_costs.append((float(attempt.reported_cost), attempt.currency))
            if bucket is not None:
                bucket["known_cost"] = round(float(bucket["known_cost"] or 0) + float(attempt.reported_cost), 6)
                currencies = set(bucket.get("_currencies") or [])
                currencies.add(attempt.currency or "UNKNOWN")
                bucket["_currencies"] = sorted(currencies)
        if attempt.elapsed_seconds is None:
            unknown_elapsed += 1
            if bucket is not None:
                bucket["elapsed_unknown"] += 1
        else:
            all_elapsed.append(float(attempt.elapsed_seconds))
            if bucket is not None:
                bucket["elapsed_seconds"] = round(float(bucket["elapsed_seconds"] or 0) + float(attempt.elapsed_seconds), 3)
    for bucket in stages.values():
        currencies = bucket.pop("_currencies", [])
        bucket["currency"] = currencies[0] if len(currencies) == 1 else None
        bucket["mixed_currency"] = len(currencies) > 1
        bucket["total_cost"] = bucket["known_cost"] if bucket["cost_unknown"] == 0 and not bucket["mixed_currency"] else None
    currencies = sorted({currency or "UNKNOWN" for _, currency in all_costs})
    known_total = round(sum(value for value, _ in all_costs), 6) if all_costs else None
    return {
        "attempt_count": len(attempts),
        "successful_attempts": counts[ProductionAttemptStatus.SUCCEEDED.value.lower()] + counts[ProductionAttemptStatus.APPROVED.value.lower()],
        "failed_attempts": counts[ProductionAttemptStatus.FAILED.value.lower()],
        "rejected_attempts": counts[ProductionAttemptStatus.REJECTED.value.lower()],
        "status_counts": counts,
        "stages": stages,
        "known_cost_total": known_total,
        "total_cost": known_total if unknown_cost == 0 and len(currencies) <= 1 else None,
        "cost_unknown_attempts": unknown_cost,
        "currency": currencies[0] if len(currencies) == 1 else None,
        "mixed_currency": len(currencies) > 1,
        "known_elapsed_seconds": round(sum(all_elapsed), 3) if all_elapsed else None,
        "total_elapsed_seconds": round(sum(all_elapsed), 3) if unknown_elapsed == 0 and all_elapsed else None,
        "elapsed_unknown_attempts": unknown_elapsed,
    }


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ProductionRecipeError("timestamp is required")
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _mime_for_path(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}[suffix]
        return "image", mime
    if suffix in {".mp4", ".mov", ".m4v", ".webm"}:
        return "video", "video/mp4" if suffix != ".webm" else "video/webm"
    if suffix in {".wav", ".mp3", ".m4a", ".aac"}:
        return "audio", "audio/wav" if suffix == ".wav" else "audio/mpeg"
    if suffix in {".srt", ".vtt"}:
        return "captions", "text/plain"
    return "file", "application/octet-stream"


def _default_output_role(stage: ProductionStage) -> ProductionAssetRole:
    return {
        ProductionStage.KEYFRAMES: ProductionAssetRole.KEYFRAME,
        ProductionStage.MOTION: ProductionAssetRole.MOTION_OUTPUT,
        ProductionStage.AUDIO: ProductionAssetRole.VOICE,
        ProductionStage.ASSEMBLY: ProductionAssetRole.ROUGH_CUT,
        ProductionStage.FINAL_QA: ProductionAssetRole.FINAL_OUTPUT,
        ProductionStage.COMPLETE: ProductionAssetRole.FINAL_OUTPUT,
        ProductionStage.LEGACY_ONE_SHOT: ProductionAssetRole.LEGACY_BASELINE,
    }.get(stage, ProductionAssetRole.QA_ARTIFACT)


def apply_attempt_result(
    db: Session,
    attempt_id: int,
    payload: dict[str, Any],
) -> tuple[ProductionAttempt, list[ContentAsset], bool]:
    attempt = db.get(ProductionAttempt, attempt_id)
    if attempt is None:
        raise AttemptResultError("attempt not found")
    version_row = db.get(ProductionRecipeVersion, attempt.recipe_version_id)
    if version_row is None:
        raise AttemptResultError("attempt recipe version not found")
    assert_version_mutable(version_row)
    if not isinstance(payload, dict):
        raise AttemptResultError("attempt result must be a JSON object")
    version = str(payload.get("contract_version") or ATTEMPT_RESULT_CONTRACT_VERSION)
    if version != ATTEMPT_RESULT_CONTRACT_VERSION:
        raise AttemptResultError("unsupported attempt result contract version")
    try:
        payload_attempt_id = int(payload.get("attempt_id"))
    except (TypeError, ValueError) as exc:
        raise AttemptResultError("attempt_id is required") from exc
    if payload_attempt_id != attempt.id:
        raise AttemptResultError("result attempt_id does not match target attempt")
    try:
        safe = secret_safe(payload, reject=True)
    except CapabilitySnapshotError as exc:
        raise AttemptResultError(str(exc)) from exc
    result_checksum = canonical_checksum(safe)
    existing_checksum = (attempt.metadata_json or {}).get("result_checksum")
    if existing_checksum:
        if existing_checksum != result_checksum:
            raise AttemptResultError("attempt already has a different applied result")
        links = db.query(ProductionAttemptAsset).filter_by(attempt_id=attempt.id).order_by(ProductionAttemptAsset.ordinal).all()
        return attempt, [db.get(ContentAsset, link.asset_id) for link in links if db.get(ContentAsset, link.asset_id)], False
    status_text = str(payload.get("status") or "").strip().lower()
    if status_text not in {"completed", "succeeded", "failed"}:
        raise AttemptResultError("status must be completed, succeeded, or failed")
    if payload.get("local_comfy_used") is True:
        raise AttemptResultError("local Comfy claims are rejected for production attempts")
    execution_target = str(payload.get("execution_target") or "").strip().lower()
    cloud_expected = execution_target in {"comfy_cloud", "comfy cloud"} or "comfy" in str(payload.get("provider") or attempt.provider or "").lower()
    if cloud_expected and status_text != "failed" and not str(payload.get("cloud_job_id") or "").strip():
        raise AttemptResultError("successful Comfy Cloud result requires cloud_job_id")
    input_links: list[tuple[int, ProductionAssetRole]] = []
    for input_row in payload.get("input_assets") or []:
        if isinstance(input_row, dict):
            asset_id = input_row.get("asset_id")
            role_value = input_row.get("role") or ProductionAssetRole.INPUT.value
        else:
            asset_id = input_row
            role_value = ProductionAssetRole.INPUT.value
        try:
            parsed_asset_id = int(asset_id)
            parsed_role = _coerce_role(role_value)
        except (TypeError, ValueError, ProductionRecipeError) as exc:
            raise AttemptResultError("input_assets must reference stored assets") from exc
        if db.get(ContentAsset, parsed_asset_id) is None:
            raise AttemptResultError(f"input asset not found: {parsed_asset_id}")
        input_links.append((parsed_asset_id, parsed_role))
    output_rows = payload.get("output_files")
    validated_outputs: list[tuple[int, dict[str, Any], Path, str, ProductionAssetRole]] = []
    if status_text != "failed":
        if not isinstance(output_rows, list) or not output_rows:
            raise AttemptResultError("successful result requires output_files")
        for ordinal, output in enumerate(output_rows):
            item = output if isinstance(output, dict) else {"path": output}
            path_text = str(item.get("path") or item.get("output_path") or "").strip()
            if not path_text:
                raise AttemptResultError("each output file requires a local path")
            path = Path(path_text)
            if not path.is_file():
                raise AttemptResultError(f"output file missing: {path}")
            try:
                role = _coerce_role(item.get("role") or _default_output_role(attempt.stage).value)
            except ProductionRecipeError as exc:
                raise AttemptResultError(str(exc)) from exc
            validated_outputs.append((ordinal, item, path, sha256_file(path), role))
    meta = dict(attempt.metadata_json or {})
    meta.update({"result_checksum": result_checksum, "result_contract_version": version, "execution_state": "RESULT_APPLIED"})
    attempt.metadata_json = meta
    attempt.provider = str(payload.get("provider") or attempt.provider or "").strip() or None
    attempt.agent = str(payload.get("agent") or attempt.agent or "").strip() or None
    attempt.cloud_job_id = str(payload.get("cloud_job_id") or "").strip() or None
    attempt.template_id = str(payload.get("template_id") or payload.get("template") or attempt.template_id or "").strip() or None
    attempt.workflow_id = str(payload.get("workflow_id") or attempt.workflow_id or "").strip() or None
    attempt.workflow_version = str(payload.get("workflow_version") or attempt.workflow_version or "").strip() or None
    attempt.model = str(payload.get("model") or attempt.model or "").strip() or None
    if isinstance(payload.get("parameters"), dict):
        attempt.parameters_json = _json_clone(payload["parameters"])
    if payload.get("started_at"):
        attempt.started_at = _parse_datetime(payload["started_at"])
    if payload.get("completed_at"):
        attempt.completed_at = _parse_datetime(payload["completed_at"])
    if attempt.started_at and attempt.completed_at:
        attempt.elapsed_seconds = max(0.0, (attempt.completed_at - attempt.started_at).total_seconds())
    elif payload.get("elapsed_seconds") is not None:
        attempt.elapsed_seconds = max(0.0, float(payload["elapsed_seconds"]))
    if payload.get("cost") is not None:
        attempt.reported_cost = float(payload["cost"])
    attempt.currency = str(payload.get("currency") or attempt.currency or "").strip().upper() or None
    attempt.qa_result_json = _json_clone(payload.get("qa") or {})
    attempt.continuity_result_json = _json_clone(payload.get("continuity") or {})
    if status_text == "failed":
        attempt.status = ProductionAttemptStatus.FAILED
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        attempt.error_code = str(error.get("code") or payload.get("error_code") or "ATTEMPT_FAILED")[:64]
        attempt.error_message = str(error.get("message") or payload.get("error_message") or "agent reported failure")[:2000]
        db.commit()
        db.refresh(attempt)
        return attempt, [], True
    for asset_id, role in input_links:
        link_attempt_asset(db, attempt.id, asset_id, role, commit=False)
    assets: list[ContentAsset] = []
    for ordinal, item, path, checksum, role in validated_outputs:
        existing_asset = db.query(ContentAsset).filter_by(file_reference=str(path), sha256=checksum).one_or_none()
        if existing_asset is None:
            asset_type, mime = _mime_for_path(path)
            dimensions = item.get("dimensions") if isinstance(item.get("dimensions"), dict) else {}
            asset = ContentAsset(
                asset_type=asset_type,
                file_reference=str(path),
                duration=float(item.get("duration_seconds")) if item.get("duration_seconds") is not None else None,
                generation_provider=attempt.provider or attempt.agent,
                generation_workflow=attempt.workflow_id or attempt.template_id,
                generation_cost=attempt.reported_cost,
                approval_status=ApprovalStatus.DRAFT,
                generation_job_id=attempt.generation_job_id,
                mime_type=str(item.get("mime_type") or mime),
                source="production_attempt",
                format_family=db.get(ProductionRecipe, attempt.recipe_id).format_family,
                width=int(dimensions.get("width")) if dimensions.get("width") is not None else None,
                height=int(dimensions.get("height")) if dimensions.get("height") is not None else None,
                asset_role=role.value,
                sha256=checksum,
                media_metadata_json=_json_clone(item.get("media_metadata") or {}),
                reported_cost=attempt.reported_cost,
                currency=attempt.currency,
            )
            db.add(asset)
            db.flush()
        else:
            asset = existing_asset
        link_attempt_asset(db, attempt.id, asset.id, role, ordinal=ordinal, commit=False)
        assets.append(asset)
    attempt.status = ProductionAttemptStatus.SUCCEEDED
    attempt.error_code = None
    attempt.error_message = None
    db.commit()
    db.refresh(attempt)
    return attempt, assets, True


def current_benchmark_selection(db: Session) -> ProductionBenchmarkDecision | None:
    return (
        db.query(ProductionBenchmarkDecision)
        .filter_by(selection_key=BENCHMARK_SELECTION_KEY)
        .order_by(ProductionBenchmarkDecision.created_at.desc(), ProductionBenchmarkDecision.id.desc())
        .first()
    )


def benchmark_status(db: Session) -> str:
    selection = current_benchmark_selection(db)
    return (
        ProductionBenchmarkStatus.USER_SELECTED.value
        if selection is not None
        else "AWAITING_USER_SELECTION"
    )


def _source_idea(db: Session, spec: ProductionSpec) -> dict[str, Any]:
    if spec.source_brainstorm_set_id is None:
        return {}
    brainstorm = db.get(FormatBrainstormSet, spec.source_brainstorm_set_id)
    ideas = brainstorm.ideas_json if brainstorm and isinstance(brainstorm.ideas_json, list) else []
    try:
        index = int(spec.source_idea_identifier)
    except (TypeError, ValueError):
        return {}
    return dict(ideas[index]) if 0 <= index < len(ideas) and isinstance(ideas[index], dict) else {}


def benchmark_shortlist(db: Session) -> dict[str, Any]:
    from trendforge.analysis.opportunities import aggregate_format_opportunities

    opportunities = {
        row.format_family: row for row in aggregate_format_opportunities(db)
    }
    selection = current_benchmark_selection(db)
    decisions = {
        row.production_spec_id: row
        for row in db.query(ProductionBenchmarkDecision).order_by(
            ProductionBenchmarkDecision.created_at, ProductionBenchmarkDecision.id
        ).all()
    }
    rows: list[dict[str, Any]] = []
    specs = db.query(ProductionSpec).order_by(ProductionSpec.created_at.desc(), ProductionSpec.id.desc()).all()
    for spec in specs:
        payload = dict(spec.spec_json or {})
        idea = _source_idea(db, spec)
        opportunity = opportunities.get(spec.format_family)
        jobs = (
            db.query(GenerationJob)
            .filter_by(production_spec_id=spec.id)
            .order_by(GenerationJob.created_at.desc(), GenerationJob.id.desc())
            .all()
        )
        job_ids = [job.id for job in jobs]
        asset_count = (
            db.query(ContentAsset)
            .filter(ContentAsset.generation_job_id.in_(job_ids))
            .count()
            if job_ids
            else 0
        )
        try:
            budget = validate_shot_plan(payload)
            eligibility_error = None
        except ProductionRecipeError as exc:
            budget = None
            eligibility_error = str(exc)
        is_selected = selection is not None and selection.production_spec_id == spec.id
        decision = decisions.get(spec.id)
        rows.append(
            {
                "spec": spec,
                "payload": payload,
                "idea": idea,
                "title": payload.get("title") or idea.get("idea_title") or f"ProductionSpec #{spec.id}",
                "hook": payload.get("hook") or idea.get("hook") or "",
                "duration_seconds": payload.get("duration_seconds"),
                "shot_count": len(_source_shots(payload)),
                "production_complexity": payload.get("production_complexity"),
                "ai_leverage": payload.get("ai_leverage") or (opportunity.ai_leverage_median if opportunity else None),
                "variation_potential": (
                    payload.get("variation_potential")
                    or idea.get("variation_axis")
                    or (opportunity.variation_density_median if opportunity else None)
                ),
                "family_status": opportunity.family_status if opportunity else "UNKNOWN",
                "evidence_strength": opportunity.evidence_strength if opportunity else "UNKNOWN",
                "family_score": opportunity.family_opportunity_score if opportunity else None,
                "generation_jobs": jobs,
                "generation_count": len(jobs),
                "asset_count": asset_count,
                "budget": budget,
                "eligibility_error": eligibility_error,
                "benchmark_state": (
                    ProductionBenchmarkStatus.USER_SELECTED.value
                    if is_selected
                    else ProductionBenchmarkStatus.EXCLUDED_BY_USER.value
                    if decision is not None and decision.status == ProductionBenchmarkStatus.EXCLUDED_BY_USER
                    else ProductionBenchmarkStatus.READY_FOR_BENCHMARK_REVIEW.value
                ),
                "can_select": selection is None and decision is None and eligibility_error is None,
                "existing_recipe": db.query(ProductionRecipe).filter_by(source_production_spec_id=spec.id).one_or_none(),
            }
        )
    return {
        "status": benchmark_status(db),
        "selection": selection,
        "rows": rows,
    }


def select_quality_benchmark(
    db: Session,
    production_spec_id: int,
    *,
    selected_by: str,
    reason: str,
) -> tuple[ProductionBenchmarkDecision, ProductionRecipe, ProductionRecipeVersion, bool]:
    reviewer = str(selected_by or "").strip()
    rationale = str(reason or "").strip()
    if not reviewer:
        raise ProductionRecipeError("selected_by is required for explicit user approval")
    if not rationale:
        raise ProductionRecipeError("selection reason is required")
    spec = db.get(ProductionSpec, production_spec_id)
    if spec is None:
        raise ProductionRecipeError("production spec not found")
    validate_shot_plan(dict(spec.spec_json or {}))
    existing = current_benchmark_selection(db)
    if existing is not None:
        if existing.production_spec_id != spec.id:
            raise ProductionRecipeError("a quality benchmark has already been user-selected")
        recipe = db.get(ProductionRecipe, existing.recipe_id) if existing.recipe_id else None
        if recipe is None or recipe.current_version_id is None:
            raise ProductionRecipeError("selected benchmark is missing its recipe")
        version = db.get(ProductionRecipeVersion, recipe.current_version_id)
        if version is None:
            raise ProductionRecipeError("selected benchmark is missing its recipe version")
        return existing, recipe, version, False
    shortlist = benchmark_shortlist(db)
    candidate = next(row for row in shortlist["rows"] if row["spec"].id == spec.id)
    if not candidate["can_select"]:
        raise ProductionRecipeError("production spec is not available for benchmark selection")
    review_snapshot = {
        "title": candidate["title"],
        "format_family": spec.format_family,
        "family_status": candidate["family_status"],
        "evidence_strength": candidate["evidence_strength"],
        "family_score": candidate["family_score"],
        "hook": candidate["hook"],
        "duration_seconds": candidate["duration_seconds"],
        "shot_count": candidate["shot_count"],
        "production_complexity": candidate["production_complexity"],
        "ai_leverage": candidate["ai_leverage"],
        "variation_potential": candidate["variation_potential"],
        "generation_count": candidate["generation_count"],
        "asset_count": candidate["asset_count"],
        "user_approval_required": True,
    }
    recipe, version, created = create_recipe_from_spec(
        db,
        spec.id,
        name=(spec.spec_json or {}).get("title"),
        notes=f"Explicit quality benchmark selection by {reviewer}: {rationale}",
        commit=False,
    )
    selection = ProductionBenchmarkDecision(
        selection_key=BENCHMARK_SELECTION_KEY,
        production_spec_id=spec.id,
        status=ProductionBenchmarkStatus.USER_SELECTED,
        selected_by=reviewer,
        selection_reason=rationale,
        review_snapshot_json=secret_safe(review_snapshot),
        recipe_id=recipe.id,
    )
    db.add(selection)
    db.commit()
    db.refresh(selection)
    db.refresh(recipe)
    db.refresh(version)
    return selection, recipe, version, created


def exclude_benchmark_candidate(
    db: Session,
    production_spec_id: int,
    *,
    reviewer: str,
    reason: str,
) -> tuple[ProductionBenchmarkDecision, bool]:
    reviewer_value = str(reviewer or "").strip()
    reason_value = str(reason or "").strip()
    if not reviewer_value or not reason_value:
        raise ProductionRecipeError("reviewer and reason are required")
    spec = db.get(ProductionSpec, production_spec_id)
    if spec is None:
        raise ProductionRecipeError("production spec not found")
    selected = current_benchmark_selection(db)
    if selected is not None and selected.production_spec_id == spec.id:
        raise ProductionRecipeError("the user-selected benchmark cannot be excluded")
    existing = db.query(ProductionBenchmarkDecision).filter_by(production_spec_id=spec.id).one_or_none()
    if existing is not None:
        if existing.status != ProductionBenchmarkStatus.EXCLUDED_BY_USER:
            raise ProductionRecipeError("benchmark candidate already has a different decision")
        return existing, False
    row = ProductionBenchmarkDecision(
        selection_key=f"excluded-spec-{spec.id}",
        production_spec_id=spec.id,
        status=ProductionBenchmarkStatus.EXCLUDED_BY_USER,
        selected_by=reviewer_value,
        selection_reason=reason_value,
        review_snapshot_json={
            "title": (spec.spec_json or {}).get("title"),
            "format_family": spec.format_family,
            "excluded_from_quality_benchmark": True,
        },
        recipe_id=None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, True


def attempt_handoff_manifest(db: Session, attempt_id: int) -> dict[str, Any]:
    attempt = db.get(ProductionAttempt, attempt_id)
    if attempt is None:
        raise ProductionRecipeError("attempt not found")
    version = db.get(ProductionRecipeVersion, attempt.recipe_version_id)
    shot = db.get(ProductionRecipeShot, attempt.shot_id) if attempt.shot_id else None
    links = db.query(ProductionAttemptAsset).filter_by(attempt_id=attempt.id).order_by(ProductionAttemptAsset.ordinal, ProductionAttemptAsset.id).all()
    return secret_safe(
        {
            "contract_version": ATTEMPT_HANDOFF_VERSION,
            "attempt_id": attempt.id,
            "recipe_id": attempt.recipe_id,
            "recipe_version_id": attempt.recipe_version_id,
            "stage": _enum_value(attempt.stage),
            "shot": _shot_manifest(shot) if shot else None,
            "status": _enum_value(attempt.status),
            "execution_state": (attempt.metadata_json or {}).get("execution_state") or "AWAITING_AGENT_EXECUTION",
            "capability_snapshot_id": version.capability_snapshot_id if version else None,
            "provider": attempt.provider,
            "agent": attempt.agent,
            "model": attempt.model,
            "template_id": attempt.template_id,
            "workflow_id": attempt.workflow_id,
            "workflow_version": attempt.workflow_version,
            "parameters": attempt.parameters_json or {},
            "prompt_components": attempt.prompt_components_json or {},
            "negative_constraints": attempt.negative_constraints_json or [],
            "input_assets": [_asset_link_manifest(db, link) for link in links],
            "result_contract_version": ATTEMPT_RESULT_CONTRACT_VERSION,
            "local_comfy_allowed": False,
            "media_generation_started": False,
        }
    )


def _shot_manifest(shot: ProductionRecipeShot | None) -> dict[str, Any] | None:
    if shot is None:
        return None
    return {
        "id": shot.id,
        "shot_number": shot.shot_number,
        "stable_key": shot.stable_key,
        "title": shot.title,
        "purpose": shot.purpose,
        "start_seconds": shot.start_seconds,
        "end_seconds": shot.end_seconds,
        "duration_seconds": shot.duration_seconds,
        "camera": shot.camera_json,
        "framing": shot.framing_json,
        "subject": shot.subject_json,
        "action": shot.action_json,
        "environment": shot.environment_json,
        "audio": shot.audio_json,
        "generation_notes": shot.generation_notes_json,
        "continuity_inputs": shot.continuity_inputs_json or [],
        "continuity_outputs": shot.continuity_outputs_json or [],
        "required_asset_roles": shot.required_asset_roles_json or [],
        "negative_constraints": shot.negative_constraints_json or [],
        "qa_requirements": shot.qa_requirements_json or [],
        "status": _enum_value(shot.status),
    }


def _asset_link_manifest(db: Session, link: ProductionAttemptAsset) -> dict[str, Any]:
    asset = db.get(ContentAsset, link.asset_id)
    return {
        "asset_id": link.asset_id,
        "role": _enum_value(link.role),
        "ordinal": link.ordinal,
        "sha256": asset.sha256 if asset else None,
        "file_reference": asset.file_reference if asset else None,
        "mime_type": asset.mime_type if asset else None,
        "metadata": link.metadata_json or {},
    }


def recipe_manifest(db: Session, recipe_id: int, version_id: int | None = None) -> dict[str, Any]:
    recipe = db.get(ProductionRecipe, recipe_id)
    if recipe is None:
        raise ProductionRecipeError("recipe not found")
    selected_id = version_id or recipe.current_version_id
    version = db.get(ProductionRecipeVersion, selected_id) if selected_id else None
    if version is None or version.recipe_id != recipe.id:
        raise ProductionRecipeError("recipe version not found")
    shots = _shots_for_version(db, version.id)
    attempts = db.query(ProductionAttempt).filter_by(recipe_version_id=version.id).order_by(ProductionAttempt.created_at, ProductionAttempt.id).all()
    approvals = db.query(ProductionApprovalEvent).filter_by(recipe_version_id=version.id).order_by(ProductionApprovalEvent.created_at, ProductionApprovalEvent.id).all()
    capability = db.get(ProductionCapabilitySnapshot, version.capability_snapshot_id) if version.capability_snapshot_id else None
    benchmark = db.query(ProductionBenchmarkDecision).filter_by(recipe_id=recipe.id).one_or_none()
    payload = {
        "manifest_version": RECIPE_MANIFEST_VERSION,
        "exported_at": utcnow().isoformat(),
        "recipe": {
            "id": recipe.id,
            "name": recipe.name,
            "slug": recipe.slug,
            "format_family": recipe.format_family,
            "status": _enum_value(recipe.status),
            "source_production_spec_id": recipe.source_production_spec_id,
            "notes": recipe.notes,
        },
        "version": {
            "id": version.id,
            "version_number": version.version_number,
            "status": _enum_value(version.status),
            "created_at": version.created_at.isoformat() if version.created_at else None,
            "frozen_at": version.frozen_at.isoformat() if version.frozen_at else None,
            "content_checksum": version.content_checksum,
            "production_spec_snapshot": version.production_spec_snapshot_json,
            "visual_bible": version.visual_bible_json or {},
            "duration_budget": version.duration_budget_json or {},
            "audio_plan": version.audio_plan_json or {},
            "caption_plan": version.caption_plan_json or {},
            "assembly_settings": version.assembly_settings_json or {},
            "capability_snapshot_id": version.capability_snapshot_id,
            "capability_status": "CAPTURED" if capability else "NOT_CAPTURED",
        },
        "capability_snapshot": {
            "id": capability.id,
            "provider": capability.provider,
            "mcp_server": capability.mcp_server,
            "agent": capability.agent,
            "agent_version": capability.agent_version,
            "captured_at": capability.captured_at.isoformat() if capability.captured_at else None,
            "content_checksum": capability.content_checksum,
            "normalized_capabilities": capability.normalized_capabilities_json,
        } if capability else None,
        "benchmark_selection": {
            "decision_id": benchmark.id,
            "status": _enum_value(benchmark.status),
            "selected_by": benchmark.selected_by,
            "selection_reason": benchmark.selection_reason,
            "created_at": benchmark.created_at.isoformat() if benchmark.created_at else None,
            "review_snapshot": benchmark.review_snapshot_json,
        } if benchmark and benchmark.status == ProductionBenchmarkStatus.USER_SELECTED else None,
        "stage_state": stage_state(db, version.id),
        "shots": [_shot_manifest(shot) for shot in shots],
        "attempts": [
            {
                "id": attempt.id,
                "shot_id": attempt.shot_id,
                "stage": _enum_value(attempt.stage),
                "attempt_number": attempt.attempt_number,
                "status": _enum_value(attempt.status),
                "provider": attempt.provider,
                "agent": attempt.agent,
                "model": attempt.model,
                "template_id": attempt.template_id,
                "workflow_id": attempt.workflow_id,
                "workflow_version": attempt.workflow_version,
                "cloud_job_id": attempt.cloud_job_id,
                "reported_cost": attempt.reported_cost,
                "currency": attempt.currency,
                "elapsed_seconds": attempt.elapsed_seconds,
                "qa": attempt.qa_result_json or {},
                "continuity": attempt.continuity_result_json or {},
                "assets": [
                    _asset_link_manifest(db, link)
                    for link in db.query(ProductionAttemptAsset).filter_by(attempt_id=attempt.id).order_by(ProductionAttemptAsset.ordinal, ProductionAttemptAsset.id).all()
                ],
            }
            for attempt in attempts
        ],
        "approvals": [
            {
                "id": event.id,
                "created_at": event.created_at.isoformat() if event.created_at else None,
                "scope_type": _enum_value(event.scope_type),
                "scope_id": event.scope_id,
                "gate": _enum_value(event.gate),
                "decision": _enum_value(event.decision),
                "reviewer": event.reviewer,
                "reason": event.reason,
                "approved_asset_id": event.approved_asset_id,
                "approved_attempt_id": event.approved_attempt_id,
            }
            for event in approvals
        ],
        "cost_time_summary": aggregate_attempts(db, version.id),
    }
    return secret_safe(payload)


def recipe_manifest_markdown(payload: dict[str, Any]) -> str:
    recipe = payload["recipe"]
    version = payload["version"]
    state = payload["stage_state"]
    lines = [
        f"# {recipe['name']} — Recipe v{version['version_number']}",
        "",
        f"- Manifest: `{payload['manifest_version']}`",
        f"- Recipe ID: `{recipe['id']}`",
        f"- Version ID: `{version['id']}`",
        f"- Format family: `{recipe['format_family']}`",
        f"- Recipe status: `{recipe['status']}`",
        f"- Version status: `{version['status']}`",
        f"- Current stage: `{state['current_stage']}`",
        f"- Current gate: `{state['current_gate'] or 'NONE'}`",
        f"- Capability snapshot: `{version['capability_status']}`",
        "",
        "## Shots",
        "",
    ]
    for shot in payload["shots"]:
        lines.append(
            f"{shot['shot_number']}. **{shot['title']}** — {shot['start_seconds']}–{shot['end_seconds']}s ({shot['status']})"
        )
    lines.extend([
        "",
        "## Cost and time",
        "",
        "```json",
        json.dumps(payload["cost_time_summary"], indent=2, ensure_ascii=False),
        "```",
        "",
        "## Machine-readable manifest",
        "",
        "Use the JSON export for complete snapshots, attempts, approvals, lineage, assembly, and QA data.",
    ])
    return "\n".join(lines) + "\n"


def recipe_list_rows(db: Session) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for recipe in db.query(ProductionRecipe).order_by(ProductionRecipe.updated_at.desc(), ProductionRecipe.id.desc()).all():
        version = db.get(ProductionRecipeVersion, recipe.current_version_id) if recipe.current_version_id else None
        if version is None:
            continue
        shots = _shots_for_version(db, version.id)
        state = stage_state(db, version.id)
        approved = sum(
            is_approved(
                db,
                version.id,
                ProductionApprovalGate.MOTION_APPROVAL,
                scope_type=ProductionApprovalScope.SHOT_MOTION,
                scope_id=shot.id,
            )
            for shot in shots
        )
        summary = aggregate_attempts(db, version.id)
        rows.append({
            "recipe": recipe,
            "version": version,
            "shot_count": len(shots),
            "approved_shots": approved,
            "stage": state["current_stage"],
            "gate": state["current_gate"],
            "costs": summary,
        })
    return rows


def stage_rail(db: Session, version_id: int) -> list[dict[str, str]]:
    state = stage_state(db, version_id)
    current = state["current_stage"]
    complete = {
        ProductionStage.PREPARATION.value: True,
        ProductionStage.VISUAL_BIBLE.value: state["visual_bible_approved"],
        ProductionStage.KEYFRAMES.value: state["keyframes_approved"],
        ProductionStage.MOTION.value: state["motion_approved"],
        ProductionStage.AUDIO.value: state["motion_approved"] and state["rough_cut_approved"],
        ProductionStage.ASSEMBLY.value: state["rough_cut_approved"],
        ProductionStage.FINAL_QA.value: state["final_render_approved"],
        ProductionStage.COMPLETE.value: state["final_render_approved"],
    }
    labels = {
        ProductionStage.PREPARATION: "Preparation",
        ProductionStage.VISUAL_BIBLE: "Visual Bible",
        ProductionStage.KEYFRAMES: "Keyframes",
        ProductionStage.MOTION: "Motion",
        ProductionStage.AUDIO: "Audio",
        ProductionStage.ASSEMBLY: "Assembly",
        ProductionStage.FINAL_QA: "Final QA",
        ProductionStage.COMPLETE: "Complete",
    }
    current_index = next(
        (index for index, stage in enumerate(STAGE_ORDER) if stage.value == current),
        len(STAGE_ORDER) - 1,
    )
    rail: list[dict[str, str]] = []
    for index, stage in enumerate(STAGE_ORDER):
        status = "complete" if complete[stage.value] else "not-started"
        if stage.value == current and not complete[stage.value]:
            status = "active"
        elif index > current_index and not complete[stage.value]:
            status = "blocked"
        rail.append({"stage": stage.value, "label": labels[stage], "status": status})
    return rail


def recipe_version_view(db: Session, recipe_id: int, version_id: int) -> dict[str, Any]:
    recipe = db.get(ProductionRecipe, recipe_id)
    version = db.get(ProductionRecipeVersion, version_id)
    if recipe is None or version is None or version.recipe_id != recipe.id:
        raise ProductionRecipeError("recipe/version not found")
    shots = _shots_for_version(db, version.id)
    attempts = db.query(ProductionAttempt).filter_by(recipe_version_id=version.id).order_by(ProductionAttempt.created_at.desc(), ProductionAttempt.id.desc()).all()
    attempts_by_shot: dict[int | None, list[dict[str, Any]]] = {}
    for attempt in attempts:
        links = db.query(ProductionAttemptAsset).filter_by(attempt_id=attempt.id).order_by(ProductionAttemptAsset.ordinal, ProductionAttemptAsset.id).all()
        attempts_by_shot.setdefault(attempt.shot_id, []).append({
            "attempt": attempt,
            "assets": [_asset_link_manifest(db, link) for link in links],
        })
    approvals = db.query(ProductionApprovalEvent).filter_by(recipe_version_id=version.id).order_by(ProductionApprovalEvent.created_at.desc(), ProductionApprovalEvent.id.desc()).all()
    versions = db.query(ProductionRecipeVersion).filter_by(recipe_id=recipe.id).order_by(ProductionRecipeVersion.version_number.desc()).all()
    shot_rows = []
    for shot in shots:
        keyframe = latest_approval(db, version.id, ProductionApprovalGate.KEYFRAME_APPROVAL, scope_type=ProductionApprovalScope.SHOT_KEYFRAME, scope_id=shot.id)
        motion = latest_approval(db, version.id, ProductionApprovalGate.MOTION_APPROVAL, scope_type=ProductionApprovalScope.SHOT_MOTION, scope_id=shot.id)
        shot_attempts = attempts_by_shot.get(shot.id, [])
        costs = [entry["attempt"].reported_cost for entry in shot_attempts if entry["attempt"].reported_cost is not None]
        elapsed = [entry["attempt"].elapsed_seconds for entry in shot_attempts if entry["attempt"].elapsed_seconds is not None]
        shot_rows.append({
            "shot": shot,
            "attempts": shot_attempts,
            "keyframe_approval": keyframe,
            "motion_approval": motion,
            "known_cost": round(sum(costs), 6) if costs else None,
            "cost_unknown": sum(entry["attempt"].reported_cost is None for entry in shot_attempts),
            "known_elapsed": round(sum(elapsed), 3) if elapsed else None,
            "elapsed_unknown": sum(entry["attempt"].elapsed_seconds is None for entry in shot_attempts),
        })
    reference_assets = []
    for entries in attempts_by_shot.values():
        for entry in entries:
            for asset in entry["assets"]:
                if asset["role"] == ProductionAssetRole.REFERENCE.value:
                    reference_assets.append(asset)
    return {
        "recipe": recipe,
        "version": version,
        "versions": versions,
        "shots": shot_rows,
        "attempts": attempts,
        "recipe_attempts": attempts_by_shot.get(None, []),
        "approvals": approvals,
        "state": stage_state(db, version.id),
        "stage_rail": stage_rail(db, version.id),
        "summary": aggregate_attempts(db, version.id),
        "reference_assets": reference_assets,
        "capability": db.get(ProductionCapabilitySnapshot, version.capability_snapshot_id) if version.capability_snapshot_id else None,
    }
