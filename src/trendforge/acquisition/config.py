from __future__ import annotations

from copy import deepcopy
from typing import Any

from trendforge.acquisition.errors import AcquisitionError
from trendforge.config import load_data_sources_config

PROFILE_ALIASES = {
    "trending": "tiktok_trending",
    "tiktok_trending": "tiktok_trending",
    "fresh": "tiktok_fresh_search",
    "fresh_search": "tiktok_fresh_search",
    "tiktok_fresh_search": "tiktok_fresh_search",
    "hashtag": "tiktok_hashtag",
    "tiktok_hashtag": "tiktok_hashtag",
    "emerging": "tiktok_emerging",
    "emerging_breakout": "tiktok_emerging",
    "tiktok_emerging": "tiktok_emerging",
    "creator_reels": "instagram_creator_reels",
    "instagram_creator_reels": "instagram_creator_reels",
    "instagram_hashtag": "instagram_hashtag",
}

DEFAULT_SOURCE_PROFILE = {
    "tiktok": "tiktok_hashtag",
    "instagram": "instagram_hashtag",
}


def load_acquisition_config(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    return cfg or load_data_sources_config()


def canonical_profile_name(name: str | None) -> str | None:
    if name is None:
        return None
    text = str(name).strip().lower()
    if text in {"", "default", "standard"}:
        return None
    mapped = PROFILE_ALIASES.get(text, text)
    return mapped


def profile_definitions(cfg: dict[str, Any]) -> dict[str, Any]:
    raw = cfg.get("profiles") if isinstance(cfg.get("profiles"), dict) else {}
    return {str(k): v for k, v in raw.items() if isinstance(v, dict)}


def get_profile(cfg: dict[str, Any], name: str) -> dict[str, Any]:
    profiles = profile_definitions(cfg)
    row = profiles.get(name)
    if not isinstance(row, dict):
        raise AcquisitionError(
            f"Unknown acquisition profile {name!r}. "
            "Use tiktok_trending, tiktok_fresh_search, or instagram_creator_reels."
        )
    if row.get("enabled") is False:
        raise AcquisitionError(f"Acquisition profile {name!r} is disabled in config.")
    return dict(row)


def actor_id_for(cfg: dict[str, Any], actor_key: str) -> str:
    apify = cfg.get("apify") if isinstance(cfg.get("apify"), dict) else {}
    actors = apify.get("actors") if isinstance(apify.get("actors"), dict) else {}
    val = actors.get(actor_key)
    if isinstance(val, str) and val.strip():
        return val.strip()
    if isinstance(val, dict) and val.get("actor_id"):
        return str(val["actor_id"])
    raise AcquisitionError(
        f"No Actor ID configured for {actor_key!r}. Set it in config/data_sources.json."
    )


def nested_actor_id(cfg: dict[str, Any], source: str, role: str) -> str | None:
    apify = cfg.get("apify") if isinstance(cfg.get("apify"), dict) else {}
    actors = apify.get("actors") if isinstance(apify.get("actors"), dict) else {}
    node = actors.get(source)
    if not isinstance(node, dict):
        return None
    role_node = node.get(role)
    if isinstance(role_node, dict) and role_node.get("actor_id"):
        return str(role_node["actor_id"])
    return None


def resolve_actor_id(cfg: dict[str, Any], source_cfg: dict[str, Any]) -> str:
    inline = source_cfg.get("actor_id")
    if isinstance(inline, str) and inline.strip():
        return inline.strip()
    actor_key = str(source_cfg.get("actor_key") or "")
    if actor_key:
        try:
            return actor_id_for(cfg, actor_key)
        except AcquisitionError:
            pass
    source = str(source_cfg.get("source") or "")
    role = str(source_cfg.get("role") or "")
    nested = nested_actor_id(cfg, source, role) if source and role else None
    if nested:
        return nested
    raise AcquisitionError("No Actor ID configured for this acquisition profile.")


def source_config(cfg: dict[str, Any], source: str) -> dict[str, Any]:
    sources = cfg.get("sources") if isinstance(cfg.get("sources"), dict) else {}
    raw = sources.get(source)
    if not isinstance(raw, dict):
        raise AcquisitionError(f"Unknown acquisition source {source!r}.")
    return dict(raw)


def clamp_limit(source_cfg: dict[str, Any], limit: int | None) -> int:
    default = int(source_cfg.get("default_limit") or 25)
    maximum = int(source_cfg.get("max_limit") or 100)
    value = default if limit is None else int(limit)
    return max(1, min(value, maximum))


def _query_list(payload: dict[str, Any]) -> list[Any]:
    for key in ("keywords", "searchQueries", "queries", "hashtags", "instagramUsernames", "creators"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            return value
    return []


def build_actor_input(source_cfg: dict[str, Any], limit: int) -> dict[str, Any]:
    payload = deepcopy(source_cfg.get("actor_input") or {})
    if not isinstance(payload, dict):
        payload = {}
    creators = source_cfg.get("_creators") or payload.get("instagramUsernames") or payload.get("creators")
    creator_key = str(source_cfg.get("creator_input_key") or "")
    if isinstance(creators, list) and creators:
        if creator_key:
            payload[creator_key] = list(creators)
        elif "instagramUsernames" not in payload:
            payload["instagramUsernames"] = list(creators)
    keys = source_cfg.get("limit_input_keys") or []
    fanout = max(1, len(_query_list(payload)))
    per = max(1, limit // fanout) if fanout > 1 else limit
    minimum = int(source_cfg.get("min_per_query") or 0)
    if minimum:
        per = max(per, minimum)
    for key in keys:
        payload[str(key)] = per
    total_key = source_cfg.get("total_limit_key")
    if total_key:
        payload[str(total_key)] = limit
    return payload


def apply_acquisition_mode(cfg: dict[str, Any], source: str, mode: str | None) -> dict[str, Any]:
    """Backward-compatible overlay. Prefer apply_acquisition_profile."""
    name = canonical_profile_name(mode)
    if name is None:
        return cfg
    profiles = profile_definitions(cfg)
    if name in profiles:
        return apply_acquisition_profile(cfg, source, name)
    if name == "tiktok_emerging":
        return _apply_legacy_emerging(cfg, source)
    return apply_acquisition_profile(cfg, source, name)


def _apply_legacy_emerging(cfg: dict[str, Any], source: str) -> dict[str, Any]:
    if source != "tiktok":
        raise AcquisitionError(
            "Emerging-breakout mode is TikTok-only. Instagram is not part of this experiment."
        )
    experiments = cfg.get("experiments") if isinstance(cfg.get("experiments"), dict) else {}
    exp = experiments.get("tiktok_emerging_breakout")
    if not isinstance(exp, dict) or not exp.get("enabled"):
        raise AcquisitionError("TikTok emerging-breakout experiment is not enabled in config.")
    sources = cfg.setdefault("sources", {})
    if not isinstance(sources, dict):
        sources = {}
        cfg["sources"] = sources
    base = dict(sources.get("tiktok") or {})
    queries = list(exp.get("first_run_queries") or exp.get("queries") or [])
    actor_input = deepcopy(exp.get("actor_input") or {})
    if not isinstance(actor_input, dict):
        actor_input = {}
    if queries:
        actor_input["keywords"] = queries
    overlay = {
        "actor_key": exp.get("actor_key") or "tiktok_emerging",
        "default_limit": exp.get("default_limit", base.get("default_limit", 40)),
        "max_limit": exp.get("max_limit", 75),
        "timeout_secs": exp.get("timeout_secs", 300),
        "max_charge_usd": exp.get("max_charge_usd", base.get("max_charge_usd", 0.5)),
        "max_short_seconds": exp.get("max_short_seconds", base.get("max_short_seconds", 60)),
        "limit_input_keys": exp.get("limit_input_keys") or ["maxItemsPerKeyword"],
        "total_limit_key": exp.get("total_limit_key") or "maxTotalResults",
        "actor_input": actor_input,
        "_profile": "tiktok_emerging",
        "_profile_label": "TikTok Emerging (coregent)",
        "_experiment": "tiktok_emerging_breakout",
        "_mode": "emerging",
        "_queries": actor_input.get("keywords") or queries,
        "_window": exp.get("window") or actor_input.get("datePosted"),
        "_regions": [actor_input["region"]] if actor_input.get("region") else exp.get("regions"),
        "_audience_regions": exp.get("regions"),
    }
    sources["tiktok"] = {**base, **overlay}
    return cfg


def apply_acquisition_profile(
    cfg: dict[str, Any],
    source: str,
    profile: str | None,
) -> dict[str, Any]:
    """Overlay a named sampling profile onto a source. Downstream persist/observe stay the same."""
    name = canonical_profile_name(profile)
    if name is None:
        return cfg
    row = get_profile(cfg, name)
    expected = str(row.get("source") or "").strip().lower()
    if expected and expected != source:
        raise AcquisitionError(
            f"Profile {name!r} is for {expected}, not {source}."
        )
    sources = cfg.setdefault("sources", {})
    if not isinstance(sources, dict):
        sources = {}
        cfg["sources"] = sources
    base = dict(sources.get(source) or {})
    actor_input = deepcopy(row.get("actor_input") or {})
    if not isinstance(actor_input, dict):
        actor_input = {}
    if not actor_input:
        actor_input = deepcopy(base.get("actor_input") or {})
        if not isinstance(actor_input, dict):
            actor_input = {}
    queries = list(row.get("first_run_queries") or row.get("queries") or [])
    if queries:
        if "searchQueries" in actor_input or row.get("role") == "fresh_search":
            actor_input["searchQueries"] = queries
            actor_input.pop("hashtags", None)
        elif "keywords" in actor_input or row.get("role") == "emerging":
            actor_input["keywords"] = queries
        elif "hashtags" in actor_input:
            actor_input["hashtags"] = queries
    creators = [str(c).strip().lstrip("@") for c in (row.get("creators") or []) if str(c).strip()]
    if creators:
        key = str(row.get("creator_input_key") or "instagramUsernames")
        actor_input[key] = creators
    if row.get("role") == "creator_reels" and not creators:
        raise AcquisitionError(
            "instagram_creator_reels needs a creators list in config/data_sources.json. "
            "Add public Instagram usernames there; do not hard-code them in Python."
        )
    overlay = {
        "actor_key": row.get("actor_key") or base.get("actor_key"),
        "actor_id": row.get("actor_id"),
        "role": row.get("role"),
        "source": source,
        "default_limit": row.get("default_limit", base.get("default_limit", 25)),
        "max_limit": row.get("max_limit", base.get("max_limit", 100)),
        "timeout_secs": row.get("timeout_secs", base.get("timeout_secs", 180)),
        "max_charge_usd": row.get("max_charge_usd", base.get("max_charge_usd", 0.5)),
        "max_short_seconds": row.get("max_short_seconds", base.get("max_short_seconds", 60)),
        "limit_input_keys": (
            list(row.get("limit_input_keys") or [])
            if "limit_input_keys" in row or row.get("actor_input")
            else (base.get("limit_input_keys") or [])
        ),
        "total_limit_key": row.get("total_limit_key"),
        "min_per_query": row.get("min_per_query"),
        "creator_input_key": row.get("creator_input_key"),
        "actor_input": actor_input,
        "_profile": name,
        "_profile_label": row.get("label") or name,
        "_profile_status": row.get("status"),
        "_mode": name,
        "_experiment": name if name == "tiktok_emerging" else None,
        "_queries": queries or actor_input.get("searchQueries") or actor_input.get("keywords") or actor_input.get("hashtags"),
        "_creators": creators or None,
        "_window": row.get("window")
        or actor_input.get("videoSearchDateFilter")
        or actor_input.get("datePosted"),
        "_regions": [actor_input["country_code"]]
        if actor_input.get("country_code")
        else ([actor_input["region"]] if actor_input.get("region") else row.get("regions")),
        "_audience_regions": row.get("regions") or (cfg.get("audience") or {}).get("regions"),
    }
    sources[source] = {**base, **overlay}
    return cfg
