# TrendForge data sources

Live gathering is **YouTube Data API v3** plus optional **TikTok + Instagram acquisition via Apify**. After normalization, all three platforms share `content_candidates` and `candidate_observations`. CapCut still has no official public trend API.

An acquisition **profile** only changes how candidates are sampled. Everything still flows:

Acquire → Normalize → Deduplicate → Persist → Observe → Deterministic signals → High-signal selection → LLM analysis

Acquisition itself does **not** send items to the LLM. Recurring Apify crawls stay off.

## Live

### YouTubeDiscoveryProvider
- Official YouTube Data API v3 search + videos + channels
- Shorts duration filter, observations, discovery scoring
- See [YOUTUBE_DISCOVERY.md](YOUTUBE_DISCOVERY.md)
- Timed gather: `python scripts/run_gathering.py --loop` (YouTube only; Apify is not on this schedule)

### Data Acquisition Engine (Apify)

Generic `AcquisitionProvider` + `ApifyClient`. Source, provider, and **profile** are separate. Actor IDs and input live in `config/data_sources.json` (`apify.actors` + `profiles`). Do not hard-code Actor IDs in Python.

| Source | Profile | Actor | Status |
| --- | --- | --- | --- |
| YouTube | official API | n/a | Baseline. YouTube Apify slot unused. |
| TikTok | `tiktok_trending` | `xtracto/tiktok-trending-scraper` | Explore-ranked videos, `country_code=US`. Not a hashtag search. |
| TikTok | `tiktok_fresh_search` | `clockworks/tiktok-scraper` | Keyword search, `LATEST` + `PAST_24_HOURS`. Distinct from hashtag-popular. |
| TikTok | `tiktok_hashtag` | `clockworks/tiktok-scraper` | **deprecated_for_discovery**. Popular hashtag feed. Do not scale. |
| TikTok | `tiktok_emerging` | `coregent/tiktok-keyword-search-scraper` | Earlier keyword experiment. Recency worked; short-form yield did not. |
| TikTok | `tiktok_discover` | `clockworks/tiktok-explore-scraper` | **deferred**. Residential `proxyCountryCode`; trending already covers Explore videos. |
| Instagram | `instagram_creator_reels` | `instagram-scraper/instagram-profile-reels-scraper` | Profile Reels tab for creator-relative baselines. |
| Instagram | `instagram_hashtag` | `apify/instagram-hashtag-scraper` | **deprecated_for_discovery**. Global firehose. Do not scale. |

Identity is `platform + external_id`. The same video found by two profiles is one candidate; `acquisition_profiles` / `profiles_that_found_candidate` records both.

Populate Instagram handles in `profiles.instagram_creator_reels.creators`. Do not hard-code creators in Python. The starter list is a small public English/NA entertainment set for data-quality tests, not automated creator discovery.

```powershell
python scripts/acquire.py --source tiktok --profile tiktok_trending --limit 25
python scripts/acquire.py --source tiktok --profile tiktok_fresh_search --limit 25
python scripts/acquire.py --source instagram --profile instagram_creator_reels --limit 25
python scripts/acquire.py --source tiktok --profile tiktok_trending --dry-run
```

Legacy `--mode emerging` still maps to `tiktok_emerging`. Prefer `--profile`.

Dashboard: `/acquisition` shows **Profile comparison** (latest succeeded run per profile) plus yield definitions:

- **Discovery yield** = new / persisted (not a predictor)
- **Recentness yield** = items within the configured freshness window / kept candidates
- **High-signal yield** = deterministic high-signal labels / persisted

Each run stores `profile`, Apify run/dataset IDs, and `actual_cost` when Apify reports it. Missing metrics stay `null`. Language: strong English keep, strong non-English reject, unknown keep.

These are third-party scrapers with an invoice. Actors break when platforms change. They are not official TikTok/Meta APIs.

### Why hashtag feeds are insufficient (kept as evidence)

