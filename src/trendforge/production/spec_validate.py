from __future__ import annotations

from trendforge.production.spec_schema import ProductionSpecDocument


class SpecValidationError(ValueError):
    pass


def _span_ok(start: float, end: float, duration: float, label: str) -> None:
    if start < -0.001:
        raise SpecValidationError(f"{label} starts before 0s")
    if end - start <= 0:
        raise SpecValidationError(f"{label} has non-positive duration")
    if end > duration + 0.05:
        raise SpecValidationError(f"{label} ends after target duration ({end}s > {duration}s)")


def _sequence_ok(items: list[tuple[float, float, str]]) -> None:
    ordered = sorted(items, key=lambda x: (x[0], x[1]))
    prev_end = 0.0
    prev_label = ""
    for start, end, label in ordered:
        if start + 0.05 < prev_end:
            raise SpecValidationError(f"{label} overlaps {prev_label}")
        prev_end = end
        prev_label = label


def validate_production_spec(spec: ProductionSpecDocument) -> None:
    duration = float(spec.duration_seconds)
    if duration <= 0:
        raise SpecValidationError("duration_seconds must be positive")
    if not (spec.format_family or "").strip():
        raise SpecValidationError("format_family is required")
    if not (spec.primary_mechanic or "").strip():
        raise SpecValidationError("primary_mechanic is required")
    if not (spec.hook or "").strip():
        raise SpecValidationError("hook is required")
    if not (spec.premise or "").strip():
        raise SpecValidationError("premise is required")
    if not spec.story_beats:
        raise SpecValidationError("at least one story beat is required")
    if not spec.shots:
        raise SpecValidationError("at least one shot is required")
    if not spec.characters:
        raise SpecValidationError("at least one character/subject is required")

    beat_spans: list[tuple[float, float, str]] = []
    for i, beat in enumerate(spec.story_beats, start=1):
        _span_ok(beat.start_seconds, beat.end_seconds, duration, f"beat {i}")
        beat_spans.append((beat.start_seconds, beat.end_seconds, f"beat {i}"))
    _sequence_ok(beat_spans)

    shot_spans: list[tuple[float, float, str]] = []
    for shot in spec.shots:
        label = f"shot {shot.shot_number}"
        end = shot.end_seconds if shot.end_seconds > shot.start_seconds else shot.start_seconds + shot.duration_seconds
        _span_ok(shot.start_seconds, end, duration, label)
        shot_spans.append((shot.start_seconds, end, label))
    _sequence_ok(shot_spans)

    names = {c.name.strip().lower() for c in spec.characters if c.name.strip()}
    mentioned = False
    for shot in spec.shots:
        blob = f"{shot.subject} {shot.action} {shot.continuity_notes}".lower()
        if any(name in blob for name in names):
            mentioned = True
    if names and not mentioned:
        raise SpecValidationError("shots never reference listed characters")

    notes = f"{spec.originality_notes} {spec.ip_considerations}".lower()
    banned = ("repost the source", "clone the original video", "download the source")
    if any(phrase in notes for phrase in banned):
        raise SpecValidationError("spec must not depend on copying a source video")
    if not (spec.originality_notes or "").strip():
        raise SpecValidationError("originality_notes is required")
    if not spec.qa_checklist:
        raise SpecValidationError("qa_checklist is required")
    if not spec.required_assets:
        raise SpecValidationError("required_assets is required")
    if not spec.continuity_requirements:
        raise SpecValidationError("continuity_requirements is required")
    generic = {"keep it consistent", "stay consistent", "be consistent"}
    if all(item.strip().lower() in generic for item in spec.continuity_requirements):
        raise SpecValidationError("continuity_requirements must name concrete elements")
