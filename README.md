# TrendForge

Closed-loop **short-form content intelligence** MVP.

Phase 1: turn short-form signals into ranked reusable formats.
YouTube Discovery v1: find Shorts from **objective performance data**, not only human-picked URLs.

## What it includes

- Manual candidate ingestion (URLs / JSON)
- YouTube Shorts discovery via the official Data API (optional `YOUTUBE_API_KEY`)
- Optional TikTok + Instagram **Data Acquisition** via Apify (`APIFY_API_TOKEN`) — small manual samples, not scheduled crawls
- Repeated observations, velocity, acceleration, creator-relative lift
- Structured LLM analysis via OpenRouter (optional) or stub analyzer
- Format-family ideation (original concept mutations; not performance evidence)
- Production specifications from selected ideas
- **Generation Control Plane** — dashboard Generate launches Cursor Agent CLI → native Comfy Cloud MCP (9:16 still for this slice)
- Deterministic format scoring **and** a separate discovery score
- Dashboards: Opportunity Queue, **Discovery**, **Acquisition**, Format Opportunities, **Generation**, Candidates, Formats, Ingest
- Schema stubs for variations, assets, Postiz, performance

## Stack

- Python 3.11+
- FastAPI + Jinja2
- SQLite + SQLAlchemy 2
- OpenRouter when `OPENROUTER_API_KEY` is set
- YouTube Data API v3 when `YOUTUBE_API_KEY` is set
- Apify when `APIFY_API_TOKEN` is set (TikTok + Instagram samples)

## Quick start

```bash
cd TrendFactory
python -m pip install -e ".[dev]"
copy .env.example .env
python scripts/seed.py --reset
python -m uvicorn trendforge.app:app --app-dir src --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000

### YouTube discovery

1. Enable YouTube Data API v3 and set `YOUTUBE_API_KEY` in `.env`
2. Edit queries/windows in `config/discovery.json` if needed
3. Run:

```bash
python scripts/discover_youtube.py
python scripts/discover_youtube.py --broad --limit 10 --no-promote
python scripts/discover_youtube.py --broad --profile north_america_english --limit 10 --no-promote
python scripts/observe_youtube.py
python scripts/run_gathering.py --loop
python scripts/analyze_high_signal.py
```

4. Open http://127.0.0.1:8733/discovery (use a non-8000 port if 8000 is taken) and `/discovery/opportunities`

Details: [docs/YOUTUBE_DISCOVERY.md](docs/YOUTUBE_DISCOVERY.md)

### TikTok / Instagram via Apify

1. Create a free Apify account (includes **$5/month** credits; Starter is **$29** prepaid usage).
2. Copy an API token from [console.apify.com/settings/integrations](https://console.apify.com/settings/integrations) into `.env` as `APIFY_API_TOKEN`.
3. Actor IDs, profiles, queries, and Instagram creator handles live in `config/data_sources.json` (not hardcoded).
4. Run a **profile**, not a generic hashtag firehose:

```powershell
python scripts/acquire.py --source tiktok --profile tiktok_trending --dry-run
python scripts/acquire.py --source tiktok --profile tiktok_trending --limit 25
python scripts/acquire.py --source tiktok --profile tiktok_fresh_search --limit 25
python scripts/acquire.py --source instagram --profile instagram_creator_reels --limit 25
python scripts/observe.py --source tiktok --profile tiktok_fresh_search --dry-run
```

Hashtag profiles remain available as `tiktok_hashtag` / `instagram_hashtag` and are marked **deprecated_for_discovery**. Do not scale them. Recurring Apify crawls stay **off**. YouTube gathering is unchanged (`scripts/run_gathering.py --loop`).

TikTok fresh-search queries and Instagram creator handles live in `config/data_sources.json`. Creator lift is measurement-only (median of other same-creator items; not part of discovery_score).

Compare profiles on `/acquisition`. Yields there are acquisition-quality ratios (new / persisted, recent / kept), not predictive scores.

See [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md).

### Generation (Comfy Cloud)

1. Authenticate native Cursor `comfy-cloud` MCP (`https://cloud.comfy.org/mcp`). Local ComfyUI is not used. Install Cursor Agent CLI (`agent`) so Generate can launch it.
2. Create ideas on Format Opportunities
3. Open `/generation`, click **Generate** (creates a job and starts Cursor Agent CLI)
4. The agent executes the job via Comfy Cloud MCP; TrendForge applies `result.json` when the process exits

Details: [docs/GENERATION.md](docs/GENERATION.md)

### Optional live format analysis

1. Set `OPENROUTER_API_KEY`
2. Promote/analyze from Discovery or Ingest (uncheck stub)

**Note:** Cursor / Claude OAuth cannot power in-app analysis.

## Tests

```bash
python -m pytest
```

## What this does NOT prove yet

- Discovery weights are a **calibration starting point**, not a predictive model.
- A high discovery score is not proof a format will work when we produce it.
- YouTube-only: this is not cross-platform trend detection.
- Creator baseline is estimated from videos TrendForge has already seen, not from YouTube’s full channel history.
- Search quota is coarse; we will miss Shorts that never match the sampling queries.

## License

Internal research tool — all rights reserved.
