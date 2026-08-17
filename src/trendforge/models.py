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
