# YouTube Discovery Engine v1

This layer finds **YouTube Shorts** from the official Data API, stores repeated metric snapshots, and scores **momentum** separately from the existing format opportunity score.

It does **not** claim to predict virality. Weights in `config/discovery_weights.json` are starting points for calibration.

## API key

1. Create a Google Cloud project.
2. Enable **YouTube Data API v3**.
3. Create an API key (restrict it to that API if possible).
4. Put it in `.env`:

```text
YOUTUBE_API_KEY=...
```

Never commit the key. `.env` is gitignored.

## Commands

```bash
python scripts/discover_youtube.py
python scripts/observe_youtube.py
python scripts/run_gathering.py --loop
```

- `discover_youtube.py` — search configured queries for recent Shorts, upsert candidates, write observations, optionally promote top-N into the existing analyzer (stub LLM unless `--live-analysis`).
- `observe_youtube.py` — re-fetch stats for stored YouTube candidates and append observations.
- `run_gathering.py` — run whichever of those jobs is **due** per `config/discovery.json` → `schedule`. `--loop` keeps checking (default every 60s). `--force` / `--job discover|observe|both` runs immediately. This is the intended always-on gatherer; the dashboard buttons are the same jobs on demand.

Default intervals (quota-aware): **discover every 360 minutes** (~800 search units per topic run; daily quota 10,000), **observe every 90 minutes** (cheap `videos.list`, needed for velocity/acceleration). Tune in JSON, not Python. `live_analysis` stays false unless you want OpenRouter on every promote.

Queries and windows live in `config/discovery.json` (edit JSON, not Python).

Default **topic** sampling queries: AI, comedy, funny, POV, story, animals, transformation, character.

**Broad / non-semantic mode** ignores those terms. It searches recent Shorts (`publishedAfter` + `videoDuration=short`) and ranks by `order=viewCount`, so the sample is “young clips that already have views,” not “clips about comedy.” Then the same observation/velocity scoring applies.

```bash
python scripts/discover_youtube.py --broad --limit 10 --no-promote
```

Tune window/order in `config/discovery.json` under `"broad"`.

YouTube `search.list` currently returns **zero items if `q` is omitted**, even with `videoDuration=short` and `publishedAfter`. Broad mode therefore sends a **format token** `unconstrained_query` (default `#shorts`), not a topic like comedy or AI. Empty `search_terms` are replaced with that token. This is a YouTube API requirement, not a semantic topic sampler.

### English / North America profile (default for `--broad`)

Broad discovery currently uses the `north_america_english` profile:

```json
"language": "en",
"regions": ["US", "CA"]
```

That profile is a **sampling bias** for the first research experiment (English-language, North American-relevant Shorts). It is **not** a guarantee that every result is American or Canadian, and it does **not** require the creator to be located in the US or Canada. An English-speaking creator in the UK, Australia, India, etc. can still be relevant.

YouTube search is biased with `regionCode` (US then CA until the run limit is filled) and `relevanceLanguage=en`. Those values come from `config/discovery.json`, not hard-coded in the provider.

A later `global` profile is already reserved in config (`language`/`regions` empty) so worldwide sampling does not need a redesign. Override on the CLI:

```powershell
python scripts/discover_youtube.py --broad --profile north_america_english --limit 10 --no-promote
python scripts/discover_youtube.py --broad --profile global --limit 10 --no-promote
```

`--broad --limit 10 --no-promote` still works; it uses the configured default profile.

### Language quality filter (v1)

Sampling params are not enough. After `videos.list`, we look at `snippet.defaultLanguage` and `snippet.defaultAudioLanguage` **when present**:

| Evidence | Action |
|----------|--------|
| Strong English (`en`, `en-US`, `en-CA`, …) | keep |
| Strong non-English (language present, none English) | filter |
| Missing / empty language fields | keep |

Titles are not used as a language detector (emojis, slang, names, hashtags). Ambiguous metadata is kept on purpose.

Each persisted candidate stores why it passed in `raw_metadata`: `discovery_profile`, `language_signal`, `region_signal`, `language_evidence`. Discovery-run `notes` JSON includes funnel counts (`search_results`, `video_details`, `shorts_eligible`, `shorts_rejected`, `language_kept`, `language_dropped`, `persisted`) and the effective search parameters (no API key). If search returns nothing, the CLI says so explicitly. It does **not** silently widen to a global sample.

`regionCode` is only a search-sampling parameter. Candidates are not dropped because the channel is outside the US/Canada.

Topic mode is unchanged: it still uses the semantic query list and does not apply the default profile unless you pass `--profile` explicitly.

Windows: `last_24_hours`, `last_48_hours`, `last_7_days`.

## How scoring works

Discovery score is **not** `views`.

From observation history:

| Signal | Rule |
|--------|------|
| `age_hours` | now − published_at |
| `views_per_hour` | last two observations if present; else views / age |
| `acceleration` | (recent velocity) / (previous velocity) from **three** view snapshots; otherwise `null` |
| `like_rate` / `comment_rate` | likes or comments / views; `null` if views missing or 0 |
| `creator_baseline` | median views of **other** videos from the same `channel_id` already in TrendForge; needs `min_creator_videos` (default 3); otherwise `null` (marked estimated when present) |
| `creator_lift` | current views / baseline; `null` if baseline missing |

