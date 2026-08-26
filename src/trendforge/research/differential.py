from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy.orm import Session

from trendforge.analysis.client import MissingAPIKeyError
from trendforge.config import Settings, get_settings, load_discovery_weights
from trendforge.models import (
    CandidateObservation,
    CohortDifferentialAnalysis,
    ContentCandidate,
    ResearchCohort,
    ResearchCohortMember,
    utcnow,
)


PROMPT_VERSION = "cohort-differential-v1"
EVIDENCE_MODE = "metadata_plus_observations"


class DifferentialAnalysisError(RuntimeError):
    """The requested cohort cannot support a valid differential analysis."""


class SupportedFinding(BaseModel):
    """A hypothesis with explicit in-cohort support references."""

    model_config = ConfigDict(extra="forbid")

    hypothesis: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    candidate_ids: list[int] = Field(default_factory=list)
    creators: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "medium"

    @model_validator(mode="after")
    def require_support_reference(self) -> "SupportedFinding":
        if not self.candidate_ids and not self.creators:
            raise ValueError("a finding must cite at least one candidate ID or creator")
        return self


class CandidateLevelNote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: int
    creator: str
    outcome: str
    note: str = Field(min_length=1)


class DifferentialResult(BaseModel):
    """Structured, outcome-aware hypotheses. These are not scoring inputs."""

    model_config = ConfigDict(extra="forbid")

    breakout_commonalities: list[SupportedFinding] = Field(default_factory=list)
    stall_commonalities: list[SupportedFinding] = Field(default_factory=list)
    candidate_level_notes: list[CandidateLevelNote] = Field(default_factory=list)
    possible_discriminators: list[SupportedFinding] = Field(default_factory=list)
    counterexamples: list[SupportedFinding] = Field(default_factory=list)
    false_negative_hypotheses: list[SupportedFinding] = Field(default_factory=list)
    features_worth_collecting: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"]
    limitations: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class DifferentialContext:
    cohort_id: int
    evidence_mode: str
    evidence_available: list[str]
    evidence_missing: list[str]
    input_candidate_ids: list[int]
    input_evidence: dict[str, Any]
    groups: dict[str, list[int]]
    creators_by_id: dict[int, str]
    outcomes_by_id: dict[int, str]


@dataclass(frozen=True)
class DifferentialProviderResponse:
    result: DifferentialResult
    provider: str
    model: str
    usage: dict[str, Any] | None = None
    actual_cost: float | None = None
    currency: str | None = None


class DifferentialProvider(Protocol):
    def analyze(self, context: DifferentialContext) -> DifferentialProviderResponse: ...


def parse_differential_result(raw: Any) -> DifferentialResult:
    if isinstance(raw, DifferentialResult):
        return raw
    if not isinstance(raw, dict):
        raise ValueError("Differential analysis must be a JSON object")
    try:
        return DifferentialResult.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"Invalid differential analysis payload: {exc}") from exc


def _enum_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip().upper()


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    aware = _aware(value)
    return aware.isoformat() if aware else None


def _observation_payload(observation: CandidateObservation | None) -> dict[str, Any] | None:
    if observation is None:
        return None
    views = observation.view_count
    likes = observation.like_count
    comments = observation.comment_count
    engagement = None
    if views is not None and views > 0 and (likes is not None or comments is not None):
        engagement = round(((likes or 0) + (comments or 0)) / views, 6)
    return {
        "observation_id": observation.id,
        "observed_at": _iso(observation.observed_at),
        "views": views,
        "likes": likes,
        "comments": comments,
        "engagement_rate_like_plus_comment": engagement,
        "creator_followers": observation.channel_subscriber_count,
    }


def _interval_payload(
    first: CandidateObservation | None,
    second: CandidateObservation | None,
) -> dict[str, Any]:
    if first is None or second is None:
        return {
            "elapsed_hours": None,
            "views_gained": None,
            "views_per_hour": None,
            "relative_growth_multiple": None,
        }
    first_at = _aware(first.observed_at)
    second_at = _aware(second.observed_at)
    if first_at is None or second_at is None:
        hours = None
    else:
        hours = (second_at - first_at).total_seconds() / 3600.0
    if first.view_count is None or second.view_count is None:
        gain = None
    else:
        gain = second.view_count - first.view_count
    velocity = None if gain is None or hours is None or hours <= 0 else gain / hours
    multiple = (
        None
        if first.view_count is None
        or first.view_count <= 0
        or second.view_count is None
        else second.view_count / first.view_count
    )
    return {
        "elapsed_hours": None if hours is None else round(hours, 4),
        "views_gained": gain,
        "views_per_hour": None if velocity is None else round(velocity, 2),
        "relative_growth_multiple": None if multiple is None else round(multiple, 4),
    }


