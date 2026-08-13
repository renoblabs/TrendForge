from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ProductionMethod(str, Enum):
    CHARACTER_REPLACEMENT = "character_replacement"
    MOTION_TRANSFER = "motion_transfer"
    PERFORMANCE_TRANSFER = "performance_transfer"
    REFERENCE_TO_VIDEO = "reference_to_video"
    IMAGE_TO_VIDEO = "image_to_video"
    VIDEO_TO_VIDEO = "video_to_video"
    LIP_SYNC_AVATAR = "lip_sync_avatar"
    TRANSFORMATION = "transformation"
    CHARACTER_CONSISTENCY = "character_consistency"


@dataclass(frozen=True)
class ProductionEstimate:
    method: ProductionMethod
    label: str
    typical_cost_usd: float
    complexity_0_100: float
    notes: str


PRODUCTION_CATALOG: dict[ProductionMethod, ProductionEstimate] = {
    ProductionMethod.CHARACTER_REPLACEMENT: ProductionEstimate(
        ProductionMethod.CHARACTER_REPLACEMENT,
        "Character replacement",
        3.0,
        45.0,
        "Swap performer identity while preserving motion/timing.",
    ),
    ProductionMethod.MOTION_TRANSFER: ProductionEstimate(
        ProductionMethod.MOTION_TRANSFER,
        "Motion transfer",
        4.0,
        55.0,
        "Drive a new character with reference motion.",
    ),
    ProductionMethod.PERFORMANCE_TRANSFER: ProductionEstimate(
        ProductionMethod.PERFORMANCE_TRANSFER,
        "Performance transfer",
        5.0,
        60.0,
        "Transfer timing, gesture, and delivery energy to a new character.",
    ),
    ProductionMethod.REFERENCE_TO_VIDEO: ProductionEstimate(
        ProductionMethod.REFERENCE_TO_VIDEO,
        "Reference-to-video",
        3.5,
        50.0,
        "Generate video from still/reference identity + prompt.",
    ),
    ProductionMethod.IMAGE_TO_VIDEO: ProductionEstimate(
        ProductionMethod.IMAGE_TO_VIDEO,
        "Image-to-video",
        2.0,
        35.0,
        "Animate a still into a short clip.",
    ),
    ProductionMethod.VIDEO_TO_VIDEO: ProductionEstimate(
        ProductionMethod.VIDEO_TO_VIDEO,
        "Video-to-video",
        4.5,
        58.0,
        "Restyle or reimagine an existing motion clip.",
    ),
    ProductionMethod.LIP_SYNC_AVATAR: ProductionEstimate(
        ProductionMethod.LIP_SYNC_AVATAR,
        "Lip sync / avatar",
        2.5,
        40.0,
        "Talking-head or avatar with synced audio.",
    ),
    ProductionMethod.TRANSFORMATION: ProductionEstimate(
        ProductionMethod.TRANSFORMATION,
        "Transformation",
        3.0,
        48.0,
        "Morph / reveal / before-after transformation beats.",
    ),
    ProductionMethod.CHARACTER_CONSISTENCY: ProductionEstimate(
        ProductionMethod.CHARACTER_CONSISTENCY,
        "Character consistency",
        3.5,
        52.0,
        "Recurring character identity across many clips.",
    ),
}


def get_production_estimate(method: str | ProductionMethod) -> ProductionEstimate | None:
    try:
        key = method if isinstance(method, ProductionMethod) else ProductionMethod(method)
    except ValueError:
        return None
    return PRODUCTION_CATALOG.get(key)


def list_production_methods() -> list[ProductionEstimate]:
    return list(PRODUCTION_CATALOG.values())
