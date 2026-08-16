# Generation Control Plane (Cursor Agent CLI + Comfy Cloud)

```text
TrendForge dashboard
    → Generation Job (job.json + production_spec.json + AGENT.md)
    → Cursor Agent CLI (headless)
    → native Comfy Cloud MCP
    → Comfy Cloud
    → result.json
    → TrendForge applies result
    → dashboard preview
```

**FastAPI does not call Comfy MCP.** Clicking Generate launches the Cursor Agent CLI. The agent calls Comfy MCP.

There is no Comfy HTTP adapter and no localhost Comfy client inside TrendForge.

## How to run a generation

1. Create ideas on Format Opportunities.
2. Open `/generation`, select an idea, click **Generate**.
3. TrendForge writes `data/generation_jobs/{id}/` and starts a Cursor Agent CLI process (`-p --force --trust --approve-mcps --workspace`).
4. The agent must already be authenticated (`agent login` once, or `CURSOR_API_KEY` in the process environment — not in TrendForge `.env`).
5. The agent reads `AGENT.md` and executes on Comfy Cloud.
6. When the process exits, TrendForge applies `result.json` (same as `python scripts/run_generation_job.py --job-id N --apply-result`).

## Job lifecycle

```text
QUEUED → PREPARING → AGENT_RUNNING (Cursor CLI launched)
  → REVIEWING when result.json is applied
  → COMPLETED or FAILED
```

## This slice

One 9:16 still (first frame). Not a finished Short. `variant_count` is stored only.

## Secrets

Do not put Comfy credentials in TrendForge. The agent inherits Cursor MCP auth. Job logs redact keys.
