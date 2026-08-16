from __future__ import annotations

import pytest

from trendforge.config import load_discovery_config
from trendforge.discovery.profiles import (
    classify_language,
    keep_language_candidate,
    load_named_profile,
    resolve_active_profile,
)
from trendforge.discovery.pipeline import resolve_search_plan
from trendforge.discovery.provider import DiscoveryError


def test_default_config_loads_north_america_english_profile():
    cfg = load_discovery_config()
    assert cfg["default_profile"] == "north_america_english"
    assert cfg["broad"]["profile"] == "north_america_english"
    assert cfg["broad"]["unconstrained_query"] == "#shorts"
    profile = load_named_profile(cfg, "north_america_english")
    assert profile.language == "en"
    assert profile.regions == ["US", "CA"]
    global_profile = load_named_profile(cfg, "global")
    assert global_profile.language is None
    assert global_profile.regions == []


def test_broad_selects_default_profile_topic_does_not():
    cfg = load_discovery_config()
    broad = resolve_active_profile(cfg, mode="broad")
    assert broad is not None
    assert broad.name == "north_america_english"
    topic = resolve_active_profile(cfg, mode="topics")
    assert topic is None
    explicit = resolve_active_profile(cfg, mode="topics", profile_name="north_america_english")
    assert explicit is not None
    assert explicit.name == "north_america_english"


def test_unknown_profile_raises():
    cfg = load_discovery_config()
    with pytest.raises(DiscoveryError, match="Unknown discovery profile"):
        load_named_profile(cfg, "mars")


def test_classify_language_keep_reject_unknown():
    assert classify_language({"defaultLanguage": "en"}) == "en"
    assert classify_language({"defaultLanguage": "en-US"}) == "en"
    assert classify_language({"defaultLanguage": "en-CA"}) == "en"
    assert classify_language({"defaultAudioLanguage": "en-GB"}) == "en"
    assert classify_language({"defaultLanguage": "eng"}) == "en"
    assert classify_language({"defaultLanguage": "English"}) == "en"
    assert classify_language({"defaultLanguage": "fr"}) == "non_en"
    assert classify_language({"defaultAudioLanguage": "es"}) == "non_en"
    assert classify_language({"defaultLanguage": "ja", "defaultAudioLanguage": "en-GB"}) == "en"
    assert classify_language({"defaultLanguage": "en", "defaultAudioLanguage": "hi"}) == "en"
    assert classify_language({}) == "unknown"
    assert classify_language({"defaultLanguage": ""}) == "unknown"
    assert classify_language({"defaultLanguage": None, "defaultAudioLanguage": None}) == "unknown"
    assert classify_language({"defaultAudioLanguage": "en-US"}) == "en"
    assert classify_language({"defaultLanguage": "zxx"}) == "unknown"
    assert keep_language_candidate("en") is True
    assert keep_language_candidate("unknown") is True
    assert keep_language_candidate("non_en") is False


def test_resolve_search_plan_uses_unconstrained_query_for_empty_terms():
    cfg = load_discovery_config()
    queries, order, _after, kind = resolve_search_plan(cfg, mode="broad")
    assert queries == ["#shorts"]
    assert order == "viewCount"
    assert kind == "discover_broad"
    topic_queries, topic_order, _, topic_kind = resolve_search_plan(cfg, mode="topics")
    assert "AI" in topic_queries
    assert topic_order == "date"
    assert topic_kind == "discover"
