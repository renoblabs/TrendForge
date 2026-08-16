from __future__ import annotations

from typing import Any

from trendforge.config import load_family_weights


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def _scale(value: float | None, at_100: float) -> float | None:
    if value is None or at_100 <= 0:
        return None
    return _clamp(float(value) / float(at_100) * 100.0)


def _median_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    from statistics import median

    return float(median(values))


def score_family_opportunity(
    *,
    candidate_count: int,
    unique_channel_count: int,
    median_discovery_score: float | None,
    median_views_per_hour: float | None,
    median_acceleration: float | None,
    accelerating_count: int,
    ai_leverage_median: float | None,
    variation_density_median: float | None,
    production_complexity_median: float | None,
    config: dict[str, Any] | None = None,
) -> tuple[float, dict[str, Any]]:
    """Deterministic family score. Missing signals are omitted and weights renormalized."""
    cfg = config or load_family_weights()
    weights: dict[str, float] = dict(cfg.get("weights") or {})
    norm = cfg.get("normalization") or {}

    recurrence = _scale(float(candidate_count), float(norm.get("candidates_at_100", 12)))
    unique_channels = _scale(float(unique_channel_count), float(norm.get("channels_at_100", 8)))

    performance_parts = [
        median_discovery_score,
        _scale(median_views_per_hour, float(norm.get("views_per_hour_at_100", 8000))),
    ]
    performance_parts = [p for p in performance_parts if p is not None]
    performance = sum(performance_parts) / len(performance_parts) if performance_parts else None

    momentum_parts = [
        _scale(median_acceleration, float(norm.get("acceleration_at_100", 2.0))),
        _scale(float(accelerating_count), float(norm.get("accelerating_count_at_100", 4))),
    ]
    momentum_parts = [p for p in momentum_parts if p is not None]
    momentum = sum(momentum_parts) / len(momentum_parts) if momentum_parts else None

    production_parts: list[float] = []
    if ai_leverage_median is not None:
        production_parts.append(_clamp(float(ai_leverage_median) / 5.0 * 100.0))
    if variation_density_median is not None:
        production_parts.append(_clamp(float(variation_density_median) / 5.0 * 100.0))
    if production_complexity_median is not None:
        production_parts.append(_clamp(100.0 - float(production_complexity_median) / 5.0 * 100.0))
    production = sum(production_parts) / len(production_parts) if production_parts else None

    components = {
        "recurrence": recurrence,
        "unique_channels": unique_channels,
        "performance": None if performance is None else _clamp(performance),
        "momentum": None if momentum is None else _clamp(momentum),
        "production": production,
    }
    available = {k: v for k, v in components.items() if v is not None}
    used_weights = {k: float(weights.get(k, 0.0)) for k in available}
    total_w = sum(used_weights.values())
    if total_w <= 0:
        return 0.0, {
            "components": components,
            "used_weights": used_weights,
            "missing": [k for k, v in components.items() if v is None],
            "note": "No usable family signals; score is 0.",
        }
    overall = sum(available[k] * (used_weights[k] / total_w) for k in available)
    return round(_clamp(overall), 2), {
        "components": {k: (None if v is None else round(v, 2)) for k, v in components.items()},
        "used_weights": used_weights,
        "weight_sum_used": round(total_w, 4),
        "missing": [k for k, v in components.items() if v is None],
        "formula": (
            "family_opportunity_score = weighted mean of available signals "
            "(recurrence, unique_channels, performance, momentum, production). "
            "Missing signals are omitted and remaining weights renormalized. "
            "Not a predictive model."
        ),
    }


def evidence_strength(
    *,
    candidate_count: int,
    unique_channel_count: int,
    has_performance: bool,
    config: dict[str, Any] | None = None,
) -> str:
    cfg = (config or load_family_weights()).get("evidence") or {}
    high_ch = int(cfg.get("high_min_channels", 5))
    high_n = int(cfg.get("high_min_candidates", 8))
    med_ch = int(cfg.get("medium_min_channels", 2))
    med_n = int(cfg.get("medium_min_candidates", 3))
    if unique_channel_count >= high_ch and candidate_count >= high_n and has_performance:
        return "HIGH"
    if unique_channel_count >= med_ch and candidate_count >= med_n:
        return "MEDIUM"
    return "LOW"


def family_status(
    *,
    score: float,
    candidate_count: int,
    unique_channel_count: int,
    accelerating_count: int,
    evidence: str,
    config: dict[str, Any] | None = None,
) -> str:
    """LLM never chooses this label."""
    cfg = (config or load_family_weights()).get("status") or {}
    build_score = float(cfg.get("build_min_score", 65))
    build_ch = int(cfg.get("build_min_channels", 3))
    build_n = int(cfg.get("build_min_candidates", 3))
    build_acc = int(cfg.get("build_min_accelerating", 1))
    watch_score = float(cfg.get("watch_min_score", 35))
    watch_n = int(cfg.get("watch_min_candidates", 2))
    watch_ch = int(cfg.get("watch_min_channels", 2))

    if (
        score >= build_score
        and unique_channel_count >= build_ch
        and candidate_count >= build_n
        and accelerating_count >= build_acc
        and evidence in {"MEDIUM", "HIGH"}
    ):
        return "BUILD"
    if score >= watch_score or (candidate_count >= watch_n and unique_channel_count >= watch_ch):
        return "WATCH"
    return "REJECT"


def explain_family_status(
    *,
    status: str,
    evidence: str,
    candidate_count: int,
    unique_channel_count: int,
    median_acceleration: float | None,
    accelerating_count: int,
    ai_leverage_median: float | None,
    variation_density_median: float | None,
    production_complexity_median: float | None,
    score: float,
) -> str:
    accel_text = (
        f"median acceleration is {median_acceleration:.2f}x"
        if median_acceleration is not None
        else "acceleration history is incomplete"
    )
    if status == "BUILD":
        return (
            f"BUILD. {unique_channel_count} independent channels and {candidate_count} live candidates "
            f"show current performance (opportunity score {score:.0f}), with {accelerating_count} accelerating. "
            "Production ratings look manufacturable. This is aggregate evidence, not a prediction."
        )
    if status == "WATCH":
        return (
            f"WATCH. Recurrence across {unique_channel_count} channels ({candidate_count} live candidates, "
            f"evidence {evidence}) and opportunity score {score:.0f}, but {accel_text} "
            f"({accelerating_count} accelerating). Stay in research until independent momentum and evidence improve."
        )
    return (
        f"REJECT. Too little independent live evidence ({candidate_count} candidates, "
        f"{unique_channel_count} channels, evidence {evidence}, score {score:.0f}) "
        "or weak current performance/production economics for a build decision."
    )
