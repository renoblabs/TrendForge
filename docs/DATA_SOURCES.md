# TrendForge data sources

Phase 1 gathering is **YouTube Data API v3 only**. TikTok / Instagram / CapCut are stubbed because they do **not** have a legitimate unpaid public trend API — not because we forgot to plug them in.

## Live

### YouTubeDiscoveryProvider
- Official YouTube Data API v3 search + videos + channels
- Shorts duration filter, observations, discovery scoring
- See [YOUTUBE_DISCOVERY.md](YOUTUBE_DISCOVERY.md)
- Broad mode defaults to the `north_america_english` sampling profile (`regionCode` US/CA, `relevanceLanguage=en`). That is audience bias, not creator nationality.
- Timed gather: `python scripts/run_gathering.py --loop` (discover every 6h, observe every 90m by default in `config/discovery.json` → `schedule`). Dashboard `/discovery` can also run discover/observe now. The FastAPI process does not keep its own scheduler (uvicorn reload would drop it).

## Stubbed (cannot be filled with a free key)

| Source | Status | Why it stays stubbed |
|--------|--------|----------------------|
| **TikTokSource** | NotImplemented | TikTok Research API is academic/non-profit only. Commercial Display/Content APIs are not broad public trend discovery. |
| **InstagramSource** | NotImplemented | Instagram Graph API is owned Business/Creator accounts, not arbitrary public Reels mining. |
| **CapCutSource** | NotImplemented | No public documented API for trending templates. |
| **YouTubeSource** (old `TrendSource`) | Unused stub | Live gathering is `YouTubeDiscoveryProvider`, not this class. |

Do not scrape those platforms, and do not wire Apify / SocialKit / unofficial “trend” wrappers as a substitute.

## Unpaid APIs that exist — not short-form discovery

These are free or free-tier, but they are **not** drop-in Shorts sources, so they are not stubbed next to TikTok:

- **Reddit** (OAuth app, rate-limited) — posts/comments, not native short video metrics.
- **Bluesky / Mastodon** — public posts, not Shorts-shaped evidence.
- **Twitch Helix** — clips/streams, different product.
- **YouTube channel RSS** — no view counts; useless for velocity.

Revisit a source only when it has a documented public API that can produce comparable observations (views over time on short video). Until then, extra platforms are manual ingest URLs only.

## Distribution

### PostizProvider
- Abstraction: `DistributionProvider`
- Postiz public API (`https://api.postiz.com/public/v1`) supports create/schedule/list posts, integrations (channels), upload, analytics.
- Phase 1: stub only — no auth, no live calls.

## Production

Comfy Cloud generation is wired through Cursor Agent CLI + native MCP. Local ComfyUI HTTP is not used.
