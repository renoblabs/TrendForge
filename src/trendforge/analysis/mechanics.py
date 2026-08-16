from __future__ import annotations

from typing import Any

CANONICAL_MECHANICS = (
    "CHARACTER_REPLACEMENT",
    "PERFORMANCE_TRANSFER",
    "IMPOSSIBLE_CHARACTER",
    "RECURRING_CHARACTER",
    "TRANSFORMATION",
    "SWAP",
    "IMPOSSIBLE_POV",
    "MICRO_STORY",
    "ABSURD_REALITY",
    "ROLE_REVERSAL",
    "RECOGNIZABLE_FORMAT_MUTATION",
    "TEMPLATE_REMIX",
    "VISUAL_CONTRADICTION",
    "SURPRISE_REVEAL",
    "BEFORE_AFTER",
    "OTHER",
)

MECHANIC_ALIASES = {
    "character replacement": "CHARACTER_REPLACEMENT",
    "performance transfer": "PERFORMANCE_TRANSFER",
    "impossible character": "IMPOSSIBLE_CHARACTER",
    "recurring character": "RECURRING_CHARACTER",
    "transformation": "TRANSFORMATION",
    "swap": "SWAP",
    "impossible pov": "IMPOSSIBLE_POV",
    "micro story": "MICRO_STORY",
    "micro-story": "MICRO_STORY",
    "absurd reality": "ABSURD_REALITY",
    "role reversal": "ROLE_REVERSAL",
    "recognizable format mutation": "RECOGNIZABLE_FORMAT_MUTATION",
    "template remix": "TEMPLATE_REMIX",
    "visual contradiction": "VISUAL_CONTRADICTION",
    "surprise reveal": "SURPRISE_REVEAL",
    "before after": "BEFORE_AFTER",
    "before/after": "BEFORE_AFTER",
    "emerging": "OTHER",
}

IP_DEPENDENCY_VALUES = (
    "LOW",
    "RECOGNIZABLE_PUBLIC_FORMAT",
    "COPYRIGHTED_CHARACTER",
    "CELEBRITY_PERFORMANCE",
    "SOURCE_VIDEO_REUSE",
)

IP_ALIASES = {
    "low": "LOW",
    "low ip dependence": "LOW",
    "low ip dependency": "LOW",
    "recognizable public format": "RECOGNIZABLE_PUBLIC_FORMAT",
    "specific copyrighted character": "COPYRIGHTED_CHARACTER",
    "copyrighted character": "COPYRIGHTED_CHARACTER",
    "specific celebrity / performance": "CELEBRITY_PERFORMANCE",
    "celebrity": "CELEBRITY_PERFORMANCE",
    "requires source-video reuse": "SOURCE_VIDEO_REUSE",
    "source video reuse": "SOURCE_VIDEO_REUSE",
}

CONFIDENCE_VALUES = ("high", "medium", "low")


def _token(value: Any) -> str:
    return str(value or "").strip().upper().replace("-", "_").replace(" ", "_")


def normalize_mechanic(value: Any) -> str:
    if value is None or str(value).strip() == "":
        return "OTHER"
    raw = str(value).strip()
    token = _token(raw)
    if token in CANONICAL_MECHANICS:
        return token
    alias = MECHANIC_ALIASES.get(raw.lower())
    if alias:
        return alias
    alias = MECHANIC_ALIASES.get(token.lower().replace("_", " "))
    if alias:
        return alias
    return "OTHER"


def normalize_mechanics(values: Any, *, primary: str | None = None) -> list[str]:
    if values is None:
        values = []
    if isinstance(values, str):
        values = [values]
    seen: list[str] = []
    for item in values:
        code = normalize_mechanic(item)
        if primary and code == primary:
            continue
        if code not in seen:
            seen.append(code)
    return seen


def normalize_ip_dependency(value: Any) -> str:
    if value is None or str(value).strip() == "":
        return "LOW"
    raw = str(value).strip()
    token = _token(raw)
    if token in IP_DEPENDENCY_VALUES:
        return token
    return IP_ALIASES.get(raw.lower(), "LOW")


def normalize_confidence(value: Any) -> str:
    text = str(value or "medium").strip().lower()
    if text in CONFIDENCE_VALUES:
        return text
    return "medium"


def clamp_rating(value: Any, default: int = 3) -> int:
    if value is None or value == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number > 5:
        number = round(number / 20.0) if number <= 100 else 5
    rating = int(round(number))
    return max(1, min(5, rating))