| | TikTok hashtag (`clockworks/tiktok-scraper`) | Instagram hashtag (`apify/instagram-hashtag-scraper`) |
| --- | --- | --- |
| Raw / new / dup / rejected | 24 / 0 / 9 / 15 | 24 / 9 / 10 / 5 |
| Recent | 4% | 96% |
| Short | 42% | 79% |
| English | 83% | 0% known |
| NA signal | none | 0% |
| Cost | $0.0898 | $0.00 |
| Verdict | Already-viral / old. Do not scale as the primary emerging feed. | Globally mixed; no English/NA field. Do not scale as the primary discovery feed. |

### Why `tiktok_trending` uses `xtracto/tiktok-trending-scraper`

Inspected Actor input (not invented): `content_type=video`, `country_code`, `limit`. Output is nested TikTok Explore ranking (`id`, `desc`, `author.uniqueId`, `stats.playCount`, `video.duration`, `createTime`). `country_code` is a ranking bias, not a hard geo fence. There is no language field. Deprecated hashtag/creator/music modes emit `_warning` and are skipped.

`clockworks/tiktok-explore-scraper` was considered and deferred: it is category Explore with residential `proxyCountryCode`.

### Why `tiktok_fresh_search` still uses `clockworks/tiktok-scraper`

Same Actor as the hashtag profile, different **input**. Schema-valid fields: `searchQueries`, `searchSection=/video`, `videoSearchSorting=LATEST`, `videoSearchDateFilter=PAST_24_HOURS`, `resultsPerPage`. LATEST and PAST_24_HOURS are charged Actor filters. Queries stay small (`AI`, `comedy`, `funny`, `POV`, `character`, `transformation`, `animal`) so one profile does not spawn hundreds of Apify runs. `story` was removed after live v1 produced too many long videos. Do not send `hashtags` on this profile.

### Earlier emerging Actor (`coregent/tiktok-keyword-search-scraper`)

Kept as `tiktok_emerging` / `--mode emerging`. Compared on Apify Store listings (Aug 2026):

| Actor | Search | Last 24h | Sort latest | Region | Shorts filter | Metrics | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `clockworks/tiktok-scraper` hashtags | hashtags / URLs | no | no | proxy country | no | excellent | First sample: 200K–23M view established clips |
| `clockworks/tiktok-scraper` fresh search | `searchQueries` | `PAST_24_HOURS` | `LATEST` | proxy country | no | excellent | Current freshness instrument |
| `coregent/tiktok-keyword-search-scraper` | keywords | `last24Hours` | `latest` | `US` | duration in output; we filter ≤60s | views/likes/comments/shares/saves/followers | Recency worked; 31% short |
| `paul_44/tiktok-search` | keywords | calendar `today` only | `LATEST` | `location=US` | min duration, not max | views/likes/comments/shares | cheaper but weaker recency window |
| `automation-lab/tiktok-search-scraper` | keywords | no | no | no | no | basic | rejected |

The coregent Actor takes **one** region code. We sample `US` (Canada is listed as a desired audience, not a second scrape). Region is sampling bias, not creator nationality.

Use only schema-valid Actor input. Marketing README fields such as `includeCaption` are **rejected by the live validator** (HTTP 400). Do not enable `includeDownloadUrl` (extra charge).

### Live experiment v1 (2026-08-16) — coregent emerging — **ITERATE**

Controlled run, not a scale-up. Apify run `g4ALYW7Jq8nfXMgxh`, dataset `2f1ejwaCsh2fCROnZ`, cost **$0.026** (`usageTotalUsd`).