def _member_observation(
    db: Session,
    member: ResearchCohortMember,
    relationship_name: str,
    id_name: str,
) -> CandidateObservation | None:
    observation = getattr(member, relationship_name, None)
    if observation is not None:
        return observation
    observation_id = getattr(member, id_name, None)
    return db.get(CandidateObservation, observation_id) if observation_id else None


def _candidate_for_member(
    db: Session,
    member: ResearchCohortMember,
) -> ContentCandidate | None:
    candidate = getattr(member, "candidate", None)
    if candidate is not None:
        return candidate
    candidate_id = getattr(member, "candidate_id", None)
    return db.get(ContentCandidate, candidate_id) if candidate_id else None


def _prior_valid_observation(
    candidate: ContentCandidate,
    t0: CandidateObservation | None,
) -> CandidateObservation | None:
    if t0 is None:
        return None
    t0_at = _aware(t0.observed_at)
    eligible = [
        observation
        for observation in (candidate.observations or [])
        if observation.id != t0.id
        and observation.view_count is not None
        and _aware(observation.observed_at) is not None
        and _aware(observation.observed_at) < t0_at
    ]
    return max(eligible, key=lambda item: _aware(item.observed_at)) if eligible else None


def _t1_acceleration(
    prior: CandidateObservation | None,
    t0: CandidateObservation | None,
    t1: CandidateObservation | None,
) -> float | None:
    previous = _interval_payload(prior, t0).get("views_per_hour")
    current = _interval_payload(t0, t1).get("views_per_hour")
    if previous is None or current is None or previous <= 0:
        return None
    return round(current / previous, 4)


def _safe_acquisition_metadata(candidate: ContentCandidate) -> dict[str, Any]:
    raw = candidate.raw_metadata if isinstance(candidate.raw_metadata, dict) else {}
    history = raw.get("creator_history")
    history_payload = None
    if isinstance(history, dict):
        history_payload = {
            "baseline_sample_size": history.get("baseline_sample_size"),
            "historical_content_count": history.get("historical_content_count"),
            "creator_baseline_views": history.get("creator_baseline_views"),
            "creator_lift": history.get("creator_lift"),
        }
    return {
        "audio_name_metadata": raw.get("audio"),
        "language_signal": raw.get("language_signal"),
        "region_signal": raw.get("region_signal"),
        "audience_relevance": raw.get("audience_relevance"),
        "acquisition_profile": raw.get("acquisition_profile"),
        "source_actor": raw.get("source_actor") or raw.get("actor_id"),
        "creator_history": history_payload,
    }


def _clean_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(value) for value in values if value not in (None, "")]


def _group_name(member: ResearchCohortMember) -> str:
    outcome = _enum_value(getattr(member, "outcome_label", None))
    censored = bool(getattr(member, "right_censored", False))
    if censored:
        return "right_censored_context"
    if outcome == "BREAKOUT":
        return "breakout"
    if outcome == "STALLED":
        return "stalled"
    if outcome in {"STRONG_RISER", "MODERATE"}:
        return "context"
    return "unclassified_context"