Each available signal is scaled 0–100 using `config/discovery_weights.json` normalization, then combined with configured weights. **Missing signals are omitted and remaining weights are renormalized.**

Labels (independent; a row can have more than one):

- **POPULAR** — high views and older (often a large channel; not the research target)
- **EMERGING** — young + strong views/hour
- **ACCELERATING** — acceleration ≥ configured factor

## High-signal format analysis

The LLM does **not** decide virality. Discovery labels/score select a small set, then analysis explains the attention mechanic.

Eligible by default (`config/discovery.json` → `analysis`):

- `ACCELERATING`, or
- `EMERGING` with `discovery_score >= emerging_min_discovery_score` (default 20)

```bash
python scripts/analyze_high_signal.py
python scripts/analyze_high_signal.py --live-analysis
```

`--live-analysis` uses OpenRouter when `OPENROUTER_API_KEY` is set. Live high-signal YouTube Shorts that were previously stub-analyzed (or recorded an older prompt version) are eligible again. A new structured result is **appended** to `analysis_history`; historical rows are not mutated. Seed/demo records are not analyzed. Without an API key, live re-analysis is skipped and existing stub results are left unchanged.

Dashboard: candidate format intelligence on `/discovery/candidates/{id}` and family opportunity plus ideation on `/discovery/opportunities`.

### Discovery vs intelligence vs ideation

- **Discovery** is quantitative evidence (`discovery_score`, labels, observations).
- **Intelligence** is LLM interpretation of high-signal clips (`format-intelligence-v1.1`): surface, specific format, mechanic, family.
- **Ideation** (`format-ideation-v1`) generates original creative mutations of a discovered mechanic. **Ideation output is creative suggestion, not evidence that an idea will perform.** It is stored in `format_brainstorm_sets` and never becomes a candidate, observation, or family-evidence row.
- **Production specification** (`production-spec-v1`) turns a selected brainstorm idea into an executable creative brief (beats, shots, continuity, assets, QA). Specs are stored in `production_specs` and are **not** videos, ComfyUI workflows, or research evidence.

From Format Opportunities: **Generate ideas**, then **Create Production Spec** on an idea. Open the spec page and download `production_spec.md`. This does not generate media.

### Candidate vs family scores

- **`discovery_score`** (individual video): how interesting this particular video's observed performance is (velocity, acceleration, lift, recency). It is not a family verdict.
- **`family_opportunity_score`** (format family): how attractive the recurring format currently looks from live evidence, momentum, replication, and production ratings. It is not `discovery_score` copied onto the family.
- **Evidence strength** (`LOW` / `MEDIUM` / `HIGH`): how much independent live evidence supports the family classification (candidate count, unique channels, presence of performance observations). It is not a synonym for viral.

Seed/demo records (`data_origin=seed`, or URLs/titles like `seed-…` / `(demo)`) are excluded from family opportunity metrics. They remain in the database for development.

Family status (`BUILD` / `WATCH` / `REJECT`) is deterministic from config in `config/family_weights.json`. The LLM does not choose it.

```text
family_opportunity_score =
    weighted mean of available:
      recurrence, unique_channels, performance, momentum, production
```

Missing signals are omitted and remaining weights are renormalized. Individual `discovery_score` is not reused as the family score.

Default status (thresholds in config, not code):

- **BUILD** — score, unique channels, candidate count, and accelerating count all meet minima, and evidence is MEDIUM or HIGH. LOW evidence never BUILD.
- **WATCH** — score meets watch minimum, or enough independent recurrence without meeting BUILD.
- **REJECT** — otherwise.

Default evidence:

- **HIGH** — enough unique channels and candidates **and** at least one performance observation
- **MEDIUM** — some independent recurrence
- **LOW** — otherwise

A family can be HIGH evidence and still WATCH (established, not currently attractive). LOW evidence with a high score stays WATCH, not BUILD.

## Shorts filter

Search uses `videoDuration=short` (&lt; 4 minutes). We then keep only videos whose ISO 8601 `contentDetails.duration` is ≤ `max_short_seconds` (default 60). Logic is isolated in `discovery/shorts.py`.

## Quota

`search.list` costs **100 units** per page. Default config is 8 queries × 1 page ≈ **800 search units** per discovery run, plus cheap `videos.list` / `channels.list`. Default daily quota is 10,000 units. Quota errors are stored on the discovery-run record; the job does not crash the database write.

## Promotion

LLM analysis is optional and expensive. New discoveries are `SKIPPED` until promotion (`promote_top_n`, default 25). Promotion uses the existing TrendForge analyzer (stub without OpenRouter).

## Dashboard

Open `/discovery` for Emerging / Accelerating / Popular / Promoted lists, recent runs, schedule status, and Run now. Candidate detail shows the observation series as a simple chart.

Keep a gatherer process open while researching:

```powershell
python scripts/run_gathering.py --loop
```

## What this does not prove

See README. In short: mechanism validation, not a finished trend oracle, and not cross-platform detection.
