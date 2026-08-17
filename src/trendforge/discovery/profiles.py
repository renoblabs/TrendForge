from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from trendforge.discovery.provider import DiscoveryError

ENGLISH_ALIASES = {"en", "eng", "english"}
# TikTok emits "un"; BCP-47 uses "und". Both mean unspecified, not non-English.
NON_LANGUAGE_CODES = {"zxx", "und", "un"}


@dataclass(frozen=True)
class DiscoveryProfile:
    name: str
    language: str | None
    regions: list[str]


def load_named_profile(cfg: dict[str, Any], name: str) -> DiscoveryProfile:
    profiles = cfg.get("profiles") or {}
    if name not in profiles:
        known = ", ".join(sorted(profiles)) or "(none)"
        raise DiscoveryError(f"Unknown discovery profile {name!r}. Known: {known}")
    raw = profiles.get(name) or {}
    language = raw.get("language")
    if language:
        language = str(language).strip() or None
    regions = [str(r).strip() for r in (raw.get("regions") or []) if str(r).strip()]
    return DiscoveryProfile(name=name, language=language, regions=regions)


def resolve_active_profile(
    cfg: dict[str, Any],
    *,
    mode: str,
    profile_name: str | None = None,
) -> DiscoveryProfile | None:
    """Broad mode uses the configured default profile; topic mode does not unless named."""
    name = (profile_name or "").strip() or None
    if not name and mode == "broad":
        name = (cfg.get("broad") or {}).get("profile") or cfg.get("default_profile")
        name = (str(name).strip() if name else None) or None
    if not name:
        return None
    return load_named_profile(cfg, name)


def search_regions(profile: DiscoveryProfile | None, fallback_region: str | None) -> list[str | None]:
    if profile and profile.regions:
        return list(profile.regions)
    return [fallback_region]


def relevance_language(profile: DiscoveryProfile | None) -> str | None:
    if profile and profile.language:
        return profile.language
    return None


def normalize_language_code(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower().replace("_", "-")
    if not text:
        return None
    primary = text.split("-", 1)[0]
    if primary in ENGLISH_ALIASES or text in ENGLISH_ALIASES:
        return "en"
    if primary in NON_LANGUAGE_CODES or text in NON_LANGUAGE_CODES:
        return None
    return primary


def language_evidence(snippet: dict[str, Any] | None) -> dict[str, Any]:
    snippet = snippet or {}
    return {
        "defaultLanguage": snippet.get("defaultLanguage"),
        "defaultAudioLanguage": snippet.get("defaultAudioLanguage"),
    }


def classify_language(snippet: dict[str, Any] | None, preferred: str = "en") -> str:
    """Return preferred code, 'non_en', or 'unknown'. Missing metadata is unknown."""
    preferred_code = normalize_language_code(preferred) or "en"
    evidence = language_evidence(snippet)
    codes = [
        normalize_language_code(evidence.get("defaultLanguage")),
        normalize_language_code(evidence.get("defaultAudioLanguage")),
    ]
    present = [code for code in codes if code]
    if not present:
        return "unknown"
    if preferred_code in present:
        return preferred_code
    return "non_en"


def keep_language_candidate(signal: str) -> bool:
    """Keep English and unknown; drop only strong non-English evidence."""
    return signal != "non_en"


def sampling_metadata(
    *,
    profile: DiscoveryProfile | None,
    language_signal: str,
    region_signal: str | None,
    snippet: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "discovery_profile": profile.name if profile else None,
        "language_signal": language_signal,
        "region_signal": region_signal,
        "language_evidence": language_evidence(snippet),
        "profile_regions": list(profile.regions) if profile else [],
        "region_meaning": "search sampling bias, not creator location",
    }
