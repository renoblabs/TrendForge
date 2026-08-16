from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trendforge.discovery.shorts import is_youtube_short, parse_iso8601_duration
from trendforge.discovery.signals import (
    ObservationPoint,
    acceleration,
    classify_labels,
    comment_rate,
    creator_baseline_views,
    creator_lift,
    like_rate,
    score_discovery,
    views_per_hour,
    compute_signals,
    recency_score,
)


T0 = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)


def pts(*pairs: tuple[int, int]) -> list[ObservationPoint]:
    """(hours_offset, views)"""
    return [
        ObservationPoint(observed_at=T0 + timedelta(hours=h), view_count=v)
        for h, v in pairs
    ]


def test_parse_duration_and_short_filter():
    assert parse_iso8601_duration("PT45S") == 45
    assert parse_iso8601_duration("PT1M3S") == 63
    assert parse_iso8601_duration("PT1H") == 3600
    assert parse_iso8601_duration(None) is None
    short = {"contentDetails": {"duration": "PT58S"}}
    longish = {"contentDetails": {"duration": "PT3M"}}
    missing = {"contentDetails": {}}
    assert is_youtube_short(short, max_seconds=60) is True
    assert is_youtube_short(longish, max_seconds=60) is False
    assert is_youtube_short(missing, max_seconds=60) is False


def test_velocity_single_observation_uses_age():
    published = T0 - timedelta(hours=2)
    obs = [ObservationPoint(observed_at=T0, view_count=20_000)]
    vph = views_per_hour(obs, published_at=published, now=T0)
    assert vph == 10_000


def test_velocity_two_observations_uses_delta():
    obs = pts((0, 120_000), (2, 240_000))
    vph = views_per_hour(obs, published_at=T0 - timedelta(hours=10))
    assert vph == 60_000


def test_acceleration_requires_three_points():
    two = pts((0, 20_000), (2, 100_000))
    assert acceleration(two) is None
    three = pts((0, 20_000), (2, 100_000), (4, 400_000))
    # prev 40k/h, curr 150k/h -> 3.75x
    assert abs(acceleration(three) - 3.75) < 1e-6


def test_acceleration_none_when_previous_velocity_non_positive():
    flat = pts((0, 100), (2, 100), (4, 200))
    assert acceleration(flat) is None


def test_engagement_missing_denominator():
    assert like_rate(10, None) is None
    assert like_rate(None, 100) is None
    assert like_rate(10, 0) is None
    assert like_rate(10, 100) == 0.1
    assert comment_rate(5, 100) == 0.05


def test_creator_baseline_and_lift():
    assert creator_baseline_views([10, 20], min_videos=3) is None
    assert creator_baseline_views([None, 10, 20, 30], min_videos=3) == 20
    assert creator_lift(200, 20) == 10
    assert creator_lift(200, None) is None
    assert creator_lift(None, 20) is None


def test_discovery_score_deterministic_and_renormalizes_missing():
    cfg = {
        "weights": {
            "velocity": 0.30,
            "acceleration": 0.25,
            "engagement": 0.15,
            "creator_lift": 0.20,
            "recency": 0.10,
        },
        "normalization": {
            "velocity_views_per_hour_at_100": 100,
            "acceleration_at_100": 2.0,
            "engagement_at_100": 0.1,
            "creator_lift_at_100": 10.0,
            "recency_hours_full": 6,
            "recency_hours_zero": 168,
        },
    }
    a, _ = score_discovery(
        velocity=50, accel=None, engagement=0.05, lift=None, recency=100, config=cfg
    )
    b, _ = score_discovery(
        velocity=50, accel=None, engagement=0.05, lift=None, recency=100, config=cfg
    )
    assert a == b
    # acceleration and lift omitted; remaining weights 0.30+0.15+0.10=0.55
    vel_c = 50
    eng_c = 50
    rec_c = 100
    expected = (vel_c * 0.30 + eng_c * 0.15 + rec_c * 0.10) / 0.55
    assert abs(a - round(expected, 2)) < 0.02


def test_labels_distinguish_popular_emerging_accelerating():
    popular = classify_labels(
        views=2_000_000,
        age=100,
        vph=1000,
        accel=1.0,
        config={
            "labels": {
                "popular_min_views": 500000,
                "popular_min_age_hours": 72,
                "emerging_max_age_hours": 48,
                "emerging_min_views_per_hour": 5000,
                "accelerating_min_factor": 1.4,
            }
        },
    )
    assert popular == ["POPULAR"]
    emerging = classify_labels(
        views=80_000,
        age=12,
        vph=20_000,
        accel=None,
        config={
            "labels": {
                "popular_min_views": 500000,
                "popular_min_age_hours": 72,
                "emerging_max_age_hours": 48,
                "emerging_min_views_per_hour": 5000,
                "accelerating_min_factor": 1.4,
            }
        },
    )
    assert emerging == ["EMERGING"]
    acc = classify_labels(
        views=80_000,
        age=12,
        vph=20_000,
        accel=2.0,
        config={
            "labels": {
                "popular_min_views": 500000,
                "popular_min_age_hours": 72,
                "emerging_max_age_hours": 48,
                "emerging_min_views_per_hour": 5000,
                "accelerating_min_factor": 1.4,
            }
        },
    )
    assert "ACCELERATING" in acc and "EMERGING" in acc


def test_compute_signals_insufficient_history():
    published = T0 - timedelta(hours=4)
    sig = compute_signals(
        observations=[ObservationPoint(observed_at=T0, view_count=8_000, like_count=100)],
        published_at=published,
        views=8_000,
        likes=100,
        comments=None,
        other_channel_views=[10],
        now=T0,
        min_creator_videos=3,
        weights_config={
            "weights": {
                "velocity": 0.30,
                "acceleration": 0.25,
                "engagement": 0.15,
                "creator_lift": 0.20,
                "recency": 0.10,
            },
            "normalization": {
                "velocity_views_per_hour_at_100": 50000,
                "acceleration_at_100": 3.0,
                "engagement_at_100": 0.08,
                "creator_lift_at_100": 10.0,
                "recency_hours_full": 6,
                "recency_hours_zero": 168,
            },
            "labels": {
                "popular_min_views": 500000,
                "popular_min_age_hours": 72,
                "emerging_max_age_hours": 48,
                "emerging_min_views_per_hour": 5000,
                "accelerating_min_factor": 1.4,
            },
        },
    )
    assert sig.acceleration is None
    assert sig.creator_lift is None
    assert "acceleration" in sig.breakdown["missing"]
    assert "creator_lift" in sig.breakdown["missing"]
    assert sig.like_rate == 100 / 8000
    assert sig.comment_rate is None


def test_recency_score():
    assert recency_score(3, 6, 168) == 100
    assert recency_score(168, 6, 168) == 0
    assert recency_score(None, 6, 168) is None
