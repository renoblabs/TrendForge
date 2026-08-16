from __future__ import annotations

from typing import Any

from trendforge.production.spec_parse import parse_production_spec
from trendforge.production.spec_prompt import DEFAULT_ASPECT_RATIO, DEFAULT_DURATION_SECONDS
from trendforge.production.spec_schema import ProductionSpecDocument


def stub_production_spec(
    *,
    family: dict[str, Any],
    idea: dict[str, Any],
    duration_seconds: float = DEFAULT_DURATION_SECONDS,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
) -> ProductionSpecDocument:
    duration = float(duration_seconds or DEFAULT_DURATION_SECONDS)
    title = str(idea.get("idea_title") or "Untitled spec")
    hook = str(idea.get("hook") or title)
    premise = str(idea.get("premise") or hook)
    family_key = str(family.get("format_family") or "unnamed-format")
    mechanic = str(family.get("primary_mechanic") or "IMPOSSIBLE_POV")
    specific = str(family.get("format_name") or family.get("format_key") or family_key)
    hypothesis = str(family.get("format_hypothesis") or "")
    secondary = family.get("secondary_mechanics") or []
    if not isinstance(secondary, list):
        secondary = [str(secondary)]

    t = [0.0, duration * 0.10, duration * 0.30, duration * 0.55, duration * 0.80, duration]
    t = [round(x, 2) for x in t]
    beats = [
        {
            "start_seconds": t[0],
            "end_seconds": t[1],
            "purpose": "Immediate POV setup",
            "action": "Establish the familiar space from the idea's impossible viewpoint.",
            "dialogue_or_narration": hook,
            "visual_requirement": "9:16 POV; environment readable in frame one.",
            "audio_requirement": "Room tone plus one identifying sound.",
        },
        {
            "start_seconds": t[1],
            "end_seconds": t[2],
            "purpose": "Inciting change",
            "action": "The second presence or conflict from the premise enters frame.",
            "dialogue_or_narration": "",
            "visual_requirement": "New subject enters without cutting identity of the POV body.",
            "audio_requirement": "Entry SFX distinct from room tone.",
        },
        {
            "start_seconds": t[2],
            "end_seconds": t[3],
            "purpose": "Recognition",
            "action": "POV understands the relationship implied by the premise.",
            "dialogue_or_narration": "Original one-line reaction. Not source dialogue.",
            "visual_requirement": "Both subjects readable; no extra crowd.",
            "audio_requirement": "Reaction line intelligible over SFX.",
        },
        {
            "start_seconds": t[3],
            "end_seconds": t[4],
            "purpose": "Escalation",
            "action": "Stakes rise inside the same environment.",
            "dialogue_or_narration": "",
            "visual_requirement": "Same lighting and camera height as setup.",
            "audio_requirement": "Rhythmic SFX, still no licensed music bed required.",
        },
        {
            "start_seconds": t[4],
            "end_seconds": t[5],
            "purpose": "Payoff",
            "action": "The curiosity gap closes with a visual punchline.",
            "dialogue_or_narration": "Optional original button line.",
            "visual_requirement": "Payoff readable without captions.",
            "audio_requirement": "Button SFX; narration remains intelligible if used.",
        },
    ]
    shots = []
    purposes = ["POV establishment", "Inciting entrance", "Recognition", "Escalation", "Payoff"]
    for i in range(5):
        shots.append(
            {
                "shot_number": i + 1,
                "start_seconds": t[i],
                "end_seconds": t[i + 1],
                "duration_seconds": round(t[i + 1] - t[i], 2),
                "purpose": purposes[i],
                "camera": "locked low POV, 9:16",
                "framing": "wide-enough floor/subject to read geography",
                "subject": "POV Body" if i == 0 else "POV Body and Rival",
                "action": beats[i]["action"],
                "environment": "Single indoor location implied by the premise",
                "continuity_notes": "Same floor texture, lighting, and POV height as shot 1.",
                "dialogue": beats[i]["dialogue_or_narration"],
                "audio": beats[i]["audio_requirement"],
                "generation_notes": "Image-to-video from a locked POV still; do not teleport camera.",
            }
        )
    payload = {
        "title": title,
        "format_family": family_key,
        "specific_format": specific,
        "primary_mechanic": mechanic,
        "secondary_mechanics": secondary,
        "format_hypothesis": hypothesis,
        "hook": hook,
        "premise": premise,
        "target_audience": "English-language short-form scrollers who stop for POV mismatch",
        "duration_seconds": duration,
        "aspect_ratio": aspect_ratio,
        "story_beats": beats,
        "shots": shots,
        "characters": [
            {
                "name": "POV Body",
                "role": "viewpoint / protagonist object or creature",
                "appearance": "Distinct silhouette and one accent color that never changes.",
                "behavior": "Observes first, then reacts; does not become a human face.",
                "visual_constraints": "Keep the same body, color, and scuffs in every shot.",
                "continuity_constraints": "Camera stays at this body's eyeline; no third-person cutaways.",
            },
            {
                "name": "Rival",
                "role": "inciting second subject",
                "appearance": "Clearly different color/marking from POV Body.",
                "behavior": "Enters, occupies space, creates the premise conflict.",
                "visual_constraints": "Never match POV Body's accent color.",
                "continuity_constraints": "Same size relationship to POV Body after entrance.",
            },
        ],
        "environment": "One recognizable interior tied to the premise; daylight or practical lamps, not a studio void.",
        "props": ["inciting object from the premise"],
        "dialogue": [
            {
                "speaker": "POV Body",
                "start_seconds": t[2],
                "text": "Original reaction line derived from the hook — not a source quote.",
            }
        ],
        "narration": hook,
        "audio_direction": "Dry room, close SFX, optional original one-liner. No copyrighted songs.",
        "sound_effects": ["room tone", "entrance clunk", "payoff hit"],
        "music": "None required. If used, original bed under -18 LUFS relative to voice.",
        "audio_priority": "Hook narration and the recognition line must stay intelligible.",
        "visual_style": "Photoreal phone-camera aesthetic, slightly wide lens, 9:16, no cinematic letterbox.",
        "camera_direction": "Locked POV height; tiny handheld drift only; no teleport cuts.",
        "continuity_requirements": [
            "POV Body accent color and wear marks identical in all shots",
            "Rival remains the contrasting color after entrance",
            "Same interior lighting and time of day",
            "Camera eyeline stays at POV Body height",
            "Relative sizes of POV Body and Rival do not jump",
        ],
        "generation_requirements": [
            "character consistency for two subjects",
            "image-to-video from POV stills",
            "no extra characters",
        ],
        "negative_constraints": [
            "no extra characters or crowd",
            "no on-screen text or watermarks unless the idea requires a UI",
            "no swapping POV Body and Rival colors",
            "no sudden camera teleportation",
            "no malformed geometry on either subject",
            "no copyrighted character likeness",
        ],
        "production_complexity": 2,
        "complexity_reasons": [
            "single environment",
            "two recurring characters",
            "limited camera movement",
            "no crowd simulation",
        ],
        "required_assets": [
            {
                "asset_type": "character_reference",
                "description": "Still of POV Body, three-quarter and front",
                "mandatory": True,
                "source": "generate",
                "notes": "Lock color and wear before video.",
            },
            {
                "asset_type": "character_reference",
                "description": "Still of Rival with contrasting markings",
                "mandatory": True,
                "source": "generate",
                "notes": "Must not match POV Body palette.",
            },
            {
                "asset_type": "environment_reference",
                "description": "Wide still of the interior from POV height",
                "mandatory": True,
                "source": "generate",
                "notes": "Reuse as image-to-video start frame.",
            },
            {
                "asset_type": "voice",
                "description": "Original POV line, dry, 1–2 seconds",
                "mandatory": False,
                "source": "future_factory",
                "notes": "Do not generate in this phase.",
            },
            {
                "asset_type": "sound_effect",
                "description": "Entrance and payoff hits",
                "mandatory": True,
                "source": "stock",
                "notes": "Non-musical, original or licensed SFX only.",
            },
        ],
        "qa_checklist": [
            {"check": "hook understandable in first 2 seconds", "required": True},
            {"check": "POV is visually obvious", "required": True},
            {"check": "character identity consistent", "required": True},
            {"check": "story understandable without context", "required": True},
            {"check": "payoff occurs within target duration", "required": True},
            {"check": "dialogue intelligible", "required": True},
            {"check": "no visual continuity breaks", "required": True},
            {"check": "no unintended text/watermarks", "required": True},
            {"check": "original execution", "required": True},
            {"check": "9:16 framing", "required": True},
        ],
        "originality_notes": (
            "Original premise and original lines. Do not recreate a specific source Short "
            "or reuse another creator's dialogue."
        ),
        "ip_considerations": "Avoid branded logos and copyrighted character likenesses.",
        "comfyui_recommendations": [
            {
                "technique": "character consistency",
                "priority": "HIGH",
                "reason": "POV Body and Rival must persist across five shots",
                "required_inputs": ["character reference image"],
            },
            {
                "technique": "image-to-video",
                "priority": "HIGH",
                "reason": "Locked POV stills animate without camera teleport",
                "required_inputs": ["environment reference", "start frame"],
            },
            {
                "technique": "reference image conditioning",
                "priority": "MEDIUM",
                "reason": "Keep interior materials stable",
                "required_inputs": ["environment reference"],
            },
        ],
    }
    return parse_production_spec(
        payload,
        family_key=family_key,
        primary_mechanic=mechanic,
        duration_seconds=duration,
    )
