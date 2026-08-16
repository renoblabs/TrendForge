from __future__ import annotations

from sqlalchemy.orm import Session

from trendforge.config import get_settings
from trendforge.ideation.context import analyzed_candidates, build_family_context, family_members
from trendforge.models import FormatBrainstormSet, ProductionSpec, ProductionSpecStatus, utcnow
from trendforge.production.spec_client import get_spec_provider
from trendforge.production.spec_prompt import (
    DEFAULT_ASPECT_RATIO,
    DEFAULT_DURATION_SECONDS,
    SPEC_PROMPT_VERSION,
)
from trendforge.production.spec_validate import SpecValidationError, validate_production_spec
from trendforge.services import slugify_format_key


class UnknownIdeaError(KeyError):
    pass


def _idea_from_set(row: FormatBrainstormSet, idea_index: int) -> dict:
    ideas = row.ideas_json if isinstance(row.ideas_json, list) else []
    if idea_index < 0 or idea_index >= len(ideas):
        raise UnknownIdeaError(str(idea_index))
    idea = ideas[idea_index]
    if not isinstance(idea, dict):
        raise UnknownIdeaError(str(idea_index))
    return idea


def generate_production_spec(
    db: Session,
    *,
    brainstorm_set_id: int,
    idea_index: int,
    duration_seconds: float = DEFAULT_DURATION_SECONDS,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
    force_stub: bool = False,
) -> ProductionSpec:
    row = db.get(FormatBrainstormSet, brainstorm_set_id)
    if not row:
        raise UnknownIdeaError("brainstorm set not found")
    idea = _idea_from_set(row, idea_index)
    family_key = slugify_format_key(row.format_family)
    members = family_members(analyzed_candidates(db), family_key)
    family = build_family_context(members, family_key) if members else {
        "format_family": family_key,
        "format_name": family_key,
        "format_key": family_key,
        "primary_mechanic": idea.get("primary_mechanic") or "",
        "secondary_mechanics": [],
        "format_hypothesis": "",
    }
    duration = float(duration_seconds or DEFAULT_DURATION_SECONDS)
    settings = get_settings()
    provider = get_spec_provider(settings, force_stub=force_stub)
    spec = provider.generate_spec(
        family=family,
        idea=idea,
        duration_seconds=duration,
        aspect_ratio=aspect_ratio or DEFAULT_ASPECT_RATIO,
    )
    spec.format_family = family_key
    if family.get("primary_mechanic") and not spec.primary_mechanic:
        spec.primary_mechanic = str(family["primary_mechanic"])
    if family.get("format_hypothesis") and not spec.format_hypothesis:
        spec.format_hypothesis = str(family["format_hypothesis"])
    validate_production_spec(spec)
    record = ProductionSpec(
        created_at=utcnow(),
        format_family=family_key,
        source_brainstorm_set_id=row.id,
        source_idea_identifier=str(idea_index),
        prompt_version=SPEC_PROMPT_VERSION,
        analyzer=type(provider).__name__,
        model="stub" if type(provider).__name__ == "StubSpec" else settings.openrouter_model,
        status=ProductionSpecStatus.READY,
        spec_json=spec.model_dump(),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def find_latest_spec(
    db: Session, *, brainstorm_set_id: int, idea_index: int
) -> ProductionSpec | None:
    return (
        db.query(ProductionSpec)
        .filter(
            ProductionSpec.source_brainstorm_set_id == brainstorm_set_id,
            ProductionSpec.source_idea_identifier == str(idea_index),
        )
        .order_by(ProductionSpec.created_at.desc(), ProductionSpec.id.desc())
        .first()
    )


def specs_by_brainstorm(db: Session) -> dict[tuple[int, str], list[ProductionSpec]]:
    rows = db.query(ProductionSpec).order_by(ProductionSpec.created_at.desc()).all()
    grouped: dict[tuple[int, str], list[ProductionSpec]] = {}
    for row in rows:
        key = (int(row.source_brainstorm_set_id or 0), str(row.source_idea_identifier))
        grouped.setdefault(key, []).append(row)
    return grouped
