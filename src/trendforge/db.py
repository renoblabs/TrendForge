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
    if db_path is None:
        global _SessionLocal
        _SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


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
