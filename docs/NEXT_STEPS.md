# TrendForge — next steps

Phase 1: format intelligence from manual ingest.
YouTube Discovery Engine v1: quantitative Shorts discovery + observation history.

## Done in this milestone

- Broad YouTube sampler with configurable `north_america_english` profile (sampling bias, not creator nationality)
- Observation time series, velocity / acceleration / creator lift
- High-signal format analysis plus **family-level opportunity scores** (BUILD/WATCH/REJECT) on live YouTube families only
- **Format ideation** (`format-ideation-v1`) for original mechanic mutations on Format Opportunities — not evidence
- **Production specification** (`production-spec-v1`) briefs attached automatically to generation jobs
- **Generation Control Plane** — dashboard Generate → `generation_jobs` → Cursor/Claude agent → native **Comfy Cloud MCP** (FastAPI does not call Comfy)
- **YouTube gathering schedule** — `scripts/run_gathering.py --loop` (discover 6h / observe 90m; not Celery)
- **Thin TikTok + Instagram samples via Apify** — Data Acquisition Engine v1. Recurring crawls off.
- **Acquisition profiles v1** — `tiktok_trending`, `tiktok_fresh_search`, `instagram_creator_reels` plus kept hashtag evidence (`deprecated_for_discovery`). Same downstream pipeline; profile only changes sampling. `/acquisition` compares discovery / recentness / high-signal yields.
- **TikTok emerging-breakout experiment v1** — `coregent/tiktok-keyword-search-scraper` via `--mode emerging`. Recency filter worked; short-form yield did not. Recommendation **ITERATE** — do not scale. Hashtag Actors remain the previous, insufficient popular samples.

## Still later

0. **Do not schedule Apify yet.** Fresh-search v1.1 stayed 100% recent and short-form rose 50%→80% on a small sample after dropping `story`. `animal` produced one useful clip; `funny`/`AI`/`transformation` were empty this window. Creator Reels established lift for Dude Perfect / MKBHD (median 0.96×); `thetryguys` returned 0. Observe later with `python scripts/observe.py --source tiktok --profile tiktok_fresh_search --dry-run`. Do not LLM the sample yet.
1. **OpenRouter / LLM ops** — quality on real promoted URLs; format_key merge UI
2. **True Short video** — this slice is a 9:16 Cloud still; 15–30s video + audio is later
3. **Postiz distribution** — still stubbed
4. **Performance learning** — FORMAT × CHARACTER × HOOK × PLATFORM after we have distribution data
5. **CapCut** — still no official public trend API. See [DATA_SOURCES.md](DATA_SOURCES.md).

## Explicit non-goals until the discovery hypothesis is calibrated

- Treating discovery_score as predictive
- CapCut / SocialKit as primary sources
- Celery, Redis, Docker, cloud deploy
- Automated publishing
