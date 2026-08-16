from __future__ import annotations

from typing import Any

IDEATION_PROMPT_VERSION = "format-ideation-v1"

VARIATION_AXES = (
    "character",
    "object",
    "environment",
    "social situation",
    "time period",
    "profession",
    "relationship",
    "power dynamic",
    "species",
    "scale",
    "stakes",
    "cultural context",
)

ALLOWED_COUNTS = (5, 10, 20)
DEFAULT_COUNT = 10

EMPHASIS_PRESETS = (
    "",
    "more absurd",
    "more realistic",
    "more comedic",
    "more cinematic",
    "lower production complexity",
    "higher novelty",
    "North American audience",
)

IDEATION_SYSTEM_PROMPT = """You are a short-form format ideation assistant for TrendForge.

Your job is to generate ORIGINAL concept mutations from a discovered format and attention mechanic.

This is IDEATION, not evidence. Do not judge whether the source videos are viral. Do not invent performance metrics.

Preserve the attention mechanic. Change the creative execution.

Optimize for: recognizable format + novel premise + immediate hook + believable short-form payoff.

Good: mutate the mechanic into a new familiar situation with an immediate visual/story hook.
Bad: slap a random noun onto a title ("POV: you're a banana") unless the premise actually creates a story and payoff.

Rules:
1. Keep the same format family and primary mechanic unless a mutation truly requires a listed secondary mechanic.
2. Vary the creative DIMENSION (variation_axis). Do not produce ten near-identical premises.
3. Useful axes: character, object, environment, social situation, time period, profession, relationship, power dynamic, species, scale, stakes, cultural context.
4. Never reproduce another creator's script, dialogue, exact video, or recommend downloading/reposting source material.
5. why_it_could_work must explain the attention mechanic, not say "this is funny and engaging."
6. Rate ai_leverage, production_complexity, variation_potential as integers 1-5. These are brainstorming estimates only.
7. originality_risk is a short note (LOW / MEDIUM / HIGH plus a phrase).
8. Respond with JSON only: {"ideas": [ ... ]} matching the schema. No overall_score, BUILD/WATCH/REJECT, or discovery scores.
"""


def build_ideation_user_prompt(
    family: dict[str, Any],
    *,
    count: int,
    emphasis: str | None = None,
) -> str:
    examples = family.get("variation_examples") or []
    if isinstance(examples, list):
        example_text = "; ".join(str(x) for x in examples[:8]) or "none"
    else:
        example_text = str(examples)
    secondary = family.get("secondary_mechanics") or []
    if isinstance(secondary, list):
        secondary_text = ", ".join(str(x) for x in secondary) or "none"
    else:
        secondary_text = str(secondary)
    extra = (emphasis or "").strip()
    lines = [
        f"Generate exactly {count} original short-form concepts.",
        "Preserve the mechanic; mutate the execution. Deduplicate premises.",
        "",
        f"Format family: {family.get('format_family')}",
        f"Specific format / name: {family.get('format_name') or family.get('format_key')}",
        f"Primary mechanic: {family.get('primary_mechanic')}",
        f"Secondary mechanics: {secondary_text}",
        f"Format hypothesis: {family.get('format_hypothesis') or 'none'}",
        f"Audience signal: {family.get('audience_signal') or 'none'}",
        f"Existing variation examples (do not copy): {example_text}",
        f"AI leverage (family hint): {family.get('ai_leverage')}",
        f"Production complexity (family hint): {family.get('production_complexity')}",
        f"Requested variation axes: {', '.join(VARIATION_AXES)}",
    ]
    if extra:
        lines.append(f"Optional emphasis: {extra}")
    lines.extend(
        [
            "",
            "Each idea JSON object fields:",
            "idea_title, hook, premise, format_family, primary_mechanic, variation_axis,",
            "why_it_could_work, production_notes, ai_leverage, production_complexity,",
            "variation_potential, originality_risk",
            "",
            'Return JSON: {"ideas": [...]}',
            "Do NOT include overall_score, status, or discovery metrics.",
        ]
    )
    return "\n".join(lines)
