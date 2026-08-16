from __future__ import annotations

from typing import Any

from trendforge.production.spec_schema import ProductionSpecDocument


def spec_to_markdown(spec: ProductionSpecDocument | dict[str, Any]) -> str:
    data = spec.model_dump() if isinstance(spec, ProductionSpecDocument) else dict(spec)
    lines = [
        f"# {data.get('title') or 'Production spec'}",
        "",
        "> Production specification only. Not a generated video. Not evidence.",
        "",
        "## Format",
        f"- Family: {data.get('format_family')}",
        f"- Specific format: {data.get('specific_format')}",
        f"- Primary mechanic: {data.get('primary_mechanic')}",
        f"- Secondary: {', '.join(data.get('secondary_mechanics') or []) or 'none'}",
        f"- Hypothesis: {data.get('format_hypothesis') or '—'}",
        "",
        "## Idea",
        f"- Hook: {data.get('hook')}",
        f"- Premise: {data.get('premise')}",
        f"- Audience: {data.get('target_audience') or '—'}",
        "",
        "## Target",
        f"- Duration: {data.get('duration_seconds')}s",
        f"- Aspect: {data.get('aspect_ratio')}",
        f"- Complexity: {data.get('production_complexity')}/5",
    ]
    reasons = data.get("complexity_reasons") or []
    for reason in reasons:
        lines.append(f"  - {reason}")
    lines += ["", "## Story beats"]
    for beat in data.get("story_beats") or []:
        lines.append(
            f"- {beat.get('start_seconds')}–{beat.get('end_seconds')}s "
            f"{beat.get('purpose')}: {beat.get('action')}"
        )
    lines += ["", "## Shots"]
    for shot in data.get("shots") or []:
        lines += [
            f"### Shot {shot.get('shot_number')} ({shot.get('start_seconds')}–{shot.get('end_seconds')}s)",
            f"- Purpose: {shot.get('purpose')}",
            f"- Camera: {shot.get('camera')} / {shot.get('framing')}",
            f"- Subject: {shot.get('subject')}",
            f"- Action: {shot.get('action')}",
            f"- Environment: {shot.get('environment')}",
            f"- Continuity: {shot.get('continuity_notes')}",
            f"- Dialogue: {shot.get('dialogue') or '—'}",
            f"- Audio: {shot.get('audio')}",
            f"- Generation: {shot.get('generation_notes')}",
            "",
        ]
    lines += ["## Characters"]
    for ch in data.get("characters") or []:
        lines += [
            f"### {ch.get('name')} ({ch.get('role')})",
            f"- Appearance: {ch.get('appearance')}",
            f"- Behavior: {ch.get('behavior')}",
            f"- Visual constraints: {ch.get('visual_constraints')}",
            f"- Continuity: {ch.get('continuity_constraints')}",
            "",
        ]
    lines += [
        "## Environment / props",
        str(data.get("environment") or "—"),
        f"Props: {', '.join(data.get('props') or []) or 'none'}",
        "",
        "## Audio",
        f"- Narration: {data.get('narration') or '—'}",
        f"- Direction: {data.get('audio_direction') or '—'}",
        f"- SFX: {', '.join(data.get('sound_effects') or []) or 'none'}",
        f"- Music: {data.get('music') or 'none'}",
        f"- Priority: {data.get('audio_priority') or '—'}",
        "",
        "### Dialogue",
    ]
    for line in data.get("dialogue") or []:
        lines.append(
            f"- {line.get('start_seconds')}s {line.get('speaker')}: {line.get('text')}"
        )
    lines += ["", "## Visual", str(data.get("visual_style") or "—"), str(data.get("camera_direction") or "—"), "", "## Continuity"]
    for item in data.get("continuity_requirements") or []:
        lines.append(f"- {item}")
    lines += ["", "## Negative constraints"]
    for item in data.get("negative_constraints") or []:
        lines.append(f"- {item}")
    lines += ["", "## Required assets"]
    for asset in data.get("required_assets") or []:
        lines.append(
            f"- {asset.get('asset_type')} ({asset.get('source')}"
            f"{', mandatory' if asset.get('mandatory') else ''}): {asset.get('description')}"
        )
    lines += ["", "## ComfyUI recommendations (planning only — no workflows)"]
    for rec in data.get("comfyui_recommendations") or []:
        lines.append(
            f"- {rec.get('technique')} [{rec.get('priority')}]: {rec.get('reason')} "
            f"(inputs: {', '.join(rec.get('required_inputs') or []) or '—'})"
        )
    lines += ["", "## QA"]
    for item in data.get("qa_checklist") or []:
        flag = "required" if item.get("required") else "optional"
        lines.append(f"- [{flag}] {item.get('check')}")
    lines += [
        "",
        "## Originality / IP",
        str(data.get("originality_notes") or "—"),
        str(data.get("ip_considerations") or "—"),
        "",
    ]
    return "\n".join(lines) + "\n"
