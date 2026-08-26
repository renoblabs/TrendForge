from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    JSON,
    UniqueConstraint,
    event,
    inspect,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class AnalysisStatus(str, Enum):
    PENDING = "PENDING"
    ANALYZING = "ANALYZING"
    ANALYZED = "ANALYZED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class FormatStatus(str, Enum):
    DISCOVERED = "DISCOVERED"
    ANALYZING = "ANALYZING"
    WATCH = "WATCH"
    BUILD = "BUILD"
    TESTING = "TESTING"
    WINNER = "WINNER"
    SATURATED = "SATURATED"
    DEAD = "DEAD"


class VariationStatus(str, Enum):
    IDEA = "IDEA"
    SELECTED = "SELECTED"
    IN_PRODUCTION = "IN_PRODUCTION"
    PRODUCED = "PRODUCED"
    REJECTED = "REJECTED"


class ApprovalStatus(str, Enum):
    DRAFT = "DRAFT"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class DistributionStatus(str, Enum):
    DRAFT = "DRAFT"
    SCHEDULED = "SCHEDULED"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ProductionSpecStatus(str, Enum):
    READY = "READY"
    FAILED = "FAILED"


class GenerationJobStatus(str, Enum):
    QUEUED = "QUEUED"
    PREPARING = "PREPARING"
    SPEC_READY = "SPEC_READY"
    AGENT_RUNNING = "AGENT_RUNNING"
    COMFY_RUNNING = "COMFY_RUNNING"
    REVIEWING = "REVIEWING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ProductionRecipeStatus(str, Enum):
    DRAFT = "DRAFT"
    TUNING = "TUNING"
    SUCCESSFUL = "SUCCESSFUL"
    RETIRED = "RETIRED"


