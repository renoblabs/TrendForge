from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trendforge.config import ROOT, load_scoring_config


DEFAULT_WEIGHTS = {
    "trend_velocity": 0.25,
    "novelty": 0.20,
    "replicability": 0.15,
    "variation_density": 0.15,
    "cross_platform": 0.10,
    "production_cost": 0.05,
    "hook_strength": 0.05,
    "ip_safety": 0.05,
}


@dataclass
class ScoreInputs:
    trend_velocity: float
    novelty: float
    replicability: float
    variation_density: float
    cross_platform: float
    production_cost: float
    hook_strength: float
    ip_risk: float
    saturation: float


@dataclass
class ScoreResult:
    overall: float
    breakdown: dict[str, float]
    weighted_sum: float
    saturation_penalty_factor: float
    suggested_status: str


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def load_weights(config: dict[str, Any] | None = None) -> dict[str, float]:
    cfg = config if config is not None else load_scoring_config()
    weights = dict(DEFAULT_WEIGHTS)
    weights.update(cfg.get("weights", {}))
    return weights


def score_format(
    inputs: ScoreInputs,
    config: dict[str, Any] | None = None,
) -> ScoreResult:
    """Deterministic scoring separate from LLM analysis."""
    cfg = config if config is not None else load_scoring_config()
    weights = load_weights(cfg)
    k = float(cfg.get("saturation_penalty_k", 0.30))
    thresholds = cfg.get("status_thresholds", {"BUILD": 85, "WATCH": 70})

    ip_safety = clamp(100.0 - float(inputs.ip_risk))
    components = {
        "trend_velocity": clamp(inputs.trend_velocity),
        "novelty": clamp(inputs.novelty),
        "replicability": clamp(inputs.replicability),
        "variation_density": clamp(inputs.variation_density),
        "cross_platform": clamp(inputs.cross_platform),
        "production_cost": clamp(inputs.production_cost),
        "hook_strength": clamp(inputs.hook_strength),
        "ip_safety": ip_safety,
        "saturation": clamp(inputs.saturation),
    }

    weighted_sum = sum(components[key] * weights[key] for key in weights)
    saturation = components["saturation"]
    penalty_factor = 1.0 - (k * saturation / 100.0)
    overall = clamp(weighted_sum * penalty_factor)

    build_t = float(thresholds.get("BUILD", 85))
    watch_t = float(thresholds.get("WATCH", 70))
    if overall >= build_t:
        suggested = "BUILD"
    elif overall >= watch_t:
        suggested = "WATCH"
    else:
        suggested = "DISCOVERED"

    return ScoreResult(
        overall=round(overall, 2),
        breakdown={k: round(v, 2) for k, v in components.items()},
        weighted_sum=round(weighted_sum, 2),
        saturation_penalty_factor=round(penalty_factor, 4),
        suggested_status=suggested,
    )


def production_cost_score_from_complexity(complexity: float) -> float:
    """Map production complexity (0=easy/cheap, 100=hard/expensive) to a positive score."""
    return clamp(100.0 - float(complexity))


def scoring_config_path() -> Path:
    return ROOT / "config" / "scoring_weights.json"
