from __future__ import annotations

from typing import Any

from trendforge.acquisition.apify import ApifyClient
from trendforge.acquisition.config import (
    build_actor_input,
    clamp_limit,
    resolve_actor_id,
    source_config,
)
from trendforge.acquisition.normalize import (
    normalize_instagram_creator_reel,
    normalize_instagram_item,
)
from trendforge.acquisition.provider import ActorRunResult, NormalizedItem


class ApifyInstagramProvider:
    source = "instagram"
    provider_name = "apify"

    def __init__(self, client: ApifyClient | None = None):
        self.client = client or ApifyClient()

    def discover(self, config: dict[str, Any]) -> ActorRunResult:
        source_cfg = source_config(config, self.source)
        limit = clamp_limit(source_cfg, source_cfg.get("_limit"))
        actor_id = resolve_actor_id(config, source_cfg)
        run_input = build_actor_input(source_cfg, limit)
        return self.client.run_actor_result(
            actor_id,
            run_input,
            timeout_secs=int(source_cfg.get("timeout_secs") or 180),
            max_total_charge_usd=float(source_cfg.get("max_charge_usd") or 0.5),
            max_items=limit,
        )

    def normalize(self, raw_item: dict[str, Any], **kwargs: Any) -> NormalizedItem | None:
        profile = str(kwargs.pop("profile", "") or "")
        if profile in {"instagram_creator_reels", "creator_reels"}:
            return normalize_instagram_creator_reel(raw_item, **kwargs)
        return normalize_instagram_item(raw_item, **kwargs)
