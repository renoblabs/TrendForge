from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import median
from typing import Any, Optional, Sequence

from trendforge.config import load_discovery_weights
from trendforge.scoring.engine import clamp


@dataclass
class ObservationPoint:
    observed_at: datetime
    view_count: Optional[int] = None
    like_count: Optional[int] = None
    comment_count: Optional[int] = None


@dataclass
class DiscoverySignals:
    age_hours: Optional[float]
    views_per_hour: Optional[float]
    acceleration: Optional[float]
    like_rate: Optional[float]
    comment_rate: Optional[float]
    engagement: Optional[float]
    creator_baseline: Optional[float]
    creator_baseline_estimated: bool
    creator_lift: Optional[float]
    discovery_score: float
    breakdown: dict[str, Any]
    labels: list[str]


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def age_hours(published_at: datetime | None, now: datetime | None = None) -> float | None:
    if published_at is None:
        return None
    now = _aware(now or datetime.now(timezone.utc))
    delta = now - _aware(published_at)
    hours = delta.total_seconds() / 3600.0
    return max(hours, 0.0)


def views_per_hour(
    observations: Sequence[ObservationPoint],
    *,
    published_at: datetime | None = None,
    now: datetime | None = None,
) -> float | None:
    """
    Best available velocity:
    - 2+ observations: (latest_views - previous_views) / hours between those two points
    - 1 observation: latest_views / age_hours from published_at
    """
    points = [p for p in observations if p.view_count is not None]
    if not points:
        return None
    points = sorted(points, key=lambda p: _aware(p.observed_at))
    latest = points[-1]
    if len(points) >= 2:
        prev = points[-2]
        hours = (_aware(latest.observed_at) - _aware(prev.observed_at)).total_seconds() / 3600.0
        if hours <= 0:
            return None
        return max(0.0, (latest.view_count - prev.view_count) / hours)
    hours = age_hours(published_at, now=now or latest.observed_at)
    if hours is None or hours <= 0:
        return None
    return latest.view_count / hours


def acceleration(observations: Sequence[ObservationPoint]) -> float | None:
    """
    Recent velocity / previous velocity using the last three observation points.
    Returns None when history is insufficient (need 3 points with views).
    """
    points = [p for p in observations if p.view_count is not None]
    points = sorted(points, key=lambda p: _aware(p.observed_at))
    if len(points) < 3:
        return None
    a, b, c = points[-3], points[-2], points[-1]
    h1 = (_aware(b.observed_at) - _aware(a.observed_at)).total_seconds() / 3600.0
    h2 = (_aware(c.observed_at) - _aware(b.observed_at)).total_seconds() / 3600.0
    if h1 <= 0 or h2 <= 0:
        return None
    prev_v = (b.view_count - a.view_count) / h1
    curr_v = (c.view_count - b.view_count) / h2
    if prev_v <= 0:
        return None
    return curr_v / prev_v


def like_rate(likes: int | None, views: int | None) -> float | None:
    if views is None or views <= 0 or likes is None:
        return None
    return likes / views


def comment_rate(comments: int | None, views: int | None) -> float | None:
    if views is None or views <= 0 or comments is None:
        return None
    return comments / views


def creator_baseline_views(
    other_video_views: Sequence[int | None],
    *,
    min_videos: int = 3,
) -> float | None:
    values = [v for v in other_video_views if v is not None and v >= 0]
    if len(values) < min_videos:
        return None
    return float(median(values))


def creator_lift(current_views: int | None, baseline: float | None) -> float | None:
    if current_views is None or baseline is None or baseline <= 0:
        return None
    return current_views / baseline


def _scale(value: float | None, at_100: float) -> float | None:
    if value is None:
        return None
    if at_100 <= 0:
        return 0.0
    return clamp(100.0 * (value / at_100))


def recency_score(age: float | None, hours_full: float, hours_zero: float) -> float | None:
    if age is None:
        return None
    if hours_zero <= hours_full:
        return 100.0 if age <= hours_full else 0.0
    if age <= hours_full:
        return 100.0
    if age >= hours_zero:
        return 0.0
    return clamp(100.0 * (1.0 - (age - hours_full) / (hours_zero - hours_full)))