class ProductionRecipeVersionStatus(str, Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    FROZEN = "FROZEN"
    SUPERSEDED = "SUPERSEDED"


class ProductionShotStatus(str, Enum):
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ProductionStage(str, Enum):
    PREPARATION = "PREPARATION"
    VISUAL_BIBLE = "VISUAL_BIBLE"
    KEYFRAMES = "KEYFRAMES"
    MOTION = "MOTION"
    AUDIO = "AUDIO"
    ASSEMBLY = "ASSEMBLY"
    FINAL_QA = "FINAL_QA"
    COMPLETE = "COMPLETE"
    LEGACY_ONE_SHOT = "LEGACY_ONE_SHOT"


class ProductionAttemptStatus(str, Enum):
    PLANNED = "PLANNED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    APPROVED = "APPROVED"
    SUPERSEDED = "SUPERSEDED"
    CANCELLED = "CANCELLED"


class ProductionAssetRole(str, Enum):
    INPUT = "INPUT"
    REFERENCE = "REFERENCE"
    FIRST_FRAME = "FIRST_FRAME"
    LAST_FRAME = "LAST_FRAME"
    KEYFRAME = "KEYFRAME"
    MOTION_OUTPUT = "MOTION_OUTPUT"
    VOICE = "VOICE"
    SFX = "SFX"
    MUSIC = "MUSIC"
    CAPTIONS = "CAPTIONS"
    ROUGH_CUT = "ROUGH_CUT"
    FINAL_OUTPUT = "FINAL_OUTPUT"
    COVER_FRAME = "COVER_FRAME"
    QA_ARTIFACT = "QA_ARTIFACT"
    LEGACY_BASELINE = "LEGACY_BASELINE"


class ProductionApprovalGate(str, Enum):
    VISUAL_BIBLE_APPROVAL = "VISUAL_BIBLE_APPROVAL"
    KEYFRAME_APPROVAL = "KEYFRAME_APPROVAL"
    MOTION_APPROVAL = "MOTION_APPROVAL"
    ROUGH_CUT_APPROVAL = "ROUGH_CUT_APPROVAL"
    FINAL_RENDER_APPROVAL = "FINAL_RENDER_APPROVAL"


class ProductionApprovalScope(str, Enum):
    RECIPE_VERSION = "RECIPE_VERSION"
    VISUAL_BIBLE = "VISUAL_BIBLE"
    SHOT_KEYFRAME = "SHOT_KEYFRAME"
    SHOT_MOTION = "SHOT_MOTION"
    ROUGH_CUT = "ROUGH_CUT"
    FINAL_RENDER = "FINAL_RENDER"


class ProductionApprovalDecision(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    REVOKE = "REVOKE"


class ProductionCapabilityStatus(str, Enum):
    CAPTURED = "CAPTURED"
    INVALID = "INVALID"


class ProductionBenchmarkStatus(str, Enum):
    READY_FOR_BENCHMARK_REVIEW = "READY_FOR_BENCHMARK_REVIEW"
    USER_SELECTED = "USER_SELECTED"
    EXCLUDED_BY_USER = "EXCLUDED_BY_USER"


class ContentCandidate(Base):
    __tablename__ = "content_candidates"
    __table_args__ = (UniqueConstraint("url", name="uq_candidate_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(32), default="unknown")
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    creator: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    title: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    views: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    likes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    comments: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    shares: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duration: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    thumbnail_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    raw_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    analysis_status: Mapped[AnalysisStatus] = mapped_column(
        SAEnum(AnalysisStatus), default=AnalysisStatus.PENDING
    )
    analysis_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    analysis_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    format_id: Mapped[Optional[int]] = mapped_column(ForeignKey("formats.id"), nullable=True)

    external_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    channel_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    channel_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    hashtags: Mapped[Optional[list[Any]]] = mapped_column(JSON, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    source_query: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source_queries: Mapped[Optional[list[Any]]] = mapped_column(JSON, nullable=True)
    is_short: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    favorite_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    channel_subscriber_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    age_hours: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    views_per_hour: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    acceleration: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    like_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    comment_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    creator_baseline: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    creator_baseline_estimated: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    creator_lift: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    discovery_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    discovery_breakdown: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    discovery_labels: Mapped[Optional[list[Any]]] = mapped_column(JSON, nullable=True)
    promoted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    analysis_history: Mapped[Optional[list[Any]]] = mapped_column(JSON, nullable=True)
    data_origin: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    acquisition_run_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    acquisition_provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    acquisition_profiles: Mapped[Optional[list[Any]]] = mapped_column(JSON, nullable=True)

    format: Mapped[Optional["Format"]] = relationship(back_populates="candidates")
    observations: Mapped[list["CandidateObservation"]] = relationship(
        back_populates="candidate", order_by="CandidateObservation.observed_at"
    )

    @property
    def engagement(self) -> Optional[float]:
        if self.views is None or self.views == 0:
            parts = [x for x in (self.likes, self.comments, self.shares) if x is not None]
            return float(sum(parts)) if parts else None
        likes = self.likes or 0
        comments = self.comments or 0
        shares = self.shares or 0
        return round((likes + comments + shares) / self.views * 100, 2)


class Format(Base):
    __tablename__ = "formats"
    __table_args__ = (UniqueConstraint("format_key", name="uq_format_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    format_key: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attention_mechanic: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    format_category: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    hook_pattern: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    why_it_works: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    variables: Mapped[Optional[list[Any]]] = mapped_column(JSON, nullable=True)
    recommended_production_method: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    production_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ip_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    novelty_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    replicability_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    variation_density: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cross_platform_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    production_complexity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    estimated_generation_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ip_risk: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    comfy_feasibility: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    saturation_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    trend_velocity_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    hook_strength: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    overall_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score_breakdown: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    status: Mapped[FormatStatus] = mapped_column(SAEnum(FormatStatus), default=FormatStatus.DISCOVERED)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    candidates: Mapped[list[ContentCandidate]] = relationship(back_populates="format")
    variations: Mapped[list["FormatVariation"]] = relationship(back_populates="format")
    assets: Mapped[list["ContentAsset"]] = relationship(back_populates="format")


class FormatVariation(Base):
    __tablename__ = "format_variations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    format_id: Mapped[int] = mapped_column(ForeignKey("formats.id"), nullable=False)
    concept: Mapped[str] = mapped_column(Text, nullable=False)
    character: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    scenario: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    hook: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    production_method: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    comfy_workflow: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    estimated_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[VariationStatus] = mapped_column(
        SAEnum(VariationStatus), default=VariationStatus.IDEA
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    format: Mapped[Format] = relationship(back_populates="variations")
    assets: Mapped[list["ContentAsset"]] = relationship(back_populates="variation")


class ContentAsset(Base):
    __tablename__ = "content_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    variation_id: Mapped[Optional[int]] = mapped_column(ForeignKey("format_variations.id"), nullable=True)
    format_id: Mapped[Optional[int]] = mapped_column(ForeignKey("formats.id"), nullable=True)
    asset_type: Mapped[str] = mapped_column(String(64), default="video")
    file_reference: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    duration: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    generation_provider: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    generation_workflow: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    generation_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    approval_status: Mapped[ApprovalStatus] = mapped_column(
        SAEnum(ApprovalStatus), default=ApprovalStatus.DRAFT
    )
    generation_job_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    mime_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    format_family: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    asset_role: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    media_metadata_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    reported_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)

    variation: Mapped[Optional[FormatVariation]] = relationship(back_populates="assets")
    format: Mapped[Optional[Format]] = relationship(back_populates="assets")
    distribution_jobs: Mapped[list["DistributionJob"]] = relationship(back_populates="content_asset")


class DistributionJob(Base):
    __tablename__ = "distribution_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_asset_id: Mapped[int] = mapped_column(ForeignKey("content_assets.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    account_channel: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    postiz_post_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[DistributionStatus] = mapped_column(
        SAEnum(DistributionStatus), default=DistributionStatus.DRAFT
    )
    published_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    job_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    content_asset: Mapped[ContentAsset] = relationship(back_populates="distribution_jobs")
    performance_records: Mapped[list["PerformanceRecord"]] = relationship(
        back_populates="distribution_job"
    )


class PerformanceRecord(Base):
    __tablename__ = "performance_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    distribution_job_id: Mapped[int] = mapped_column(
        ForeignKey("distribution_jobs.id"), nullable=False
    )
    views: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    likes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    comments: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    shares: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    saves: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    engagement_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    view_velocity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    followers_gained: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    distribution_job: Mapped[DistributionJob] = relationship(back_populates="performance_records")


class CandidateObservation(Base):
    __tablename__ = "candidate_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("content_candidates.id"), nullable=False, index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    view_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    like_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    comment_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    favorite_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    channel_subscriber_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(64), default="youtube")
    raw_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    candidate: Mapped[ContentCandidate] = relationship(back_populates="observations")


class DiscoveryRun(Base):
    __tablename__ = "discovery_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), default="discover")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    queries_used: Mapped[Optional[list[Any]]] = mapped_column(JSON, nullable=True)
    candidates_found: Mapped[int] = mapped_column(Integer, default=0)
    new_candidates: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    promoted_candidates: Mapped[int] = mapped_column(Integer, default=0)
    observations_written: Mapped[int] = mapped_column(Integer, default=0)
    api_errors: Mapped[Optional[list[Any]]] = mapped_column(JSON, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cohort_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("research_cohorts.id"), nullable=True, index=True
    )
    milestone: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)
    apify_run_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    apify_dataset_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    actual_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    run_metadata_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    cohort: Mapped[Optional["ResearchCohort"]] = relationship(
        back_populates="observation_runs"
    )


class AcquisitionRun(Base):
    """One controlled acquisition pass (Apify Actor, later other providers)."""

    __tablename__ = "acquisition_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="apify")
    actor_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    profile: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    items_found: Mapped[int] = mapped_column(Integer, default=0)
    items_new: Mapped[int] = mapped_column(Integer, default=0)
    items_duplicate: Mapped[int] = mapped_column(Integer, default=0)
    items_rejected: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[Optional[list[Any]]] = mapped_column(JSON, nullable=True)
    run_metadata_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    apify_run_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    apify_dataset_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    estimated_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    actual_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)


class ResearchCohort(Base):
    """Frozen membership and timing for a longitudinal research experiment."""

    __tablename__ = "research_cohorts"
    __table_args__ = (UniqueConstraint("cohort_key", name="uq_research_cohort_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cohort_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    profile: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    acquisition_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("acquisition_runs.id"), nullable=True, index=True
    )
    source_run_ids_json: Mapped[Optional[list[Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    t0_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    target_t1_window_start: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    target_t1_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    target_t1_window_end: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    target_t2_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE", index=True)
    config_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    config_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    outcome_rule_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    acquisition_run: Mapped[Optional[AcquisitionRun]] = relationship()
    members: Mapped[list["ResearchCohortMember"]] = relationship(
        back_populates="cohort",
        cascade="all, delete-orphan",
        order_by="ResearchCohortMember.id",
    )
    observation_runs: Mapped[list[DiscoveryRun]] = relationship(
        back_populates="cohort", order_by="DiscoveryRun.started_at"
    )
    differential_analyses: Mapped[list["CohortDifferentialAnalysis"]] = relationship(
        back_populates="cohort",
        cascade="all, delete-orphan",
        order_by="CohortDifferentialAnalysis.created_at",
    )


class ResearchCohortMember(Base):
    """A stable candidate reference plus explicit observation milestone links."""

    __tablename__ = "research_cohort_members"
    __table_args__ = (
        UniqueConstraint("cohort_id", "candidate_id", name="uq_research_cohort_member"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cohort_id: Mapped[int] = mapped_column(
        ForeignKey("research_cohorts.id"), nullable=False, index=True
    )
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("content_candidates.id"), nullable=False, index=True
    )
    t0_observation_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("candidate_observations.id"), nullable=True
    )
    t1_observation_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("candidate_observations.id"), nullable=True
    )
    t2_observation_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("candidate_observations.id"), nullable=True
    )
    included_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    outcome_label: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    outcome_rule_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    right_censored: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    censor_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    censored_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    cohort: Mapped[ResearchCohort] = relationship(back_populates="members")
    candidate: Mapped[ContentCandidate] = relationship()
    t0_observation: Mapped[Optional[CandidateObservation]] = relationship(
        foreign_keys=[t0_observation_id]
    )
    t1_observation: Mapped[Optional[CandidateObservation]] = relationship(
        foreign_keys=[t1_observation_id]
    )
    t2_observation: Mapped[Optional[CandidateObservation]] = relationship(
        foreign_keys=[t2_observation_id]
    )


class CohortDifferentialAnalysis(Base):
    """Append-only outcome-aware analysis for a mature research cohort."""

    __tablename__ = "cohort_differential_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cohort_id: Mapped[int] = mapped_column(
        ForeignKey("research_cohorts.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    evidence_mode: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_available: Mapped[Optional[list[Any]]] = mapped_column(JSON, nullable=True)
    evidence_missing: Mapped[Optional[list[Any]]] = mapped_column(JSON, nullable=True)
    input_candidate_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    input_evidence_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    usage_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    actual_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)

    cohort: Mapped[ResearchCohort] = relationship(back_populates="differential_analyses")


class FormatBrainstormSet(Base):
    """Persisted ideation output. Not evidence, not a candidate, not a discovery signal."""

    __tablename__ = "format_brainstorm_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    format_family: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    analyzer: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    requested_count: Mapped[int] = mapped_column(Integer, default=10)
    emphasis: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ideas_json: Mapped[Optional[list[Any]]] = mapped_column(JSON, nullable=True)


class ProductionSpec(Base):
    """Production brief for a selected idea. Not evidence and not a generated video."""

    __tablename__ = "production_specs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    format_family: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_brainstorm_set_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("format_brainstorm_sets.id"), nullable=True, index=True
    )
    source_idea_identifier: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    analyzer: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    status: Mapped[ProductionSpecStatus] = mapped_column(
        SAEnum(ProductionSpecStatus), default=ProductionSpecStatus.READY
    )
    spec_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)


class GenerationJob(Base):
    __tablename__ = "generation_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    status: Mapped[GenerationJobStatus] = mapped_column(
        SAEnum(GenerationJobStatus), default=GenerationJobStatus.QUEUED, index=True
    )
    format_family: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_brainstorm_set_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("format_brainstorm_sets.id"), nullable=True
    )
    source_idea_identifier: Mapped[str] = mapped_column(String(64), nullable=False)
    production_spec_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("production_specs.id"), nullable=True
    )
    requested_duration_seconds: Mapped[int] = mapped_column(Integer, default=20)
    variant_count: Mapped[int] = mapped_column(Integer, default=1)
    style: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    voice: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    audio_preferences: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    agent: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    agent_session_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    comfy_workflow_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    comfy_workflow_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    output_asset_ids: Mapped[Optional[list[Any]]] = mapped_column(JSON, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    job_metadata_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    recipe_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("production_recipes.id"), nullable=True, index=True
    )
    recipe_version_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("production_recipe_versions.id"), nullable=True, index=True
    )
    current_stage: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    current_gate: Mapped[Optional[str]] = mapped_column(String(48), nullable=True)
    estimated_budget: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    actual_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    total_elapsed_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_stage_transition_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    production_metadata_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)


