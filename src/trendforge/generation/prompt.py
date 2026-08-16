from __future__ import annotations

from typing import Any

AGENT_PROMPT_VERSION = "production-agent-v3"


def render_video_prompt(job: dict[str, Any], spec: dict[str, Any]) -> str:
    idea = job.get("source_idea") if isinstance(job.get("source_idea"), dict) else {}
    model = job.get("cloud_model") if isinstance(job.get("cloud_model"), dict) else {}
    duration = job.get("requested_duration") or spec.get("duration_seconds") or 5
    max_dur = model.get("max_duration_seconds")
    try:
        duration = int(duration)
        if max_dur:
            duration = min(duration, int(max_dur))
    except (TypeError, ValueError):
        duration = 5
    title = spec.get("title") or idea.get("idea_title") or "short"
    hook = spec.get("hook") or idea.get("hook") or ""
    premise = spec.get("premise") or idea.get("premise") or ""
    style = job.get("style") or spec.get("visual_style") or "photoreal, handheld-phone realism, no cinematic grade"
    lines = [
        f"Vertical 9:16 video, {duration} seconds, {style}.",
        f"Concept: {title}",
        f"Hook: {hook}" if hook else "",
        f"Premise: {premise}" if premise else "",
        "Single continuous clip that still reads as this sequence:",
        f"Compress the hook and payoff into {duration} seconds. This is a test clip, not a finished Short.",
    ]
    beats = spec.get("story_beats") if isinstance(spec.get("story_beats"), list) else []
    shots = spec.get("shots") if isinstance(spec.get("shots"), list) else []
    rows = shots or beats
    for idx, row in enumerate(rows[:6]):
        if not isinstance(row, dict):
            continue
        start = row.get("start_seconds")
        try:
            if start is not None and float(start) >= float(duration):
                continue
        except (TypeError, ValueError):
            pass
        end = row.get("end_seconds")
        camera = row.get("camera") or ""
        action = row.get("action") or row.get("visual_requirement") or ""
        subject = row.get("subject") or ""
        env = row.get("environment") or ""
        dialogue = row.get("dialogue") or row.get("dialogue_or_narration") or ""
        stamp = f"{start}-{end}s" if start is not None and end is not None else f"beat {idx + 1}"
        bit = f"- {stamp}: {action}"
        if camera:
            bit += f" Camera: {camera}."
        if subject:
            bit += f" Subject: {subject}."
        if env:
            bit += f" Setting: {env}."
        if dialogue:
            bit += f' VO: "{dialogue}"'
        lines.append(bit)
    negatives = spec.get("negative_constraints") if isinstance(spec.get("negative_constraints"), list) else []
    lines.append(
        "Constraints: no on-screen captions, no brand logos, no human face on objects, "
        "no copying existing videos, night lighting if specified, photoreal."
    )
    for item in negatives[:8]:
        text = str(item).strip()
        if text:
            lines.append(f"- Avoid: {text}")
    return "\n".join(line for line in lines if line)


def render_agent_prompt(context: dict[str, Any]) -> str:
    job_id = context.get("job_id") or context.get("generation_job_id")
    model = context.get("cloud_model") if isinstance(context.get("cloud_model"), dict) else {}
    kind = context.get("output_kind") or model.get("kind") or "video"
    partner = model.get("partner_model") or ""
    template = model.get("template") or ""
    params = model.get("params") or {}
    max_dur = model.get("max_duration_seconds")
    label = model.get("label") or model.get("id") or "selected Cloud video model"
    requested = context.get("requested_duration")
    return f"""# TrendForge production-agent-v3

You are executing TrendForge Generation Job #{job_id}.

Read these files in this directory first. Do not rediscover the idea from TrendForge source.

- `job.json`
- `video_prompt.txt`  ← this is the actual Cloud prompt. Use it.
- `production_spec.json` (reference only; do not paste the whole spec into the model)
- this `AGENT.md`

Architecture:

```text
TrendForge → this Cursor Agent CLI → native comfy-cloud MCP → Comfy Cloud
```

FastAPI does not call Comfy. Do **not** use local ComfyUI, `127.0.0.1:8188`, or `comfyui-local`.

## Phase A — Read

Read the three contract files. Note job_id, idea, duration, 9:16, format/mechanic, visual requirements, and the selected Cloud model.

## Phase B — Plan

This job is a **{kind}** 9:16 short, not a still.

Selected Cloud model: **{label}**
- path: `{model.get("path") or "partner_generate"}`
- partner_generate model: `{partner or "n/a"}`
- template (if needed): `{template or "n/a"}`
- extra params: `{params}`
- requested duration: {requested}s
- provider max duration: {max_dur}s

Clamp duration to the provider maximum. Prefer 9:16 / portrait.

**Prompt:** pass the exact contents of `video_prompt.txt` as `partner_generate.prompt` (or the template text slot). Do not dump production_spec.json into the model. Do not switch models unless the selected path is unavailable.

For Seedance (`byteplus/seedance-2.0-t2v`) pass `params.ratio` = `9:16`, `params.duration` as the provider expects, plus any `params.model` listed above.
For Kling/Veo/Hailuo/FLUX 3 pass `aspect_ratio` = `9:16` and duration as that provider expects.

## Phase C — Comfy Cloud

Use the native `comfy-cloud` / `comfyui-cloud` MCP only.

If path is `partner_generate`, call `partner_generate` with the partner model above (`confirm: true` after the spend gate). Pass prompt, aspect_ratio 9:16, and duration as the provider expects.

If path is `run_template`, `get_template_schema` then `run_template` for `{template or "the listed template"}`.

Then wait for completion, retrieve output, and download into TrendForge `data/generated/{{cloud_job_id}}/`.

Never fabricate a Cloud job id. Never claim success without a real output file on disk.

## Phase D — QA

Bounded retry: at most two retries if QA fails. Check that the file exists, is video (mp4) when kind=video, is readable, and is approximately 9:16 when dimensions are known. Populate `output_duration_seconds` only when known.

## Phase E — Return

Write `result.json` in this directory, then exit. TrendForge applies the result after this process exits. Do not start local Comfy. Do not print secrets.

`result.json` fields:

status, job_id, success, execution_target (`comfy_cloud`), local_comfy_used (false),
cloud_mcp_used (true), cloud_job_id, workflow_or_template, model, output_files,
output_dimensions, output_duration_seconds, qa_result, agent (`cursor`), completed_at, error

Use status=completed only when a real Cloud output file exists on disk.
Use status=failed with a useful error when generation fails.
"""


def render_launch_prompt(job_id: int) -> str:
    rel = f"data/generation_jobs/{job_id}"
    return (
        f"You are executing TrendForge Generation Job #{job_id}.\n"
        f"Read {rel}/job.json, {rel}/video_prompt.txt, {rel}/production_spec.json, and {rel}/AGENT.md.\n"
        "Follow AGENT.md. Use video_prompt.txt as the Cloud prompt. Use native comfy-cloud MCP.\n"
        "Generate the selected 9:16 Cloud video. Do NOT use local ComfyUI.\n"
        f"When finished, write {rel}/result.json with real Cloud job/output metadata, then exit.\n"
        "Use status=completed only when a real output file exists. "
        "Use status=failed with a useful error when generation fails. Do not fabricate success."
    )
