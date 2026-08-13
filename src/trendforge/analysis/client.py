from __future__ import annotations

import json
from typing import Protocol

import httpx

from trendforge.analysis.prompt import ANALYSIS_SYSTEM_PROMPT, build_analysis_user_prompt
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
        parsed = json.loads(content)
        return FormatAnalysis.model_validate(parsed)


class StubAnalyzer:
    """Deterministic analyzer for tests / offline demos."""

    def analyze_candidate(self, candidate: dict) -> FormatAnalysis:
        title = (candidate.get("title") or "Unknown clip").strip()
        return FormatAnalysis(
            what_happens=f"A short-form clip titled '{title}' delivers a strong attention hook.",
            first_second_hook="Immediate visual surprise or incongruity in the first second.",
            why_stop_scrolling="Pattern interrupt between expected subject and unexpected performer.",
            attention_mechanic="Unexpected character + preserved performance structure",
            format_name="Unexpected Character + Performance",
            format_key="unexpected-character-performance",
            format_category="performance-transfer",
            format_description=(
                "Take a recognizable performance structure and replace the expected performer "
                "with an incongruous character while preserving timing and delivery."
            ),
            hook_pattern="Incongruous character appears mid-performance within 1s",
            why_it_works="Cognitive dissonance + familiarity of the performance scaffold.",
            variables=["character", "performance type", "setting", "costume", "audio"],
            estimated_variation_count=40,
            novelty_signal=82.0,
            replicability_signal=90.0,
            saturation_signal=35.0,
            trend_velocity_signal=88.0,
            cross_platform_signal=70.0,
            hook_strength_signal=86.0,
            production_complexity_signal=40.0,
            comfy_feasibility_signal=85.0,
            ip_risk_signal=45.0,
            ip_notes="Avoid copying protected routines/scripts; invent original performances.",
            recommended_production_method="character_replacement",
            production_notes="Use motion/performance transfer with original script and character.",
            estimated_generation_cost_usd=2.5,
            original_variations=[
                {
                    "concept": "A medieval knight delivers an original product pitch monologue",
                    "character": "knight",
                    "scenario": "castle courtyard",
                    "hook": "Armor clanks as sales cadence begins",
                    "production_method": "character_replacement",
                    "notes": "Original script only",
                }
            ],
        )


def get_analyzer(settings: Settings | None = None, force_stub: bool = False) -> AnalysisProvider:
    settings = settings or get_settings()
    if force_stub or not settings.has_openrouter:
        return StubAnalyzer()
    return OpenRouterAnalyzer(settings)
