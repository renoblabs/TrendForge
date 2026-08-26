# Generation and Quality-First Production

TrendForge currently preserves two distinct paths.

## Legacy one-shot control plane

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

The repository has progressed beyond the original still-image plumbing slice: historical jobs may contain stills or a one-shot Cloud video. The architecture is still one continuous generation request, so it remains a baseline—not the target production method. `variant_count` remains stored only.

## Quality-first staged path

```text
ProductionSpec
    → Production Recipe
    → immutable Recipe Version snapshot
    → ordered Shots
    → Attempts
    → Assets and lineage
    → Approval Gates
    → Frozen successful Recipe Version
```

The staged path lives at `/production/recipes`. It does not launch Cursor, Claude Code, Comfy Cloud, local ComfyUI, or any media generator in this phase. A planned attempt is explicitly `AWAITING_AGENT_EXECUTION` until a future MCP-capable agent exports a truthful result contract.

The first real benchmark is chosen at `/production/benchmarks`. Eligible ProductionSpecs are shown with evidence and production context, but no score or completeness rule selects one automatically. Only an explicit `SELECT AS QUALITY BENCHMARK` action records `USER_SELECTED` and creates recipe v1. Until then, the correct status is `AWAITING_USER_SELECTION`.

Five gates are mandatory, in order:

1. visual bible approval;
2. every required shot keyframe approved;
3. every required shot motion output approved;
4. rough cut approved;
5. final render approved.

A rejected shot is retried by creating a new attempt for that shot. Prior attempts and approval events are never deleted. Replacing an approved visual bible invalidates keyframe, motion, rough-cut, and final approvals. Replacing a keyframe invalidates only that shot's motion plus downstream rough/final approvals. Replacing motion invalidates rough/final approvals. This is conservative deterministic invalidation, not a general dependency graph.

Deterministic local assembly with FFmpeg is represented separately from generative compute. The repository records audio, captions, assembly settings, costs, elapsed time, QA, and continuity results without expecting one video model to solve them all.

See [PRODUCTION_RECIPES.md](PRODUCTION_RECIPES.md) for contracts and commands.

## Secrets

Do not put Comfy credentials in TrendForge. The agent inherits Cursor MCP auth. Job logs redact keys.

Capability snapshots and staged result payloads reject secret-like keys/values and use canonical JSON SHA-256 checksums. Native Comfy Cloud capabilities remain unverified in this Codex session; `config/generation.json` is not treated as a live capability inventory.
