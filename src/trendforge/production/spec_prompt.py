from __future__ import annotations

from typing import Any

SPEC_PROMPT_VERSION = "production-spec-v1"
DEFAULT_DURATION_SECONDS = 20
DEFAULT_ASPECT_RATIO = "9:16"

SPEC_SYSTEM_PROMPT = """You are a short-form production specification writer for TrendForge.

Translate a selected format family and brainstorm idea into a production-ready specification for a future content factory.

This is NOT video generation. Do not invent ComfyUI node graphs. Do not publish. Do not copy source videos.

Preserve the attention mechanic. Do not change the concept merely to make generation easier.

Favor: short-form clarity, visual storytelling, simple production, repeatability, continuity, original execution.
Avoid: unnecessary complexity, generic cinematic fluff, copyrighted replication, source-video copying, unexplained VFX, requirements that cannot reasonably be generated.

Rules:
1. Keep format_family, specific_format, primary_mechanic, secondary_mechanics, and format_hypothesis from the research context.
2. Target duration_seconds is binding. All story_beats and shots must fit inside it. Do not write a 45s script for a 20s spec.
3. Story beats need start_seconds, end_seconds, purpose, action, dialogue_or_narration, visual_requirement, audio_requirement.
4. Shots need shot_number, start_seconds, end_seconds, duration_seconds, purpose, camera, framing, subject, action, environment, continuity_notes, dialogue, audio, generation_notes.
5. Shot times must be sequential and non-overlapping (end of one may equal start of next).
6. Characters need name, role, appearance, behavior, visual_constraints, continuity_constraints. Identity must persist across shots.
7. continuity_requirements must name concrete elements (colors, lighting, camera orientation, time of day, object positions) — not "keep it consistent."
8. negative_constraints must be specific to this concept, not a generic negative prompt dump.
9. comfyui_recommendations are high-level techniques (image-to-video, character consistency, reference image conditioning, lip sync, video-to-video). Include priority, reason, required_inputs. No invented node names.
10. required_assets use source: generate | user_provided | stock | library | future_factory.
11. Dialogue and narration must be original. Do not write copyrighted songs or source-video lines.
12. production_complexity is 1–5 with complexity_reasons.
13. qa_checklist is structured checks the generated Short must pass.
14. Respond with one JSON object matching the schema. No overall_score, BUILD/WATCH, or discovery metrics.
"""


def build_spec_user_prompt(
    *,
    family: dict[str, Any],
    idea: dict[str, Any],
    duration_seconds: float,
    aspect_ratio: str,
) -> str:
    secondary = family.get("secondary_mechanics") or []
    if isinstance(secondary, list):
        secondary_text = ", ".join(str(x) for x in secondary) or "none"
    else:
        secondary_text = str(secondary)
    return "\n".join(
        [
            "Create a production specification for this selected idea.",
            "Preserve the mechanic. Do not replace the concept.",
            "",
            f"Format family: {family.get('format_family')}",
            f"Specific format: {family.get('format_name') or family.get('format_key')}",
            f"Primary mechanic: {family.get('primary_mechanic')}",
            f"Secondary mechanics: {secondary_text}",
            f"Format hypothesis: {family.get('format_hypothesis') or 'none'}",
            "",
            f"Idea title: {idea.get('idea_title')}",
            f"Hook: {idea.get('hook')}",
            f"Premise: {idea.get('premise')}",
            f"Why it could work: {idea.get('why_it_could_work')}",
            f"Variation axis: {idea.get('variation_axis')}",
            f"Idea production notes: {idea.get('production_notes') or 'none'}",
            "",
            f"Target duration_seconds: {duration_seconds}",
            f"Aspect ratio: {aspect_ratio}",
            "",
            "Return JSON with:",
            "title, format_family, specific_format, primary_mechanic, secondary_mechanics,",
            "format_hypothesis, hook, premise, target_audience, duration_seconds, aspect_ratio,",
            "story_beats[], shots[], characters[], environment, props[],",
            "dialogue[], narration, audio_direction, sound_effects[], music, audio_priority,",
            "visual_style, camera_direction, continuity_requirements[],",
            "generation_requirements[], negative_constraints[],",
            "production_complexity, complexity_reasons[], required_assets[], qa_checklist[],",
            "originality_notes, ip_considerations, comfyui_recommendations[].",
        ]
    )
