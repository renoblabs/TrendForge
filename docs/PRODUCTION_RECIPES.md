# Production Recipes v1

Production Recipes are TrendForge's quality-first state and approval backbone. They extend the existing ProductionSpec and GenerationJob architecture; they do not replace it and do not add a Comfy client.

```text
Selected idea
    → ProductionSpec (creative source of truth)
    → READY_FOR_BENCHMARK_REVIEW
    → explicit USER_SELECTED decision
    → ProductionRecipe (durable reusable format recipe)
    → ProductionRecipeVersion (immutable spec snapshot)
    → ordered ProductionRecipeShots
    → ProductionAttempts per stage/shot
    → ProductionAttemptAssets ↔ ContentAssets
    → append-only ProductionApprovalEvents
    → frozen successful version
```

## Versioning and immutability

Recipe v1 deep-copies the relevant `ProductionSpec.spec_json`; later ProductionSpec edits do not alter it. A draft/active version can update its visual bible and capability reference. Cloning copies timing and recipe content exactly, creates a new version number, and copies no attempts or approvals.

Once frozen, the version fields and shot definitions are read-only. Further tuning starts from a clone. `SUCCESSFUL` is assigned only by the freeze operation after all five gates are complete, so it points to a frozen current version.

The canonical content checksum covers the ProductionSpec snapshot, visual bible, duration/audio/caption/assembly plans, capability snapshot reference, and ordered shot definitions.

## Stages and gates

Canonical stages are:

```text
PREPARATION → VISUAL_BIBLE → KEYFRAMES → MOTION → AUDIO → ASSEMBLY → FINAL_QA → COMPLETE
```

`LEGACY_ONE_SHOT` identifies imported historical baselines without rewriting their job status.

Canonical gates are:

```text
VISUAL_BIBLE_APPROVAL
KEYFRAME_APPROVAL       (one current approval per required shot)
MOTION_APPROVAL         (one current approval per required shot)
ROUGH_CUT_APPROVAL
FINAL_RENDER_APPROVAL
```

Approval decisions are `APPROVE`, `REJECT`, `REQUEST_CHANGES`, and `REVOKE`. Every decision creates a new event. Reject/request-changes/revoke require a reason. Approval of an output requires a real succeeded matching attempt; a form cannot fabricate execution.

Invalidation is deliberately conservative:

- changing/rejecting the visual bible revokes all downstream approvals;
- replacing/rejecting a shot keyframe revokes only that shot's motion approval and rough/final approvals;
- replacing/rejecting shot motion revokes rough/final approvals;
- replacing/rejecting the rough cut revokes final approval;
- unrelated approved shots remain approved.

Automatic invalidations are also append-only `REVOKE` events with `reviewer=system`.

## Attempts and lineage

Attempt numbers are scoped to recipe version + stage + optional shot. Retrying creates a new row. Failed, rejected, and superseded attempts stay in history.

The `production_attempt_assets` join table records inputs, references, first/last frames, keyframes, motion outputs, audio, captions, cuts, final output, cover, QA artifacts, and the legacy baseline. Local result files receive a SHA-256 checksum. Existing asset files are not moved or rewritten, and legacy assets may honestly retain incomplete lineage.

Costs and elapsed time are aggregated only from stored values. Unknown is not treated as zero. Mixed currencies are reported separately and never combined into a single total without conversion data.

## Native MCP capability snapshot contract

Native Comfy Cloud MCP is not available in this Codex session, so no model/template inventory is inferred from configuration. A future MCP-capable agent exports `production-capability-snapshot-v1` JSON containing:

```json
{
  "contract_version": "production-capability-snapshot-v1",
  "captured_at": "2026-08-25T12:00:00Z",
  "provider": "Comfy Cloud",
  "mcp_server": "comfy-cloud",
  "agent": "cursor-agent",
  "agent_version": "...",
  "raw_snapshot": {},
  "normalized_capabilities": {},
  "notes": "read-only inventory"
}
```

Import and optionally link it:

```powershell
python scripts/production_recipes.py import-capabilities --file snapshot.json --version-id <VERSION_ID>
```

The importer validates structure, rejects secret-like keys/values, computes a deterministic canonical JSON checksum, deduplicates identical input, and stores immutable history. Versions without a snapshot report `NOT_CAPTURED`.

## External attempt contract

Create a planned attempt and export its handoff:

```powershell
python scripts/production_recipes.py create-attempt --version-id <VERSION_ID> --stage KEYFRAMES --shot-id <SHOT_ID>
python scripts/production_recipes.py export-attempt --attempt-id <ATTEMPT_ID> --output attempt_handoff.json
```

This does not launch an agent. A future MCP-capable agent returns `production-attempt-result-v1` with the attempt ID, truthful status, provider/agent, Cloud job ID, reported model/workflow/template, parameters, input assets, local copies of output files, dimensions/duration, cost/currency, timestamps, QA/continuity, or a failure object.

Apply it with:

```powershell
python scripts/production_recipes.py apply-attempt-result --attempt-id <ATTEMPT_ID> --result-file result.json
```

The importer rejects attempt mismatches, local-Comfy claims, successful Cloud claims without a Cloud job ID, missing files, malformed/secret-bearing payloads, and conflicting second results. It registers local output assets and checksums only after validation. A failed result remains failed and fabricates no output.

## Benchmark selection and manifests

The shortlist at `/production/benchmarks` displays eligible ProductionSpecs with format evidence, hook, duration, shot count, complexity, AI leverage, variation potential, and existing generation history. These signals inform review; they never select a benchmark.

An explicit user action records an append-only `USER_SELECTED` decision and creates recipe v1:

```powershell
python scripts/production_recipes.py select-benchmark --production-spec-id <ID> --selected-by <USER> --reason <REASON>
```

User-excluded candidates can be recorded without changing or deleting their historical ProductionSpec, jobs, assets, or media:

```powershell
python scripts/production_recipes.py exclude-benchmark --production-spec-id <ID> --reviewer <USER> --reason <REASON>
```

When no `USER_SELECTED` decision exists, `AWAITING_USER_SELECTION` is the correct completed implementation state. No recipe is created automatically from scores, completeness, or prior generation history.

Export a secret-safe agent handoff:

```powershell
python scripts/production_recipes.py export --recipe-id <RECIPE_ID> --output-dir <DIRECTORY>
```

This writes `recipe_manifest.json` and `recipe_manifest.md` with identity, immutable spec snapshot, visual bible, capability reference, shots, continuity, attempts/assets, approvals, state/gate, cost/time, assembly, and QA information.

## Current limits

- Native Comfy Cloud MCP capability inventory still needs an MCP-capable Cursor or Claude Code agent.
- Actual keyframe and motion workflows/models have not been selected.
- The quality benchmark has not been generated.
- Manual gates exist but have not been exercised on real staged outputs.
- This phase generates no image, video, or audio and spends no Cloud credits.
