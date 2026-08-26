from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from trendforge.config import get_settings
from trendforge.models import Base

_engine = None
_SessionLocal = None


def get_engine(db_path: Path | None = None):
    global _engine, _SessionLocal
    settings = get_settings()
    path = db_path or settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{path.as_posix()}"
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ARG001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    if db_path is None:
        _engine = engine
        _SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return engine


def init_db(db_path: Path | None = None) -> None:
    engine = get_engine(db_path)
    Base.metadata.create_all(bind=engine)
    _ensure_candidate_columns(engine)
    _ensure_asset_columns(engine)
    _ensure_generation_job_columns(engine)
    _ensure_acquisition_run_columns(engine)
    _ensure_discovery_run_columns(engine)
    _ensure_cohort_differential_columns(engine)
    if db_path is None:
        global _SessionLocal
        _SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _ensure_candidate_columns(engine) -> None:
    """Add discovery columns to existing SQLite DBs created in Phase 1."""
    new_columns = {
        "external_id": "VARCHAR(128)",
        "channel_id": "VARCHAR(128)",
        "channel_name": "VARCHAR(255)",
        "hashtags": "JSON",
        "category": "VARCHAR(64)",
        "source_query": "VARCHAR(255)",
        "source_queries": "JSON",
        "is_short": "BOOLEAN",
        "favorite_count": "INTEGER",
        "channel_subscriber_count": "INTEGER",
        "age_hours": "FLOAT",
        "views_per_hour": "FLOAT",
        "acceleration": "FLOAT",
        "like_rate": "FLOAT",
        "comment_rate": "FLOAT",
        "creator_baseline": "FLOAT",
        "creator_baseline_estimated": "BOOLEAN",
        "creator_lift": "FLOAT",
        "discovery_score": "FLOAT",
        "discovery_breakdown": "JSON",
        "discovery_labels": "JSON",
        "promoted_at": "DATETIME",
        "analysis_history": "JSON",
        "data_origin": "VARCHAR(16)",
        "acquisition_run_id": "INTEGER",
        "acquisition_provider": "VARCHAR(32)",
        "acquisition_profiles": "JSON",
    }
    with engine.begin() as conn:
        rows = conn.exec_driver_sql("PRAGMA table_info(content_candidates)").fetchall()
        if not rows:
            return
        existing = {row[1] for row in rows}
        for name, ddl in new_columns.items():
            if name not in existing:
                conn.exec_driver_sql(
                    f"ALTER TABLE content_candidates ADD COLUMN {name} {ddl}"
                )


def _ensure_acquisition_run_columns(engine) -> None:
    new_columns = {"profile": "VARCHAR(64)"}
    with engine.begin() as conn:
        rows = conn.exec_driver_sql("PRAGMA table_info(acquisition_runs)").fetchall()
        if not rows:
            return
        existing = {row[1] for row in rows}
        for name, ddl in new_columns.items():
            if name not in existing:
                conn.exec_driver_sql(f"ALTER TABLE acquisition_runs ADD COLUMN {name} {ddl}")


def _ensure_discovery_run_columns(engine) -> None:
    """Add cohort/provider metadata to pre-cohort SQLite databases."""
    new_columns = {
        "cohort_id": "INTEGER",
        "milestone": "VARCHAR(16)",
        "apify_run_id": "VARCHAR(128)",
        "apify_dataset_id": "VARCHAR(128)",
        "actual_cost": "FLOAT",
        "currency": "VARCHAR(8)",
        "run_metadata_json": "JSON",
    }
    with engine.begin() as conn:
        rows = conn.exec_driver_sql("PRAGMA table_info(discovery_runs)").fetchall()
        if not rows:
            return
        existing = {row[1] for row in rows}
        for name, ddl in new_columns.items():
            if name not in existing:
                conn.exec_driver_sql(f"ALTER TABLE discovery_runs ADD COLUMN {name} {ddl}")
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_discovery_runs_cohort_id "
            "ON discovery_runs (cohort_id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_discovery_runs_milestone "
            "ON discovery_runs (milestone)"
        )


def _ensure_cohort_differential_columns(engine) -> None:
    """Complete databases created while the v1 research schema was evolving."""
    new_columns = {
        "evidence_available": "JSON",
        "evidence_missing": "JSON",
    }
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(
            "PRAGMA table_info(cohort_differential_analyses)"
        ).fetchall()
        if not rows:
            return
        existing = {row[1] for row in rows}
        for name, ddl in new_columns.items():
            if name not in existing:
                conn.exec_driver_sql(
                    f"ALTER TABLE cohort_differential_analyses ADD COLUMN {name} {ddl}"
                )


def _ensure_asset_columns(engine) -> None:
    new_columns = {
        "generation_job_id": "INTEGER",
        "mime_type": "VARCHAR(128)",
        "source": "VARCHAR(32)",
        "format_family": "VARCHAR(128)",
        "width": "INTEGER",
        "height": "INTEGER",
        "asset_role": "VARCHAR(32)",
        "sha256": "VARCHAR(64)",
        "media_metadata_json": "JSON",
        "reported_cost": "FLOAT",
        "currency": "VARCHAR(8)",
    }
    with engine.begin() as conn:
        rows = conn.exec_driver_sql("PRAGMA table_info(content_assets)").fetchall()
        if not rows:
            return
        existing = {row[1] for row in rows}
        for name, ddl in new_columns.items():
            if name not in existing:
                conn.exec_driver_sql(f"ALTER TABLE content_assets ADD COLUMN {name} {ddl}")
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_content_assets_asset_role ON content_assets (asset_role)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_content_assets_sha256 ON content_assets (sha256)"
        )


def _ensure_generation_job_columns(engine) -> None:
    """Add staged-production metadata without rewriting legacy generation jobs."""
    new_columns = {
        "recipe_id": "INTEGER",
        "recipe_version_id": "INTEGER",
        "current_stage": "VARCHAR(32)",
        "current_gate": "VARCHAR(48)",
        "estimated_budget": "FLOAT",
        "actual_cost": "FLOAT",
        "currency": "VARCHAR(8)",
        "total_elapsed_seconds": "FLOAT",
        "last_stage_transition_at": "DATETIME",
        "production_metadata_json": "JSON",
    }
    with engine.begin() as conn:
        rows = conn.exec_driver_sql("PRAGMA table_info(generation_jobs)").fetchall()
        if not rows:
            return
        existing = {row[1] for row in rows}
        for name, ddl in new_columns.items():
            if name not in existing:
                conn.exec_driver_sql(f"ALTER TABLE generation_jobs ADD COLUMN {name} {ddl}")
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_generation_jobs_recipe_id ON generation_jobs (recipe_id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_generation_jobs_recipe_version_id "
            "ON generation_jobs (recipe_version_id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_generation_jobs_current_stage "
            "ON generation_jobs (current_stage)"
        )


def get_session_factory(db_path: Path | None = None):
    if db_path is not None:
        engine = get_engine(db_path)
        return sessionmaker(bind=engine, autoflush=False, autocommit=False)
    if _SessionLocal is None:
        init_db()
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
