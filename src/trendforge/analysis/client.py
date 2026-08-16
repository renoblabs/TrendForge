from __future__ import annotations

import json
from typing import Protocol

import httpx

from trendforge.analysis.parse import parse_format_analysis
from trendforge.analysis.prompt import ANALYSIS_SYSTEM_PROMPT, PROMPT_VERSION, build_analysis_user_prompt
from trendforge.analysis.schema import FormatAnalysis
from trendforge.config import Settings, get_settings


class AnalysisProvider(Protocol):
    def analyze_candidate(self, candidate: dict) -> FormatAnalysis: ...


class MissingAPIKeyError(RuntimeError):
    pass


class OpenRouterAnalyzer:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def analyze_candidate(self, candidate: dict) -> FormatAnalysis:
        if not self.settings.has_openrouter:
            raise MissingAPIKeyError(
                "OPENROUTER_API_KEY is not set. Seed data still works; "
                "add a key to .env for live analysis."
            )

        payload = {
            "model": self.settings.openrouter_model,
            "messages": [
                {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
                {"role": "user", "content": build_analysis_user_prompt(candidate)},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://localhost/trendforge",
            "X-Title": "TrendForge",
        }
        url = f"{self.settings.openrouter_base_url.rstrip('/')}/chat/completions"
        with httpx.Client(timeout=90.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part) for part in content
            )
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError("LLM response was not valid JSON") from exc
        return parse_format_analysis(parsed)


class StubAnalyzer:
    """Deterministic analyzer for tests / offline demos."""

    def analyze_candidate(self, candidate: dict) -> FormatAnalysis:
        title = (candidate.get("title") or "Unknown clip").strip()
        return parse_format_analysis(
            {
            "what_happens": f"A short-form clip titled '{title}' delivers a strong attention hook.",
            "surface_content": f"Clip titled '{title}' shows an incongruous character in a familiar situation.",
            "first_second_hook": "Immediate visual surprise or incongruity in the first second.",
            "why_stop_scrolling": "Pattern interrupt between expected subject and unexpected performer.",
            "attention_mechanic": "Familiar situation + impossible role reversal + immediate visual contradiction",
            "primary_mechanic": "ROLE_REVERSAL",
            "secondary_mechanics": ["ABSURD_REALITY", "VISUAL_CONTRADICTION"],
            "format_name": "Unexpected Character + Performance",
            "format_key": "unexpected-character-performance",
            "format_family": "unexpected-character-performance",
            "format_category": "performance-transfer",
            "format_description": (
                "Take a recognizable performance structure and replace the expected performer "
                "with an incongruous character while preserving timing and delivery."
            ),
            "premise": "A familiar real-world interaction with inverted roles.",
            "character_device": "Incongruous replacement character",
            "visual_pattern": "Immediate visual contradiction in frame one",
            "story_structure": "Setup of familiar scene, then role inversion",
            "pacing": "Punchline visible within the first second",
            "emotional_trigger": "Amused disbelief",
            "novelty_mechanism": "Impossible character in an everyday scaffold",
            "hook_pattern": "Incongruous character appears mid-performance within 1s",
            "why_it_works": "Cognitive dissonance + familiarity of the performance scaffold.",
            "reason_it_might_work": (
                "Uses an immediately recognizable everyday premise, introduces an impossible "
                "character or role reversal, and makes the contradiction visible immediately."
            ),
            "format_hypothesis": (
                "Take a familiar real-world interaction, introduce an impossible character "
                "or role reversal, and make the contradiction visually obvious immediately."
            ),
            "audience_signal": "Viewers stop for the mismatch between expected actor and actual performer.",
            "variables": ["character", "performance type", "setting", "costume", "audio"],
            "estimated_variation_count": 40,
            "variation_examples": [
                "mosquito → shark",
                "teacher → toddler",
                "grandmother → professional wrestler",
                "delivery driver → medieval knight",
            ],
            "novelty_signal": 82.0,
            "replicability_signal": 90.0,
            "saturation_signal": 35.0,
            "trend_velocity_signal": 88.0,
            "cross_platform_signal": 70.0,
            "hook_strength_signal": 86.0,
            "production_complexity_signal": 40.0,
            "comfy_feasibility_signal": 85.0,
            "ip_risk_signal": 45.0,
            "ai_leverage": 5,
            "variation_density_rating": 5,
            "production_complexity_rating": 2,
            "ip_dependency": "LOW",
            "originality_risk": "Low if performances and scripts are original.",
            "analysis_confidence": "medium",
            "ip_notes": "Avoid copying protected routines/scripts; invent original performances.",
            "recommended_production_method": "character_replacement",
            "production_notes": "Use motion/performance transfer with original script and character.",
            "estimated_generation_cost_usd": 2.5,
            "original_variations": [
                {
                    "concept": "A medieval knight delivers an original product pitch monologue",
                    "character": "knight",
                    "scenario": "castle courtyard",
                    "hook": "Armor clanks as sales cadence begins",
                    "production_method": "character_replacement",
                    "notes": "Original script only",
                }
            ],
            }
        )


def get_analyzer(settings: Settings | None = None, force_stub: bool = False) -> AnalysisProvider:
    settings = settings or get_settings()
    if force_stub or not settings.has_openrouter:
        return StubAnalyzer()
    return OpenRouterAnalyzer(settings)
