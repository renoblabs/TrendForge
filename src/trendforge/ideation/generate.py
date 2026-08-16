from __future__ import annotations

from collections import defaultdict

from sqlalchemy.orm import Session

from trendforge.config import get_settings
from trendforge.ideation.client import get_ideation_provider
from trendforge.ideation.context import analyzed_candidates, build_family_context, family_key_for, family_members
from trendforge.ideation.parse import dedupe_ideas
from trendforge.ideation.prompt import ALLOWED_COUNTS, DEFAULT_COUNT, IDEATION_PROMPT_VERSION
from trendforge.models import FormatBrainstormSet, utcnow
from trendforge.services import slugify_format_key


class UnknownFamilyError(KeyError):
    pass


class UnknownBrainstormIdeaError(KeyError):
    pass


POOL_PENDING = "pending"
POOL_APPROVED = "approved"
POOL_DENIED = "denied"
POOL_STATUSES = {POOL_PENDING, POOL_APPROVED, POOL_DENIED}


def normalize_count(count: int | None) -> int:
    value = int(count or DEFAULT_COUNT)
    if value in ALLOWED_COUNTS:
        return value
    if value <= 5:
        return 5
    if value <= 10:
        return 10
    return 20


def generate_brainstorm_set(
    db: Session,
    family_key: str,
    *,
    count: int = DEFAULT_COUNT,
    emphasis: str | None = None,
    force_stub: bool = False,
) -> FormatBrainstormSet:
    key = slugify_format_key(family_key)
    members = family_members(analyzed_candidates(db), key)
    if not members:
        raise UnknownFamilyError(key)
    requested = normalize_count(count)
    family = build_family_context(members, key)
    settings = get_settings()
    provider = get_ideation_provider(settings, force_stub=force_stub)
    ideas = provider.generate_ideas(family, count=requested, emphasis=emphasis)
    ideas = dedupe_ideas(ideas)[:requested]
    payload = []
    for idea in ideas:
        row_idea = idea.model_dump()
        row_idea["pool_status"] = POOL_PENDING
        payload.append(row_idea)
    row = FormatBrainstormSet(
        format_family=key,
        generated_at=utcnow(),
        prompt_version=IDEATION_PROMPT_VERSION,
        analyzer=type(provider).__name__,
        model="stub" if type(provider).__name__ == "StubIdeation" else settings.openrouter_model,
        requested_count=requested,
        emphasis=(emphasis or "").strip() or None,
        ideas_json=payload,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def load_brainstorm_sets(
    db: Session,
    *,
    limit_per_family: int = 5,
) -> dict[str, list[FormatBrainstormSet]]:
    rows = (
        db.query(FormatBrainstormSet)
        .order_by(FormatBrainstormSet.generated_at.desc(), FormatBrainstormSet.id.desc())
        .all()
    )
    grouped: dict[str, list[FormatBrainstormSet]] = defaultdict(list)
    for row in rows:
        bucket = grouped[row.format_family]
        if len(bucket) < limit_per_family:
            bucket.append(row)
    return dict(grouped)


def idea_pool_status(idea: dict | None) -> str:
    raw = str((idea or {}).get("pool_status") or POOL_PENDING).strip().lower()
    return raw if raw in POOL_STATUSES else POOL_PENDING


def set_idea_pool_status(db: Session, set_id: int, idea_index: int, status: str) -> FormatBrainstormSet:
    wanted = str(status or "").strip().lower()
    if wanted not in POOL_STATUSES:
        raise ValueError("pool status must be pending, approved, or denied")
    row = db.get(FormatBrainstormSet, set_id)
    if row is None:
        raise UnknownBrainstormIdeaError("brainstorm set not found")
    ideas = list(row.ideas_json or [])
    if idea_index < 0 or idea_index >= len(ideas) or not isinstance(ideas[idea_index], dict):
        raise UnknownBrainstormIdeaError("idea not found")
    updated = dict(ideas[idea_index])
    updated["pool_status"] = wanted
    ideas[idea_index] = updated
    row.ideas_json = ideas
    db.commit()
    db.refresh(row)
    return row


def approve_all_ideas(db: Session, bset: FormatBrainstormSet) -> FormatBrainstormSet:
    ideas = list(bset.ideas_json or [])
    changed = []
    for idea in ideas:
        if isinstance(idea, dict):
            item = dict(idea)
            item["pool_status"] = POOL_APPROVED
            changed.append(item)
        else:
            changed.append(idea)
    bset.ideas_json = changed
    db.commit()
    db.refresh(bset)
    return bset


def list_ideation_families(db: Session) -> list[dict[str, str]]:
    seen: dict[str, str] = {}
    for candidate in analyzed_candidates(db):
        key = family_key_for(candidate)
        data = candidate.analysis_json if isinstance(candidate.analysis_json, dict) else {}
        name = str(data.get("format_name") or key)
        seen.setdefault(key, name)
    for row in db.query(FormatBrainstormSet).all():
        seen.setdefault(row.format_family, row.format_family)
    return [{"id": key, "label": seen[key]} for key in sorted(seen)]


def list_idea_review(db: Session) -> dict[str, list[dict]]:
    rows = (
        db.query(FormatBrainstormSet)
        .order_by(FormatBrainstormSet.generated_at.desc(), FormatBrainstormSet.id.desc())
        .all()
    )
    buckets: dict[str, list[dict]] = {
        POOL_PENDING: [],
        POOL_APPROVED: [],
        POOL_DENIED: [],
    }
    for bset in rows:
        for idx, idea in enumerate(bset.ideas_json or []):
            if not isinstance(idea, dict):
                continue
            status = idea_pool_status(idea)
            buckets[status].append(
                {
                    "set_id": bset.id,
                    "idea_index": idx,
                    "format_family": bset.format_family,
                    "title": idea.get("idea_title") or f"Idea {idx + 1}",
                    "hook": idea.get("hook") or "",
                    "premise": idea.get("premise") or "",
                    "status": status,
                    "generated_at": bset.generated_at,
                }
            )
    return buckets
