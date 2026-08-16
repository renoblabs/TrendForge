from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from trendforge.generation.adapters import generation_jobs_dir
from trendforge.generation.events import redact
from trendforge.generation.prompt import AGENT_PROMPT_VERSION, render_agent_prompt, render_video_prompt


def job_handoff_dir(job_id: int) -> Path:
    path = generation_jobs_dir() / str(job_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_job_contract(job_id: int, job: dict[str, Any], production_spec: dict[str, Any]) -> Path:
    folder = job_handoff_dir(job_id)
    job_payload = redact(dict(job))
    job_payload["agent_prompt_version"] = AGENT_PROMPT_VERSION
    spec_payload = redact(dict(production_spec))
    (folder / "job.json").write_text(json.dumps(job_payload, indent=2, default=str), encoding="utf-8")
    (folder / "production_spec.json").write_text(
        json.dumps(spec_payload, indent=2, default=str), encoding="utf-8"
    )
    (folder / "video_prompt.txt").write_text(
        render_video_prompt(job_payload, spec_payload), encoding="utf-8"
    )
    (folder / "AGENT.md").write_text(render_agent_prompt(job_payload), encoding="utf-8")
    return folder


def read_result(job_id: int) -> dict[str, Any] | None:
    path = job_handoff_dir(job_id) / "result.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def write_result(job_id: int, result: dict[str, Any]) -> Path:
    path = job_handoff_dir(job_id) / "result.json"
    path.write_text(json.dumps(redact(result), indent=2, default=str), encoding="utf-8")
    return path
