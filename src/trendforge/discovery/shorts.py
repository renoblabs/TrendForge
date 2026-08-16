from __future__ import annotations

import re
from typing import Any

ISO_DURATION = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)(?:\.\d+)?S)?)?$"
)


def parse_iso8601_duration(value: str | None) -> float | None:
    """Parse YouTube contentDetails.duration (ISO 8601) to seconds."""
    if not value or not isinstance(value, str):
        return None
    match = ISO_DURATION.match(value.strip().upper())
    if not match:
        return None
    days = int(match.group("days") or 0)
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds") or 0)
    total = days * 86400 + hours * 3600 + minutes * 60 + seconds
    return float(total)


def is_youtube_short(
    video: dict[str, Any],
    *,
    max_seconds: float = 60.0,
) -> bool:
    """
    Isolate Shorts filtering so it can be tightened later.

    v1 rule: parse contentDetails.duration and keep videos at or under max_seconds.
    YouTube search videoDuration=short is <4 minutes, so duration filtering is required.
    """
    details = video.get("contentDetails") or {}
    duration = parse_iso8601_duration(details.get("duration"))
    if duration is None:
        return False
    return duration <= max_seconds
