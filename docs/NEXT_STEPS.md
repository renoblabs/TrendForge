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

## Still later

1. **OpenRouter / LLM ops** — quality on real promoted URLs; format_key merge UI
2. **True Short video** — this slice is a 9:16 Cloud still; 15–30s video + audio is later
3. **Postiz distribution** — still stubbed
4. **Performance learning** — FORMAT × CHARACTER × HOOK × PLATFORM after we have distribution data
5. **Other platforms** — only with a legitimate API that can produce short-video observations; TikTok/IG/CapCut stubs cannot be filled with a free key. See [DATA_SOURCES.md](DATA_SOURCES.md).

## Explicit non-goals until the discovery hypothesis is calibrated

- Treating discovery_score as predictive
- TikTok / Instagram / CapCut / Apify / SocialKit
- Celery, Redis, Docker, cloud deploy
- Automated publishing
