from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from trendforge.config import get_settings, load_discovery_config
from trendforge.discovery.instagram import run_instagram_discovery, run_instagram_observation
from trendforge.discovery.pipeline import run_discovery, run_observation_refresh
from trendforge.discovery.provider import DiscoveryError
from trendforge.discovery.schedule import gathering_status, jobs_due
from trendforge.discovery.tiktok import run_tiktok_discovery, run_tiktok_observation
from trendforge.discovery.youtube import YouTubeDiscoveryProvider
from trendforge.models import DiscoveryRun

APIFY_JOBS = {
    "discover_tiktok",
    "observe_tiktok",
    "discover_instagram",
    "observe_instagram",
}


def run_gathering_jobs(
    db: Session,
    *,
    jobs: list[str] | None = None,
    force: bool = False,
    now: datetime | None = None,
    cfg: dict[str, Any] | None = None,
    provider: YouTubeDiscoveryProvider | None = None,
    apify_client: Any | None = None,
) -> list[tuple[str, DiscoveryRun]]:
    cfg = cfg or load_discovery_config()
    status = gathering_status(db, now=now, cfg=cfg)
    sched = status["schedule"]
    selected = list(jobs) if jobs is not None else jobs_due(status, force=force)
    if not selected:
        return []
    settings = get_settings()
    youtube_jobs = [j for j in selected if j in {"discover", "observe"}]
    apify_jobs = [j for j in selected if j in APIFY_JOBS]
    if youtube_jobs and not settings.has_youtube and provider is None:
        if not apify_jobs:
            raise DiscoveryError("YOUTUBE_API_KEY is not set.")
        youtube_jobs = []
    if apify_jobs and not settings.has_apify and apify_client is None:
        if not youtube_jobs:
            raise DiscoveryError("APIFY_API_TOKEN is not set.")
        apify_jobs = []
    ran: list[tuple[str, DiscoveryRun]] = []
    yt = provider
    if youtube_jobs:
        yt = yt or YouTubeDiscoveryProvider()
    if "discover" in youtube_jobs:
        run = run_discovery(
            db,
            provider=yt,
            promote=bool(sched["promote"]),
            force_stub_analysis=not bool(sched["live_analysis"]),
            cfg=cfg,
            now=now,
            limit=sched["limit"],
            mode=str(sched["mode"] or "topics"),
        )
        ran.append(("discover", run))
    if "observe" in youtube_jobs:
        run = run_observation_refresh(db, provider=yt, cfg=cfg)
        ran.append(("observe", run))
    if "discover_tiktok" in apify_jobs:
        ran.append(("discover_tiktok", run_tiktok_discovery(db, client=apify_client, cfg=cfg)))
    if "observe_tiktok" in apify_jobs:
        ran.append(("observe_tiktok", run_tiktok_observation(db, client=apify_client, cfg=cfg)))
    if "discover_instagram" in apify_jobs:
        ran.append(
            ("discover_instagram", run_instagram_discovery(db, client=apify_client, cfg=cfg))
        )
    if "observe_instagram" in apify_jobs:
        ran.append(
            ("observe_instagram", run_instagram_observation(db, client=apify_client, cfg=cfg))
        )
    return ran