def classify_labels(
    *,
    views: int | None,
    age: float | None,
    vph: float | None,
    accel: float | None,
    config: dict[str, Any] | None = None,
) -> list[str]:
    labels_cfg = (config or load_discovery_weights()).get("labels", {})
    labels: list[str] = []
    if (
        views is not None
        and views >= float(labels_cfg.get("popular_min_views", 500_000))
        and age is not None
        and age >= float(labels_cfg.get("popular_min_age_hours", 72))
    ):
        labels.append("POPULAR")
    if (
        age is not None
        and age <= float(labels_cfg.get("emerging_max_age_hours", 48))
        and vph is not None
        and vph >= float(labels_cfg.get("emerging_min_views_per_hour", 5000))
    ):
        labels.append("EMERGING")
    if accel is not None and accel >= float(labels_cfg.get("accelerating_min_factor", 1.4)):
        labels.append("ACCELERATING")
    return labels


def score_discovery(
    *,
    velocity: float | None,
    accel: float | None,
    engagement: float | None,
    lift: float | None,
    recency: float | None,
    config: dict[str, Any] | None = None,
) -> tuple[float, dict[str, Any]]:
    cfg = config or load_discovery_weights()
    weights: dict[str, float] = dict(cfg.get("weights") or {})
    norm = cfg.get("normalization") or {}
    components = {
        "velocity": _scale(velocity, float(norm.get("velocity_views_per_hour_at_100", 50_000))),
        "acceleration": _scale(accel, float(norm.get("acceleration_at_100", 3.0))),
        "engagement": _scale(engagement, float(norm.get("engagement_at_100", 0.08))),
        "creator_lift": _scale(lift, float(norm.get("creator_lift_at_100", 10.0))),
        "recency": recency,
    }
    available = {k: v for k, v in components.items() if v is not None}
    used_weights = {k: weights.get(k, 0.0) for k in available}
    total_w = sum(used_weights.values())
    if total_w <= 0:
        return 0.0, {
            "components": components,
            "used_weights": used_weights,
            "missing": [k for k, v in components.items() if v is None],
            "note": "No usable signals; score is 0.",
        }
    overall = sum(available[k] * (used_weights[k] / total_w) for k in available)
    return round(clamp(overall), 2), {
        "components": {k: (None if v is None else round(v, 2)) for k, v in components.items()},
        "used_weights": used_weights,
        "weight_sum_used": round(total_w, 4),
        "missing": [k for k, v in components.items() if v is None],
        "formula": (
            "overall = sum(component_i * weight_i) / sum(available weights). "
            "Missing signals (e.g. acceleration without 3 observations, "
            "creator_lift without enough channel history) are omitted and weights renormalized. "
            "Initial weights are calibration starting points, not a predictive model."
        ),
    }


def compute_signals(
    *,
    observations: Sequence[ObservationPoint],
    published_at: datetime | None,
    views: int | None,
    likes: int | None,
    comments: int | None,
    other_channel_views: Sequence[int | None],
    now: datetime | None = None,
    min_creator_videos: int = 3,
    weights_config: dict[str, Any] | None = None,
) -> DiscoverySignals:
    cfg = weights_config or load_discovery_weights()
    now = now or datetime.now(timezone.utc)
    age = age_hours(published_at, now=now)
    vph = views_per_hour(observations, published_at=published_at, now=now)
    accel = acceleration(observations)
    lr = like_rate(likes, views)
    cr = comment_rate(comments, views)
    engagement = None
    if lr is not None or cr is not None:
        engagement = (lr or 0.0) + (cr or 0.0)
    baseline = creator_baseline_views(other_channel_views, min_videos=min_creator_videos)
    lift = creator_lift(views, baseline)
    recency = recency_score(
        age,
        float((cfg.get("normalization") or {}).get("recency_hours_full", 6)),
        float((cfg.get("normalization") or {}).get("recency_hours_zero", 168)),
    )
    overall, breakdown = score_discovery(
        velocity=vph,
        accel=accel,
        engagement=engagement,
        lift=lift,
        recency=recency,
        config=cfg,
    )
    labels = classify_labels(views=views, age=age, vph=vph, accel=accel, config=cfg)
    return DiscoverySignals(
        age_hours=None if age is None else round(age, 2),
        views_per_hour=None if vph is None else round(vph, 2),
        acceleration=None if accel is None else round(accel, 4),
        like_rate=None if lr is None else round(lr, 6),
        comment_rate=None if cr is None else round(cr, 6),
        engagement=None if engagement is None else round(engagement, 6),
        creator_baseline=None if baseline is None else round(baseline, 2),
        creator_baseline_estimated=baseline is not None,
        creator_lift=None if lift is None else round(lift, 4),
        discovery_score=overall,
        breakdown=breakdown,
        labels=labels,
    )
