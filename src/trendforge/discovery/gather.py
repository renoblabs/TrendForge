from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from trendforge.config import get_settings, load_discovery_config
from trendforge.discovery.pipeline import run_discovery, run_observation_refresh
from trendforge.discovery.provider import DiscoveryError
from trendforge.discovery.schedule import gathering_status, jobs_due
from trendforge.discovery.youtube import YouTubeDiscoveryProvider
from trendforge.models import DiscoveryRun


def run_gathering_jobs(
    db: Session,
    *,
    jobs: list[str] | None = None,
    force: bool = False,
    now: datetime | None = None,
    cfg: dict[str, Any] | None = None,
    provider: YouTubeDiscoveryProvider | None = None,
) -> list[tuple[str, DiscoveryRun]]:
    cfg = cfg or load_discovery_config()
    status = gathering_status(db, now=now, cfg=cfg)
    sched = status["schedule"]
    selected = list(jobs) if jobs is not None else jobs_due(status, force=force)
    if not selected:
        return []
    settings = get_settings()
    if not settings.has_youtube and provider is None:
        raise DiscoveryError("YOUTUBE_API_KEY is not set.")
    provider = provider or YouTubeDiscoveryProvider()
    ran: list[tuple[str, DiscoveryRun]] = []
    if "discover" in selected:
        run = run_discovery(
            db,
            provider=provider,
            promote=bool(sched["promote"]),
            force_stub_analysis=not bool(sched["live_analysis"]),
            cfg=cfg,
            now=now,
            limit=sched["limit"],
            mode=str(sched["mode"] or "topics"),
        )
        ran.append(("discover", run))
    if "observe" in selected:
        run = run_observation_refresh(db, provider=provider, cfg=cfg)
        ran.append(("observe", run))
    return ran