class ProductionCapabilitySnapshot(Base):
    __tablename__ = "production_capability_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    mcp_server: Mapped[str] = mapped_column(String(128), nullable=False)
    agent: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    agent_version: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    raw_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    normalized_capabilities_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    content_checksum: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    status: Mapped[ProductionCapabilityStatus] = mapped_column(
        SAEnum(ProductionCapabilityStatus), default=ProductionCapabilityStatus.CAPTURED
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class ProductionBenchmarkDecision(Base):
    """Append-only human benchmark selection or exclusion decision."""

    __tablename__ = "production_benchmark_decisions"
    __table_args__ = (
        UniqueConstraint("selection_key", name="uq_production_benchmark_selection_key"),
        UniqueConstraint("production_spec_id", name="uq_production_benchmark_spec"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    selection_key: Mapped[str] = mapped_column(String(64), nullable=False)
    production_spec_id: Mapped[int] = mapped_column(
        ForeignKey("production_specs.id"), nullable=False, index=True
    )
    status: Mapped[ProductionBenchmarkStatus] = mapped_column(
        SAEnum(ProductionBenchmarkStatus), nullable=False, index=True
    )
    selected_by: Mapped[str] = mapped_column(String(128), nullable=False)
    selection_reason: Mapped[str] = mapped_column(Text, nullable=False)
    review_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    recipe_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("production_recipes.id"), nullable=True, unique=True
    )


class ProductionRecipe(Base):
    __tablename__ = "production_recipes"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_production_recipe_slug"),
        UniqueConstraint("source_production_spec_id", name="uq_production_recipe_source_spec"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    format_family: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[ProductionRecipeStatus] = mapped_column(
        SAEnum(ProductionRecipeStatus), default=ProductionRecipeStatus.DRAFT, index=True
    )
    current_version_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("production_recipe_versions.id"), nullable=True
    )
    source_production_spec_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("production_specs.id"), nullable=True, index=True
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)


class ProductionRecipeVersion(Base):
    __tablename__ = "production_recipe_versions"
    __table_args__ = (
        UniqueConstraint("recipe_id", "version_number", name="uq_production_recipe_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recipe_id: Mapped[int] = mapped_column(
        ForeignKey("production_recipes.id"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    frozen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[ProductionRecipeVersionStatus] = mapped_column(
        SAEnum(ProductionRecipeVersionStatus), default=ProductionRecipeVersionStatus.DRAFT, index=True
    )
    source_production_spec_id: Mapped[int] = mapped_column(
        ForeignKey("production_specs.id"), nullable=False, index=True
    )
    production_spec_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    visual_bible_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    duration_budget_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    audio_plan_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    caption_plan_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    assembly_settings_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    capability_snapshot_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("production_capability_snapshots.id"), nullable=True, index=True
    )
    recipe_metadata_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    content_checksum: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


class ProductionRecipeShot(Base):
    __tablename__ = "production_recipe_shots"
    __table_args__ = (
        UniqueConstraint("recipe_version_id", "shot_number", name="uq_recipe_shot_number"),
        UniqueConstraint("recipe_version_id", "stable_key", name="uq_recipe_shot_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recipe_version_id: Mapped[int] = mapped_column(
        ForeignKey("production_recipe_versions.id"), nullable=False, index=True
    )
    shot_number: Mapped[int] = mapped_column(Integer, nullable=False)
    stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    purpose: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    camera_json: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    framing_json: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    subject_json: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    action_json: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    environment_json: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    audio_json: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    generation_notes_json: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    continuity_inputs_json: Mapped[Optional[list[Any]]] = mapped_column(JSON, nullable=True)
    continuity_outputs_json: Mapped[Optional[list[Any]]] = mapped_column(JSON, nullable=True)
    required_asset_roles_json: Mapped[Optional[list[Any]]] = mapped_column(JSON, nullable=True)
    negative_constraints_json: Mapped[Optional[list[Any]]] = mapped_column(JSON, nullable=True)
    qa_requirements_json: Mapped[Optional[list[Any]]] = mapped_column(JSON, nullable=True)
    status: Mapped[ProductionShotStatus] = mapped_column(
        SAEnum(ProductionShotStatus), default=ProductionShotStatus.PLANNED, index=True
    )


class ProductionAttempt(Base):
    __tablename__ = "production_attempts"
    __table_args__ = (
        UniqueConstraint(
            "recipe_version_id", "shot_id", "stage", "attempt_number",
            name="uq_production_attempt_number",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("production_recipes.id"), nullable=False, index=True)
    recipe_version_id: Mapped[int] = mapped_column(
        ForeignKey("production_recipe_versions.id"), nullable=False, index=True
    )
    generation_job_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("generation_jobs.id"), nullable=True, index=True
    )
    shot_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("production_recipe_shots.id"), nullable=True, index=True
    )
    stage: Mapped[ProductionStage] = mapped_column(SAEnum(ProductionStage), nullable=False, index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ProductionAttemptStatus] = mapped_column(
        SAEnum(ProductionAttemptStatus), default=ProductionAttemptStatus.PLANNED, index=True
    )
    provider: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    agent: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    template_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    workflow_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    workflow_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    cloud_job_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    parameters_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    prompt_components_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    negative_constraints_json: Mapped[Optional[list[Any]]] = mapped_column(JSON, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    elapsed_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    reported_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    qa_result_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    continuity_result_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)


class ProductionAttemptAsset(Base):
    __tablename__ = "production_attempt_assets"
    __table_args__ = (
        UniqueConstraint("attempt_id", "asset_id", "role", name="uq_attempt_asset_role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    attempt_id: Mapped[int] = mapped_column(
        ForeignKey("production_attempts.id"), nullable=False, index=True
    )
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("content_assets.id"), nullable=False, index=True
    )
    role: Mapped[ProductionAssetRole] = mapped_column(
        SAEnum(ProductionAssetRole), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)


class ProductionApprovalEvent(Base):
    __tablename__ = "production_approval_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("production_recipes.id"), nullable=False, index=True)
    recipe_version_id: Mapped[int] = mapped_column(
        ForeignKey("production_recipe_versions.id"), nullable=False, index=True
    )
    generation_job_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("generation_jobs.id"), nullable=True, index=True
    )
    scope_type: Mapped[ProductionApprovalScope] = mapped_column(
        SAEnum(ProductionApprovalScope), nullable=False, index=True
    )
    scope_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    gate: Mapped[ProductionApprovalGate] = mapped_column(
        SAEnum(ProductionApprovalGate), nullable=False, index=True
    )
    decision: Mapped[ProductionApprovalDecision] = mapped_column(
        SAEnum(ProductionApprovalDecision), nullable=False, index=True
    )
    reviewer: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    approved_asset_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("content_assets.id"), nullable=True
    )
    approved_attempt_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("production_attempts.id"), nullable=True
    )
    metadata_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)