def build_differential_context(
    db: Session,
    cohort: ResearchCohort | int,
    *,
    weights_config: dict[str, Any] | None = None,
) -> DifferentialContext:
    row = db.get(ResearchCohort, cohort) if isinstance(cohort, int) else cohort
    if row is None:
        raise DifferentialAnalysisError("research cohort not found")
    members = (
        db.query(ResearchCohortMember)
        .filter(ResearchCohortMember.cohort_id == row.id)
        .order_by(ResearchCohortMember.candidate_id.asc())
        .all()
    )
    if not members:
        raise DifferentialAnalysisError("research cohort has no members")

    weights = weights_config or load_discovery_weights()
    labels_config = dict(weights.get("labels") or {})
    emerging_min_vph = float(labels_config.get("emerging_min_views_per_hour", 5000))
    accelerating_min_factor = float(labels_config.get("accelerating_min_factor", 1.4))

    evidence_rows: list[dict[str, Any]] = []
    groups: dict[str, list[int]] = {
        "breakout": [],
        "stalled": [],
        "context": [],
        "right_censored_context": [],
        "unclassified_context": [],
        "false_negative_breakouts": [],
    }
    creators_by_id: dict[int, str] = {}
    outcomes_by_id: dict[int, str] = {}

    for member in members:
        candidate = _candidate_for_member(db, member)
        if candidate is None:
            raise DifferentialAnalysisError(
                f"cohort member {getattr(member, 'id', '?')} has no candidate"
            )
        t0 = _member_observation(db, member, "t0_observation", "t0_observation_id")
        t1 = _member_observation(db, member, "t1_observation", "t1_observation_id")
        t2 = _member_observation(db, member, "t2_observation", "t2_observation_id")
        prior = _prior_valid_observation(candidate, t0)
        t0_t1 = _interval_payload(t0, t1)
        t1_t2 = _interval_payload(t1, t2)
        t1_acceleration = _t1_acceleration(prior, t0, t1)
        reconstructed_high_signal = bool(
            (
                t0_t1["views_per_hour"] is not None
                and t0_t1["views_per_hour"] >= emerging_min_vph
            )
            or (
                t1_acceleration is not None
                and t1_acceleration >= accelerating_min_factor
            )
        )
        group = _group_name(member)
        candidate_id = int(candidate.id)
        creator = str(candidate.creator or "unknown")
        outcome = _enum_value(getattr(member, "outcome_label", None))
        groups[group].append(candidate_id)
        creators_by_id[candidate_id] = creator
        outcomes_by_id[candidate_id] = outcome
        if group == "breakout" and not reconstructed_high_signal:
            groups["false_negative_breakouts"].append(candidate_id)

        evidence_rows.append(
            {
                "candidate_id": candidate_id,
                "creator": creator,
                "outcome": outcome or None,
                "analysis_group": group,
                "right_censored": bool(getattr(member, "right_censored", False)),
                "censor_reason": getattr(member, "censor_reason", None),
                "metadata": {
                    "platform": candidate.platform,
                    "external_id": candidate.external_id,
                    "url": candidate.url,
                    "source_query": candidate.source_query,
                    "caption": candidate.title or candidate.description,
                    "hashtags": _clean_list(candidate.hashtags),
                    "published_at": _iso(candidate.published_at),
                    "duration_seconds": candidate.duration,
                    "thumbnail_url_available": bool(candidate.thumbnail_url),
                    "thumbnail_was_inspected": False,
                    "acquisition": _safe_acquisition_metadata(candidate),
                },
                "observations": {
                    "t0": _observation_payload(t0),
                    "t1": _observation_payload(t1),
                    "t2": _observation_payload(t2),
                },
                "early_movement": {
                    "t0_to_t1": t0_t1,
                    "t1_acceleration_factor": t1_acceleration,
                    "high_signal_at_t1_reconstructed": reconstructed_high_signal,
                },
                "later_movement": {"t1_to_t2": t1_t2},
            }
        )

    if not groups["breakout"] or not groups["stalled"]:
        raise DifferentialAnalysisError(
            "differential analysis requires uncensored BREAKOUT and STALLED groups"
        )

    available = [
        "creator_and_caption_metadata",
        "hashtags_and_source_query",
        "published_time_and_duration",
        "creator_follower_counts_where_present",
        "t0_t1_t2_view_like_comment_observations",
        "safe_acquisition_metadata",
        "thumbnail_url_metadata_only",
    ]
    missing = [
        "video_frames_or_visual_inspection",
        "audio_playback_or_transcript",
        "subtitles",
        "viewer_retention_watch_time_or_rewatches",
        "comment_text",
        "longitudinal_share_and_save_counts",
        "reliable_creator_baseline_or_creator_lift",
        "existing_format_intelligence_analysis",
        "historically_persisted_t1_discovery_labels",
    ]
    input_ids = sorted(creators_by_id)
    evidence = {
        "cohort": {
            "id": row.id,
            "name": getattr(row, "name", None) or getattr(row, "label", None),
            "source": getattr(row, "source", None),
            "profile": getattr(row, "profile", None),
        },
        "evidence_mode": EVIDENCE_MODE,
        "evidence_available": available,
        "evidence_missing": missing,
        "group_candidate_ids": groups,
        "gate_reconstruction": {
            "emerging_min_views_per_hour": emerging_min_vph,
            "accelerating_min_factor": accelerating_min_factor,
            "note": (
                "Historical T1 labels were not stored per observation. High-signal state is "
                "reconstructed from observation history and the supplied deterministic thresholds."
            ),
        },
        "candidates": evidence_rows,
    }
    return DifferentialContext(
        cohort_id=int(row.id),
        evidence_mode=EVIDENCE_MODE,
        evidence_available=available,
        evidence_missing=missing,
        input_candidate_ids=input_ids,
        input_evidence=evidence,
        groups=groups,
        creators_by_id=creators_by_id,
        outcomes_by_id=outcomes_by_id,
    )