| | Previous (`clockworks` hashtag) | Emerging v1 (`coregent`) |
| --- | --- | --- |
| Input | hashtags pov/comedy/storytime | keywords POV, skit, comedy, storytime, plot twist · `sort=latest` · `datePosted=last24Hours` · `region=US` · cap 40 |
| Raw | 24 | 13 (asked 40; sparse last-24h search) |
| New / dup / rejected | 1 / 10 / 13 | 4 / 0 / 9 |
| Recent ≤24h | no (days–weeks old popular clips) | **100%** (median age **~4.0h**) |
| Short ≤60s | ~2/3 of raw | **31%** (4/13). All 9 rejects were duration 72–276s |
| Language field | `textLanguage` present | **absent** (`english_rate=0`, `unknown_language_rate=1`). Captions were mostly English; unknown kept |
| NA signal | none | 100% `region=US` — search parameter echoed back, not creator location |
| Median views | ~200K–23M established | **14,681** (range 316–471,208) |
| Cost | $0.0898 | $0.026 |

Query yield: POV 6, comedy 3, storytime 4, **skit 0**, **plot twist 0**. Every `storytime` row was 171–178 seconds. Do not scale this source yet. Recency is solved; short-form sampling is not.

Recommendation: **ITERATE** (keep the Actor, change the query mix, do not increase volume).

### Acquisition profiles live v1 (2026-08-16)

Controlled `--limit 25` runs. No hashtag re-runs. Candidates stayed `SKIPPED` (no OpenRouter). Recurring crawls stay off.

TikTok `textLanguage=un` is unspecified (same idea as BCP-47 `und`). After this experiment we map `un` → unknown/keep. The live trending rejects below still counted `un` as non-English (7 of 21 language rejects). That does **not** change the trending verdict: even the English survivors were old and already large.

| | TikTok Trending | TikTok Fresh Search | Instagram Creator Reels |
| --- | --- | --- | --- |
| Actor | `xtracto/tiktok-trending-scraper` | `clockworks/tiktok-scraper` search | `instagram-scraper/instagram-profile-reels-scraper` |
| Apify run | `OgNbLaRkzIoMYTZ7j` | `EyqQVBm84zvHsu8Pn` | `ht256ulVcwYP4Dszf` |
| Dataset | `l50HorGqDh3wh5qa0` | `sGddXxKx5WGQrQaDg` | `DGIRvgDhpWMDSq92q` |
| Input | `content_type=video` · `country_code=US` · `limit=25` | 6 queries · `LATEST` · `PAST_24_HOURS` · 4/query | `@saturdaynightlive` `@dudeperfect` `@mkbhd` · 8/profile |
| Raw | 25 | 16 (14 videos + 2 empty-query errors) | 16 |
| New / dup / rejected | 4 / 0 / 21 | 7 / 0 / 9 | 13 / 0 / 3 |
| Reject reasons | language 21 (`en` 4, `es` 7, `ru` 5, `un` 7, `vi` 1, `ha` 1) | not-short 4, language 3, unnormalizable 2 | not-short 3 |
| Recent ≤24h | **0%** (median age **~27d**; min 74h) | **100%** (median age **~0.9h**) | **0%** (median **~12d**; min 30h) |
| Short | 88% | 50% (7/14) | 81% (≤90s) |
| English | 16% known | 79% of normalized | 0% known / 100% unknown (captions look English; kept) |
| NA signal | 100% `country_code=US` echo, not creator location | none (not fabricated) | none (creators chosen as NA-relevant) |
| Median views | **563,900** (53k–108M) | **1,409** (173–1.3M; kept sample hundreds–6k) | **1,835,717** (mega-creator Reels) |
| Metric completeness | views/likes/comments/shares/saves/followers/timestamps **complete** | same on the 14 videos | views/likes/comments/followers/timestamps complete; **shares/saves null** |
| High-signal (deterministic) | 0 / 4 | 1 / 7 (`neptiz` EMERGING) | 1 / 13 (one Dude Perfect reel) |
| Discovery yield (new/persisted) | 100% | 100% | 100% |
| Recentness yield | 0% | 100% | 0% |
| Cost | **$0.022** | **$0.097** | **$0.018** |
| Cost / new candidate | $0.005 | $0.014 | $0.001 |
| Verdict | **REJECT** as emerging feed | **ITERATE** — best freshness + early views | **GO for baselines**, not trend discovery |

