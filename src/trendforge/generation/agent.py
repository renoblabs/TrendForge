from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from trendforge.generation.adapters import CloudResult
from trendforge.generation.events import append_event, redact
from trendforge.generation.handoff import read_result
from trendforge.generation.prompt import AGENT_PROMPT_VERSION
from trendforge.generation.qa import review_output
from trendforge.models import ContentAsset, GenerationJob, GenerationJobStatus, utcnow


class ResultContractError(ValueError):
    pass


def _first_output_path(payload: dict[str, Any]) -> Path:
    files = payload.get("output_files") or []
    if isinstance(files, list) and files:
        return Path(str(files[0]))
    return Path(str(payload.get("output_path") or ""))


def parse_result_contract(payload: dict[str, Any]) -> CloudResult:
    if payload.get("local_comfy_used") is True:
        raise ResultContractError("result claimed local Comfy; rejected")
    if payload.get("cloud_mcp_used") is not True:
        raise ResultContractError("cloud_mcp_used must be true")
    cloud_id = str(payload.get("cloud_job_id") or "").strip()
    if not cloud_id:
        raise ResultContractError("missing cloud_job_id")
    path = _first_output_path(payload)
    if not path.is_file():
        raise ResultContractError("output file missing")
    if payload.get("success") is False or str(payload.get("status") or "").lower() == "failed":
        raise ResultContractError(str(payload.get("error") or "agent reported failure"))
    template = str(payload.get("workflow_or_template") or payload.get("template") or "")
    return CloudResult(
        cloud_job_id=cloud_id,
        template=template,
        output_path=path,
        model=payload.get("model"),
        parameters=payload.get("parameters") if isinstance(payload.get("parameters"), dict) else {},
        output_url=payload.get("output_url"),
        cloud_mcp_used=True,
        local_comfy_used=False,
        agent=str(payload.get("agent") or "cursor-agent"),
    )


def apply_result_file(db: Session, job: GenerationJob) -> GenerationJob:
    payload = read_result(job.id)
    if not payload:
        job.status = GenerationJobStatus.FAILED
        job.error_message = "result.json missing; agent did not return an output"
        append_event(job, "job failed", job.error_message)
        db.commit()
        return job
    job.status = GenerationJobStatus.REVIEWING
    append_event(job, "reviewing output")
    db.commit()
    try:
        result = parse_result_contract(payload)
    except ResultContractError as exc:
        job.status = GenerationJobStatus.FAILED
        job.error_message = str(exc)[:400]
        append_event(job, "job failed", job.error_message)
        db.commit()
        return job
    review = review_output(result.output_path)
    append_event(job, "output detected", review.get("reason"))
    if not review.get("ok"):
        job.status = GenerationJobStatus.FAILED
        job.error_message = str(review.get("reason") or "qa failed")
        append_event(job, "qa failed", job.error_message)
        db.commit()
        return job
    append_event(job, "QA passed")
    duration = payload.get("output_duration_seconds")
    if duration is None:
        duration = payload.get("output_duration")
    try:
        duration_val = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_val = None
    asset = ContentAsset(
        asset_type="image" if str(review.get("mime_type") or "").startswith("image/") else "video",
        file_reference=str(result.output_path),
        duration=duration_val,
        generation_provider=result.agent,
        generation_workflow=result.template,
        generation_job_id=job.id,
        mime_type=review.get("mime_type"),
        source="generate",
        format_family=job.format_family,
        width=review.get("width"),
        height=review.get("height"),
    )
    db.add(asset)
    db.flush()
    job.output_asset_ids = [asset.id]
    job.comfy_workflow_id = result.template
    job.comfy_workflow_version = AGENT_PROMPT_VERSION
    job.agent = result.agent
    job.agent_session_id = result.cloud_job_id
    meta = dict(job.job_metadata_json or {})
    meta["execution_target"] = "Comfy Cloud"
    meta["local_comfy_used"] = False
    meta["cloud_mcp_used"] = True
    meta["cloud_job_id"] = result.cloud_job_id
    meta["workflow"] = {
        "id": result.template,
        "version": AGENT_PROMPT_VERSION,
        "model": result.model,
        "parameters": redact(result.parameters),
        "prompt_id": result.cloud_job_id,
        "output_url": result.output_url,
    }
    meta["qa"] = redact(review)
    job.job_metadata_json = meta
    job.status = GenerationJobStatus.COMPLETED
    job.error_message = None
    job.updated_at = utcnow()
    append_event(job, "Generation completed", f"asset {asset.id}")
    db.commit()
    db.refresh(asset)
    return job
