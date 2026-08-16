from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from trendforge.config import load_discovery_config
from trendforge.models import DiscoveryRun, utcnow

DISCOVER_KINDS = ("discover", "discover_broad")
OBSERVE_KINDS = ("observe",)

DEFAULT_SCHEDULE = {
    "enabled": True,
    "discover_every_minutes": 360,
    "observe_every_minutes": 90,
    "mode": "topics",
    "promote": True,
    "live_analysis": False,
    "limit": None,
    "check_every_seconds": 60,
}


def schedule_config(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or load_discovery_config()
    raw = cfg.get("schedule") if isinstance(cfg.get("schedule"), dict) else {}
    merged = dict(DEFAULT_SCHEDULE)
    merged.update({k: v for k, v in raw.items() if v is not None})
    merged["enabled"] = bool(merged.get("enabled", True))
    merged["discover_every_minutes"] = max(1, int(merged.get("discover_every_minutes") or 360))
    merged["observe_every_minutes"] = max(1, int(merged.get("observe_every_minutes") or 90))
    merged["mode"] = str(merged.get("mode") or "topics")
    merged["promote"] = bool(merged.get("promote", True))
    merged["live_analysis"] = bool(merged.get("live_analysis", False))
    limit = merged.get("limit")
    merged["limit"] = None if limit in (None, "", 0) else int(limit)
    merged["check_every_seconds"] = max(5, int(merged.get("check_every_seconds") or 60))
    return merged


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def last_run(db: Session, kinds: tuple[str, ...]) -> DiscoveryRun | None:
    return (
        db.query(DiscoveryRun)
        .filter(DiscoveryRun.kind.in_(kinds))
        .order_by(DiscoveryRun.started_at.desc())
        .first()
    )


def _job_status(
    *,
    name: str,
    last: DiscoveryRun | None,
    every_minutes: int,
    now: datetime,
    enabled: bool,
) -> dict[str, Any]:
    started = _as_utc(last.started_at) if last else None
    due_at = (started + timedelta(minutes=every_minutes)) if started else now
    overdue = enabled and (started is None or now >= due_at)
    return {
        "name": name,
        "last_started_at": started,
        "every_minutes": every_minutes,
        "due_at": due_at,
        "due": overdue,
        "last_kind": last.kind if last else None,
        "last_id": last.id if last else None,
    }


def gathering_status(
    db: Session,
    *,
    now: datetime | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sched = schedule_config(cfg)
    now = _as_utc(now or utcnow()) or utcnow()
    discover = _job_status(
        name="discover",
        last=last_run(db, DISCOVER_KINDS),
        every_minutes=sched["discover_every_minutes"],
        now=now,
        enabled=sched["enabled"],
    )
    observe = _job_status(
        name="observe",
        last=last_run(db, OBSERVE_KINDS),
        every_minutes=sched["observe_every_minutes"],
        now=now,
        enabled=sched["enabled"],
    )
    return {
        "schedule": sched,
        "now": now,
        "discover": discover,
        "observe": observe,
        "any_due": bool(sched["enabled"] and (discover["due"] or observe["due"])),
    }


def jobs_due(status: dict[str, Any], *, force: bool = False) -> list[str]:
    if force:
        return ["discover", "observe"]
    if not status.get("schedule", {}).get("enabled"):
        return []
    due: list[str] = []
    if status["discover"]["due"]:
        due.append("discover")
    if status["observe"]["due"]:
        due.append("observe")
    return due
