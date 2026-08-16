from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from trendforge.config import ROOT, load_generation_config


@dataclass
class CloudResult:
    cloud_job_id: str
    template: str
    output_path: Path
    model: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    output_url: str | None = None
    cloud_mcp_used: bool = True
    local_comfy_used: bool = False
    agent: str = "Cursor"


class AgentUnavailableError(RuntimeError):
    pass


def generation_output_dir() -> Path:
    override = os.environ.get("TRENDFORGE_GENERATED_DIR", "").strip()
    if override:
        path = Path(override)
    else:
        cfg = load_generation_config()
        path = ROOT / str(cfg.get("output_dir") or "data/generated")
    path.mkdir(parents=True, exist_ok=True)
    return path


def generation_jobs_dir() -> Path:
    override = os.environ.get("TRENDFORGE_JOBS_DIR", "").strip()
    if override:
        path = Path(override)
    else:
        path = ROOT / "data" / "generation_jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path
