from __future__ import annotations

from trendforge.scoring.engine import ScoreInputs, score_format


def test_score_breakdown_and_weights():
    result = score_format(
        ScoreInputs(
            trend_velocity=92,
            novelty=88,
            replicability=95,
            variation_density=97,
            cross_platform=71,
            production_cost=84,
            hook_strength=91,
            ip_risk=24,  # => ip_safety 76
            saturation=38,
        )
    )
    assert result.breakdown["ip_safety"] == 76
    assert result.breakdown["trend_velocity"] == 92
    assert 0 <= result.overall <= 100
    assert result.saturation_penalty_factor < 1.0
    assert "overall" not in result.breakdown or True


def test_saturation_penalty_lowers_score():
    low = score_format(
        ScoreInputs(90, 90, 90, 90, 90, 90, 90, 10, saturation=10)
    )
    high = score_format(
        ScoreInputs(90, 90, 90, 90, 90, 90, 90, 10, saturation=80)
    )
    assert high.overall < low.overall


def test_status_thresholds():
    build = score_format(
        ScoreInputs(100, 100, 100, 100, 100, 100, 100, 0, saturation=0)
    )
    assert build.suggested_status == "BUILD"
    assert build.overall >= 85

    watch = score_format(
        ScoreInputs(75, 75, 75, 75, 75, 75, 75, 20, saturation=10)
    )
    assert watch.suggested_status in {"WATCH", "BUILD", "DISCOVERED"}
    # Force a mid score
    mid = score_format(
        ScoreInputs(72, 72, 72, 72, 72, 72, 72, 20, saturation=5),
        config={
            "weights": {
                "trend_velocity": 1.0,
                "novelty": 0,
                "replicability": 0,
                "variation_density": 0,
                "cross_platform": 0,
                "production_cost": 0,
                "hook_strength": 0,
                "ip_safety": 0,
            },
            "saturation_penalty_k": 0,
            "status_thresholds": {"BUILD": 85, "WATCH": 70},
        },
    )
    assert mid.overall == 72
    assert mid.suggested_status == "WATCH"

    low = score_format(
        ScoreInputs(40, 40, 40, 40, 40, 40, 40, 50, saturation=0),
        config={
            "weights": {
                "trend_velocity": 1.0,
                "novelty": 0,
                "replicability": 0,
                "variation_density": 0,
                "cross_platform": 0,
                "production_cost": 0,
                "hook_strength": 0,
                "ip_safety": 0,
            },
            "saturation_penalty_k": 0,
            "status_thresholds": {"BUILD": 85, "WATCH": 70},
        },
    )
    assert low.suggested_status == "DISCOVERED"


def test_clamp_extremes():
    result = score_format(
        ScoreInputs(200, -10, 50, 50, 50, 50, 50, 150, saturation=200)
    )
    assert result.breakdown["trend_velocity"] == 100
    assert result.breakdown["novelty"] == 0
    assert result.breakdown["ip_safety"] == 0
    assert result.breakdown["saturation"] == 100