SYSTEM_PROMPT = """You are conducting outcome-aware short-form trend research for TrendForge.

Compare candidates collected through one cohort experiment that later broke out with clean
stalled controls. Identify testable structural hypotheses, not causes and not a new score.

Evidence discipline:
- You receive metadata and metric observations only. You have not watched or heard any clip.
- Never claim a visual, spoken, audio, editing, or narrative detail that is not explicit in the metadata.
- Treat right-censored candidates only as context, never as clean negative outcomes.
- Treat STRONG_RISER and uncensored MODERATE candidates as context/counterexamples.
- Every group-level hypothesis must cite supporting candidate IDs or creator handles.
- Pay particular attention to BREAKOUT candidates whose reconstructed T1 gate was false.
- Findings are hypotheses and associations, not proven causal mechanisms.
- Do not propose or apply changes to discovery scores, labels, thresholds, or lifecycle state.
- Return JSON only, with no Markdown.
"""


def build_differential_user_prompt(context: DifferentialContext) -> str:
    finding_shape = {
        "hypothesis": "testable hypothesis",
        "evidence": "specific stored evidence",
        "candidate_ids": [1],
        "creators": ["creator"],
        "confidence": "medium",
    }
    schema = {
        "breakout_commonalities": [finding_shape],
        "stall_commonalities": [finding_shape],
        "candidate_level_notes": [
            {
                "candidate_id": 1,
                "creator": "creator",
                "outcome": "BREAKOUT",
                "note": "evidence-grounded note",
            }
        ],
        "possible_discriminators": [finding_shape],
        "counterexamples": [finding_shape],
        "false_negative_hypotheses": [finding_shape],
        "features_worth_collecting": ["feature"],
        "confidence": "medium",
        "limitations": ["limitation"],
    }
    return "\n".join(
        [
            f"Prompt version: {PROMPT_VERSION}",
            "Analyze the frozen cohort evidence below.",
            "Provide one candidate_level_note for every candidate.",
            (
                "Cover every ID in group_candidate_ids.false_negative_breakouts in "
                "false_negative_hypotheses."
            ),
            "Required JSON shape:",
            json.dumps(schema, separators=(",", ":")),
            "Cohort evidence:",
            json.dumps(context.input_evidence, sort_keys=True, separators=(",", ":")),
        ]
    )


def _response_content(data: dict[str, Any]) -> str:
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("OpenRouter differential response missing message content") from exc
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    if not isinstance(content, str):
        raise ValueError("OpenRouter differential response content must be text")
    return content


def _reported_cost(usage: dict[str, Any] | None) -> float | None:
    if not isinstance(usage, dict):
        return None
    for key in ("cost", "total_cost", "totalCost"):
        value = usage.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


class OpenRouterDifferentialProvider:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def analyze(self, context: DifferentialContext) -> DifferentialProviderResponse:
        if not self.settings.has_openrouter:
            raise MissingAPIKeyError(
                "OPENROUTER_API_KEY is not set; live cohort differential analysis remains pending."
            )
        payload = {
            "model": self.settings.openrouter_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_differential_user_prompt(context)},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://localhost/trendforge",
            "X-Title": "TrendForge",
        }
        url = f"{self.settings.openrouter_base_url.rstrip('/')}/chat/completions"
        with httpx.Client(timeout=90.0) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        content = _response_content(data)
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError("Differential analysis response was not valid JSON") from exc
        result = parse_differential_result(parsed)
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
        actual_cost = _reported_cost(usage)
        return DifferentialProviderResponse(
            result=result,
            provider="openrouter",
            model=str(data.get("model") or self.settings.openrouter_model),
            usage=usage,
            actual_cost=actual_cost,
            currency="USD" if actual_cost is not None else None,
        )


def _finding_ids(
    finding: SupportedFinding,
    context: DifferentialContext,
) -> set[int]:
    valid_ids = set(context.input_candidate_ids)
    unknown_ids = set(finding.candidate_ids) - valid_ids
    if unknown_ids:
        raise ValueError(f"finding cites candidates outside the cohort: {sorted(unknown_ids)}")
    creator_lookup = {
        creator.casefold(): candidate_id
        for candidate_id, creator in context.creators_by_id.items()
    }
    ids = set(finding.candidate_ids)
    for creator in finding.creators:
        candidate_id = creator_lookup.get(str(creator).casefold())
        if candidate_id is None:
            raise ValueError(f"finding cites creator outside the cohort: {creator}")
        ids.add(candidate_id)
    return ids


