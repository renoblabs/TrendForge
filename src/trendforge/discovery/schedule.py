from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from trendforge.config import load_discovery_config
from trendforge.models import DiscoveryRun, utcnow

DISCOVER_KINDS = ("discover", "discover_broad")
OBSERVE_KINDS = ("observe",)
TIKTOK_DISCOVER_KINDS = ("discover_tiktok",)
TIKTOK_OBSERVE_KINDS = ("observe_tiktok",)
INSTAGRAM_DISCOVER_KINDS = ("discover_instagram",)
INSTAGRAM_OBSERVE_KINDS = ("observe_instagram",)

DEFAULT_SCHEDULE = {
    "enabled": True,
    "discover_every_minutes": 1440,
    "observe_every_minutes": 1440,
    "mode": "topics",
    "promote": True,
    "live_analysis": False,
    "limit": None,
    "check_every_seconds": 60,
}

APIFY_JOB_NAMES = (
    "discover_tiktok",
    "observe_tiktok",
    "discover_instagram",
    "observe_instagram",
)


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
    apify = cfg.get("apify") if isinstance(cfg.get("apify"), dict) else {}
    tiktok = apify.get("tiktok") if isinstance(apify.get("tiktok"), dict) else {}
    instagram = apify.get("instagram") if isinstance(apify.get("instagram"), dict) else {}
    apify_on = bool(apify.get("enabled", False))
    merged["tiktok_enabled"] = apify_on
    merged["tiktok_discover_every_minutes"] = max(
        1, int(tiktok.get("discover_every_minutes") or 1440)
    )
    merged["tiktok_observe_every_minutes"] = max(
        1, int(tiktok.get("observe_every_minutes") or 720)
    )
    merged["instagram_enabled"] = apify_on and bool(instagram.get("enabled", False))
    merged["instagram_discover_every_minutes"] = max(
        1, int(instagram.get("discover_every_minutes") or 1440)
    )
    merged["instagram_observe_every_minutes"] = max(
        1, int(instagram.get("observe_every_minutes") or 720)
    )
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
    tiktok_on = bool(sched.get("tiktok_enabled"))
    tiktok_discover = _job_status(
        name="discover_tiktok",
        last=last_run(db, TIKTOK_DISCOVER_KINDS),
        every_minutes=int(sched.get("tiktok_discover_every_minutes") or 1440),
        now=now,
        enabled=sched["enabled"] and tiktok_on,
    )
    tiktok_observe = _job_status(
        name="observe_tiktok",
        last=last_run(db, TIKTOK_OBSERVE_KINDS),
        every_minutes=int(sched.get("tiktok_observe_every_minutes") or 720),
        now=now,
        enabled=sched["enabled"] and tiktok_on,
    )
    ig_on = bool(sched.get("instagram_enabled"))
    ig_discover = _job_status(
        name="discover_instagram",
        last=last_run(db, INSTAGRAM_DISCOVER_KINDS),
        every_minutes=int(sched.get("instagram_discover_every_minutes") or 1440),
        now=now,
        enabled=sched["enabled"] and ig_on,
    )
    ig_observe = _job_status(
        name="observe_instagram",
        last=last_run(db, INSTAGRAM_OBSERVE_KINDS),
        every_minutes=int(sched.get("instagram_observe_every_minutes") or 720),
        now=now,
        enabled=sched["enabled"] and ig_on,
    )
    return {
        "schedule": sched,
        "now": now,
        "discover": discover,
        "observe": observe,
        "discover_tiktok": tiktok_discover,
        "observe_tiktok": tiktok_observe,
        "discover_instagram": ig_discover,
        "observe_instagram": ig_observe,
        "any_due": bool(
            sched["enabled"]
            and (
                discover["due"]
                or observe["due"]
                or tiktok_discover["due"]
                or tiktok_observe["due"]
                or ig_discover["due"]
                or ig_observe["due"]
            )
        ),
    }


def jobs_due(status: dict[str, Any], *, force: bool = False) -> list[str]:
    names = ["discover", "observe", *APIFY_JOB_NAMES]
    if force:
        due = ["discover", "observe"]
        sched = status.get("schedule") or {}
        if sched.get("tiktok_enabled"):
            due.extend(["discover_tiktok", "observe_tiktok"])
        if sched.get("instagram_enabled"):
            due.extend(["discover_instagram", "observe_instagram"])
        return due
    if not status.get("schedule", {}).get("enabled"):
        return []
    due = []
    for name in names:
        job = status.get(name) or {}
        if job.get("due"):
            due.append(name)
    return due
