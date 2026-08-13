# TrendForge

Closed-loop **short-form content intelligence** MVP.

We are **not** building an AI video generator first. Phase 1 proves:

> Can we systematically turn short-form video signals into a ranked list of promising reusable formats?

## What Phase 1 includes

- Manual candidate ingestion (URLs / JSON)
- Structured LLM analysis via OpenRouter (optional key) or stub analyzer
- Deterministic, configurable scoring with transparent breakdowns
- Format family matching via stable `format_key`
- Local dashboard: Opportunity Queue, Candidates, Format Explorer, Format Detail, Ingest
- Schema stubs for variations, assets, Postiz distribution, performance
- Seed demo data (3 format families)

## Stack

- Python 3.11+
- FastAPI + Jinja2
- SQLite + SQLAlchemy 2
- OpenRouter (OpenAI-compatible) when `OPENROUTER_API_KEY` is set

## Quick start

```bash
cd TrendFactory
python -m pip install -e ".[dev]"
copy .env.example .env
python scripts/seed.py --reset
python -m uvicorn trendforge.app:app --app-dir src --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000

### Optional live analysis

1. Put an OpenRouter key in `.env` as `OPENROUTER_API_KEY`
2. Ingest real URLs on `/ingest`
3. Uncheck “Force stub analyzer” and run analysis

**Note:** Cursor / Claude OAuth cannot power in-app analysis. Use OpenRouter (or later another API provider).

## Tests

```bash
python -m pytest
```

## Project layout

```text
src/trendforge/     app, models, scoring, analysis, sources, distribution, templates
config/             scoring_weights.json
scripts/seed.py     demo data
docs/               DATA_SOURCES.md, NEXT_STEPS.md
tests/
```

## Core concepts

- **Content instance ≠ format.** Multiple candidates can share one format.
- LLM extracts formats and score *signals*; it does **not** set commercial `overall_score` / BUILD status.
- Scoring weights live in `config/scoring_weights.json` and are adjustable.

## License

Internal research tool — all rights reserved.
