from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from trendforge.config import load_generation_config
from trendforge.generation.events import append_event, redact
from trendforge.generation.handoff import write_job_contract
from trendforge.generation.prompt import AGENT_PROMPT_VERSION
from trendforge.generation.runner import launch_generation_agent
from trendforge.ideation.generate import POOL_APPROVED, idea_pool_status, load_brainstorm_sets
from trendforge.models import (
    FormatBrainstormSet,
    GenerationJob,
    GenerationJobStatus,
    utcnow,
)
from trendforge.production.spec_generate import find_latest_spec, generate_production_spec
from trendforge.services import slugify_format_key


class GenerationError(ValueError):
    pass


def list_cloud_models() -> list[dict[str, Any]]:
    cfg = load_generation_config()
    models = cfg.get("cloud_models") or []
    return [row for row in models if isinstance(row, dict) and row.get("id")]


def resolve_cloud_model(model_id: str | None) -> dict[str, Any]:
    cfg = load_generation_config()
    default_id = str(cfg.get("default_cloud_model") or "seedance-2.0")
    wanted = (model_id or default_id).strip()
    by_id = {str(row.get("id")): row for row in list_cloud_models()}
    if wanted not in by_id:
        raise GenerationError("unknown Comfy Cloud video model")
    return dict(by_id[wanted])


def list_generation_options(db: Session) -> list[dict[str, Any]]:
    grouped = load_brainstorm_sets(db, limit_per_family=20)
    options: list[dict[str, Any]] = []
    for family, sets in grouped.items():
        ideas: list[dict[str, Any]] = []
        for bset in sets:
            for idx, idea in enumerate(bset.ideas_json or []):
                if not isinstance(idea, dict):
                    continue
                if idea_pool_status(idea) != POOL_APPROVED:
                    continue
                ideas.append(
                    {
                        "set_id": bset.id,
                        "idea_index": idx,
                        "title": idea.get("idea_title") or f"Idea {idx + 1}",
                        "hook": idea.get("hook") or "",
                    }
                )
        if ideas:
            options.append({"format_family": family, "ideas": ideas[:20]})
    options.sort(key=lambda row: row["format_family"])
    return options


def _idea_from_set(bset: FormatBrainstormSet, idea_index: int) -> dict[str, Any]:
    ideas = bset.ideas_json if isinstance(bset.ideas_json, list) else []
    idea = ideas[idea_index]
    return idea if isinstance(idea, dict) else {}


def create_generation_job(
    db: Session,
    *,
    format_family: str,
    brainstorm_set_id: int,
    idea_index: int,
    duration_seconds: int = 5,
    variant_count: int = 1,
    style: str = "",
    voice: str = "",
    audio_preferences: str = "",
    cloud_model: str | None = None,
    force_stub_spec: bool = False,
    launch_agent: bool = True,
) -> GenerationJob:
    cfg = load_generation_config()
    allowed_durations = [int(x) for x in cfg.get("allowed_durations") or [5, 8, 15, 20, 30]]
    allowed_variants = [int(x) for x in cfg.get("allowed_variant_counts") or [1, 3, 5]]
    duration = int(duration_seconds)
    variants = int(variant_count)
    if duration not in allowed_durations:
        allowed = ", ".join(str(x) for x in allowed_durations)
        raise GenerationError(f"duration must be {allowed} seconds")
    if variants not in allowed_variants:
        raise GenerationError("variant_count must be 1, 3, or 5")
    model = resolve_cloud_model(cloud_model)
    bset = db.get(FormatBrainstormSet, brainstorm_set_id)
    if not bset:
        raise GenerationError("brainstorm set not found")
    family = slugify_format_key(format_family or bset.format_family)
    ideas = bset.ideas_json if isinstance(bset.ideas_json, list) else []
    if idea_index < 0 or idea_index >= len(ideas):
        raise GenerationError("idea not found")
    idea = _idea_from_set(bset, idea_index)
    if idea_pool_status(idea) != POOL_APPROVED:
        raise GenerationError("idea is not in the generation pool; approve it first")
    job = GenerationJob(
        status=GenerationJobStatus.QUEUED,
        format_family=family,
        source_brainstorm_set_id=bset.id,
        source_idea_identifier=str(idea_index),
        requested_duration_seconds=duration,
        variant_count=variants,
        style=(style or "").strip() or None,
        voice=(voice or "").strip() or None,
        audio_preferences=(audio_preferences or "").strip() or None,
        agent="cursor-agent",
        job_metadata_json={
            "events": [],
            "execution_target": "Comfy Cloud",
            "local_comfy_used": False,
            "cloud_mcp_used": True,
            "mcp": "native comfy-cloud",
            "agent_prompt_version": AGENT_PROMPT_VERSION,
            "cloud_model": model,
            "output_kind": model.get("kind") or "video",
        },
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    append_event(job, "job created")
    db.commit()

    job.status = GenerationJobStatus.PREPARING
    db.commit()
    spec = find_latest_spec(db, brainstorm_set_id=bset.id, idea_index=idea_index)
    if spec is None:
        spec = generate_production_spec(
            db,
            brainstorm_set_id=bset.id,
            idea_index=idea_index,
            duration_seconds=float(duration),
            force_stub=force_stub_spec,
        )
        db.refresh(job)
        append_event(job, "production spec generated", str(spec.id))
    else:
        append_event(job, "production spec reused", str(spec.id))
    job.production_spec_id = spec.id
    spec_json = spec.spec_json if isinstance(spec.spec_json, dict) else {}
    job_payload = {
        "job_id": job.id,
        "format_family": family,
        "source_idea": redact(idea),
        "production_spec_id": spec.id,
        "requested_duration": duration,
        "variant_count": variants,
        "style": job.style,
        "voice": job.voice,
        "audio_preferences": job.audio_preferences,
        "status": GenerationJobStatus.AGENT_RUNNING.value,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "execution_target": "comfy_cloud",
        "agent": "cursor-agent",
        "mcp": "native comfy-cloud",
        "output_kind": model.get("kind") or "video",
        "cloud_model": model,
    }
    write_job_contract(job.id, job_payload, spec_json)
    job.status = GenerationJobStatus.AGENT_RUNNING
    append_event(job, "waiting for agent", "job.json written; launching Cursor Agent CLI")
    db.commit()
    if launch_agent:
        launch_generation_agent(db, job)
        db.refresh(job)
    return job