def validate_result_support(
    result: DifferentialResult,
    context: DifferentialContext,
) -> DifferentialResult:
    finding_fields = (
        "breakout_commonalities",
        "stall_commonalities",
        "possible_discriminators",
        "counterexamples",
        "false_negative_hypotheses",
    )
    finding_ids: dict[str, list[set[int]]] = {}
    for field_name in finding_fields:
        finding_ids[field_name] = [
            _finding_ids(finding, context) for finding in getattr(result, field_name)
        ]

    if not result.breakout_commonalities:
        raise ValueError("differential result needs at least one breakout commonality")
    if not result.stall_commonalities:
        raise ValueError("differential result needs at least one stall commonality")
    breakout_ids = set(context.groups["breakout"])
    stalled_ids = set(context.groups["stalled"])
    for support in finding_ids["breakout_commonalities"]:
        if not support.intersection(breakout_ids):
            raise ValueError("breakout commonality does not cite a BREAKOUT candidate")
    for support in finding_ids["stall_commonalities"]:
        if not support.intersection(stalled_ids):
            raise ValueError("stall commonality does not cite a clean STALLED candidate")

    false_negative_ids = set(context.groups["false_negative_breakouts"])
    cited_false_negatives: set[int] = set()
    for support in finding_ids["false_negative_hypotheses"]:
        cited_false_negatives.update(support.intersection(false_negative_ids))
    missing_false_negatives = false_negative_ids - cited_false_negatives
    if missing_false_negatives:
        raise ValueError(
            "false-negative findings omit candidates: "
            f"{sorted(missing_false_negatives)}"
        )

    notes_by_id: dict[int, CandidateLevelNote] = {}
    for note in result.candidate_level_notes:
        if note.candidate_id not in context.creators_by_id:
            raise ValueError(
                f"candidate-level note cites candidate outside cohort: {note.candidate_id}"
            )
        expected_creator = context.creators_by_id[note.candidate_id]
        if note.creator.casefold() != expected_creator.casefold():
            raise ValueError(
                f"candidate-level note creator does not match candidate {note.candidate_id}"
            )
        expected_outcome = context.outcomes_by_id[note.candidate_id]
        if note.outcome.strip().upper() != expected_outcome:
            raise ValueError(
                f"candidate-level note outcome does not match candidate {note.candidate_id}"
            )
        if note.candidate_id in notes_by_id:
            raise ValueError(f"duplicate candidate-level note: {note.candidate_id}")
        notes_by_id[note.candidate_id] = note
    missing_notes = set(context.input_candidate_ids) - set(notes_by_id)
    if missing_notes:
        raise ValueError(f"candidate-level notes omit candidates: {sorted(missing_notes)}")

    required_limitations = [
        "The analysis used metadata and metric observations only; no clip was watched or heard.",
        (
            "Historical T1 high-signal state was reconstructed because discovery labels were not "
            "persisted per observation."
        ),
        "Associations in this result are hypotheses, not proven causal findings.",
    ]
    limitations = list(result.limitations)
    known = {item.casefold() for item in limitations}
    for limitation in required_limitations:
        if limitation.casefold() not in known:
            limitations.append(limitation)
    return result.model_copy(update={"limitations": limitations})


def run_cohort_differential_analysis(
    db: Session,
    cohort_id: int,
    *,
    provider: DifferentialProvider | None = None,
    settings: Settings | None = None,
    weights_config: dict[str, Any] | None = None,
) -> CohortDifferentialAnalysis:
    """Run one research-only comparison and append its immutable result.

    No candidate, observation, score, label, format, or cohort membership is
    mutated by this function.
    """
    context = build_differential_context(
        db,
        cohort_id,
        weights_config=weights_config,
    )
    if provider is None:
        resolved_settings = settings or get_settings()
        if not resolved_settings.has_openrouter:
            raise MissingAPIKeyError(
                "OPENROUTER_API_KEY is not set; live cohort differential analysis remains pending."
            )
        provider = OpenRouterDifferentialProvider(resolved_settings)

    response = provider.analyze(context)
    parsed = validate_result_support(
        parse_differential_result(response.result),
        context,
    )
    row = CohortDifferentialAnalysis(
        cohort_id=context.cohort_id,
        created_at=utcnow(),
        prompt_version=PROMPT_VERSION,
        provider=response.provider,
        model=response.model,
        evidence_mode=context.evidence_mode,
        evidence_available=context.evidence_available,
        evidence_missing=context.evidence_missing,
        input_candidate_ids=context.input_candidate_ids,
        input_evidence_json=context.input_evidence,
        result_json=parsed.model_dump(mode="json"),
        usage_json=response.usage,
        actual_cost=response.actual_cost,
        currency=response.currency,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
