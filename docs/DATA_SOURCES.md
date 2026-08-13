# TrendForge data sources

Phase 1 prioritizes **manual ingestion**. Automated trend sources are stubbed with documented constraints so we do not build brittle scraping as the foundation.

## Implemented

### ManualSource
- Paste URLs (one per line)
- JSON array of candidate objects
- Optional best-effort **oEmbed** enrichment for YouTube and TikTok (official endpoints; never required)

## Stubbed (interfaces only)

| Source | Status | Legitimate access notes |
|--------|--------|-------------------------|
| **TikTokSource** | NotImplemented | TikTok Research API is academic/non-profit only. Commercial APIs do not provide broad public trend discovery comparable to Research API. |
| **YouTubeSource** | NotImplemented | YouTube Data API v3 can search videos/Shorts but `search.list` costs 100 quota units; default quota is tight for research loops. Good Phase 2 candidate with API key + caching. |
| **InstagramSource** | NotImplemented | Instagram Graph API is oriented to owned Business/Creator accounts, not arbitrary public Reels trend mining. |
| **CapCutSource** | NotImplemented | No public documented API for trending templates. |

## Distribution

### PostizProvider
- Abstraction: `DistributionProvider`
- Postiz public API (`https://api.postiz.com/public/v1`) supports create/schedule/list posts, integrations (channels), upload, analytics.
- Phase 1: stub only — no auth, no live calls.

## Production

ComfyUI / partner generation is **catalog-only** in Phase 1 (`ProductionMethod` + cost estimates). Local ComfyUI and Comfy Cloud can be wired in Phase 2 without changing the format intelligence core.