`saturdaynightlive` returned **0** Reels this run; replace/add handles in `profiles.instagram_creator_reels.creators`. `story` on fresh search still produced long videos (same class of problem as `storytime` on the coregent Actor).

**Primary recurring discovery source (not scheduled yet):** `tiktok_fresh_search`.
**Primary creator-relative instrument:** `instagram_creator_reels`.
Do not scale `tiktok_trending` or hashtag profiles.

Creator baseline (measurement, not discovery_score): median views of other same-creator items already stored, needing 3+ with views. `creator_lift = current_views / baseline`. Null when history, baseline, or current views are insufficient.

### Acquisition refinement v1.1 (2026-08-16)

Query mix: `story` removed; `funny` and `animal` added. Instagram handles: `dudeperfect`, `mkbhd`, `ijustine`, `thetryguys`, `yestheory`. Controlled `--limit 25` only. No hashtag/trending re-runs. Candidates stayed `SKIPPED`.

| | TikTok Fresh Search v1 (#18) | TikTok Fresh Search v1.1 (#22) | Instagram Creator Reels v1.1 (#23) |
| --- | --- | --- | --- |
| Apify run | `EyqQVBm84zvHsu8Pn` | `5R0BPAZB4hLnYcUk4` | `dN7yMvVtFDYTXEj9Q` |
| Input | 6 queries including `story` | 7 queries, no `story`, +`animal`/`funny`, LATEST + PAST_24_HOURS | 5 handles × 5 posts |
| Raw / new / dup / rejected | 16 / 7 / 0 / 9 | 13 / 5 / 2 / 6 | 20 / 8 / 8 / 4 |
| Recent ≤24h | 100% | **100%** | 15% (creator tab, not a trend window) |
| Short | **50%** (7/14) | **80%** (8/10 with duration) | 80% (≤90s) |
| English | 79% | 70% (unknown 10%, `un` now kept) | unknown (kept) |
| High-signal / persisted | 1 / 7 | 2 / 7 | 2 / 16 |
| Cost | $0.097 | **$0.071** | $0.022 |
| Notes | `story` returned long videos | `animal` kept 1 English 21s zoo clip (10k views, 0.3h). `funny`/`AI`/`transformation` were empty-query rows. | `thetryguys` returned 0. Collab owners `jennaezarik`/`pluginheatclub` appeared. 2/5 requested creators have lift baselines (prior Dude Perfect / MKBHD history). Median lift **0.96×**. |

Per-query yield (v1.1 fresh search, new/raw): animal 1/3, character 2/3, POV 2/3, comedy 0/1 (duplicate), funny/AI/transformation 0/1 (rejected/empty).

Creator baseline is measurement-only (median of other same-creator items, need 3+ with views). It is **not** part of `discovery_score`.

TikTok observation: 13 `tiktok_fresh_search` candidates are eligible (`python scripts/observe.py --source tiktok --profile tiktok_fresh_search --dry-run`). Two already have 2 observations because they were re-acquired as duplicates. A dedicated URL-refresh observe was **not** run in this session (no useful time window on the new rows; extra Apify spend). Do not schedule.


## Stubbed

| Source | Status | Notes |
|--------|--------|--------|
| **TikTokSource** / **InstagramSource** | Unused classes | Live path is `scripts/acquire.py`, not these `TrendSource` stubs. |
| **CapCutSource** | NotImplemented | No public documented API. |
| **YouTubeSource** | Unused stub | Live gathering is `YouTubeDiscoveryProvider`. |

## Unpaid APIs that exist — not short-form discovery

- **Reddit**, **Bluesky / Mastodon**, **Twitch Helix**, **YouTube channel RSS** — not comparable Shorts observations.

## Distribution

### PostizProvider
- Phase 1: stub only — no auth, no live calls.

## Production

Comfy Cloud generation is wired through Cursor Agent CLI + native MCP. Local ComfyUI HTTP is not used.