def _reject_append_only_changes(mapper, connection, target) -> None:  # noqa: ARG001
    raise ValueError(f"{target.__class__.__name__} records are immutable")


event.listen(ProductionCapabilitySnapshot, "before_update", _reject_append_only_changes)
event.listen(ProductionCapabilitySnapshot, "before_delete", _reject_append_only_changes)
event.listen(ProductionBenchmarkDecision, "before_update", _reject_append_only_changes)
event.listen(ProductionBenchmarkDecision, "before_delete", _reject_append_only_changes)
event.listen(ProductionApprovalEvent, "before_update", _reject_append_only_changes)
event.listen(ProductionApprovalEvent, "before_delete", _reject_append_only_changes)


@event.listens_for(ProductionRecipeVersion, "before_update")
def _reject_frozen_recipe_version_changes(mapper, connection, target) -> None:  # noqa: ARG001
    state = inspect(target)
    if state.attrs.production_spec_snapshot_json.history.has_changes():
        raise ValueError("production-spec snapshots are immutable")
    history = state.attrs.status.history
    previous = history.deleted[0] if history.deleted else target.status
    if previous == ProductionRecipeVersionStatus.FROZEN:
        raise ValueError("frozen recipe versions are immutable")


@event.listens_for(ProductionRecipeVersion, "before_delete")
def _reject_frozen_recipe_version_delete(mapper, connection, target) -> None:  # noqa: ARG001
    if target.status == ProductionRecipeVersionStatus.FROZEN:
        raise ValueError("frozen recipe versions are immutable")


def _reject_frozen_shot_changes(mapper, connection, target) -> None:  # noqa: ARG001
    status = connection.execute(
        select(ProductionRecipeVersion.status).where(
            ProductionRecipeVersion.id == target.recipe_version_id
        )
    ).scalar_one_or_none()
    if status == ProductionRecipeVersionStatus.FROZEN:
        raise ValueError("shots belonging to a frozen recipe version are immutable")


event.listen(ProductionRecipeShot, "before_update", _reject_frozen_shot_changes)
event.listen(ProductionRecipeShot, "before_delete", _reject_frozen_shot_changes)
