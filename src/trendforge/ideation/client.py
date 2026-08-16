from __future__ import annotations

import json
from typing import Any, Protocol

import httpx

from trendforge.analysis.client import MissingAPIKeyError
from trendforge.config import Settings, get_settings
from trendforge.ideation.parse import parse_ideation_payload
from trendforge.ideation.prompt import (
    IDEATION_SYSTEM_PROMPT,
    VARIATION_AXES,
    build_ideation_user_prompt,
)
from trendforge.ideation.schema import FormatIdea


class IdeationProvider(Protocol):
    def generate_ideas(
        self,
        family: dict[str, Any],
        *,
        count: int,
        emphasis: str | None = None,
    ) -> list[FormatIdea]: ...


class OpenRouterIdeation:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def generate_ideas(
        self,
        family: dict[str, Any],
        *,
        count: int,
        emphasis: str | None = None,
    ) -> list[FormatIdea]:
        if not self.settings.has_openrouter:
            raise MissingAPIKeyError(
                "OPENROUTER_API_KEY is not set. Add a key to .env for live ideation."
            )
        payload = {
            "model": self.settings.openrouter_model,
            "messages": [
                {"role": "system", "content": IDEATION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": build_ideation_user_prompt(
                        family, count=count, emphasis=emphasis
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.7,
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
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError("Ideation response was not valid JSON") from exc
        return parse_ideation_payload(
            parsed,
            family_key=str(family.get("format_family") or ""),
            primary_mechanic=str(family.get("primary_mechanic") or ""),
        )


class StubIdeation:
    """Deterministic ideas for tests / offline use. Not evidence."""

    def generate_ideas(
        self,
        family: dict[str, Any],
        *,
        count: int,
        emphasis: str | None = None,
    ) -> list[FormatIdea]:
        family_key = str(family.get("format_family") or "unnamed-format")
        mechanic = str(family.get("primary_mechanic") or "ROLE_REVERSAL")
        extra = (emphasis or "none").strip() or "none"
        templates = [
            (
                "POV: you're the last parking spot in a packed lot",
                "Every car thinks you're empty until they don't.",
                "A scarce parking space narrates Black Friday desperation as drivers circle.",
                "The familiar hunt for a space plus an impossible POV creates an instant curiosity gap.",
            ),
            (
                "POV: you're a Roomba when a second Roomba arrives",
                "So that's what you've been doing while I'm asleep.",
                "Household rivalry between two robots over the same kitchen floor.",
                "Recognizable home tech plus a sudden power-dynamic shift in the same format.",
            ),
            (
                "POV: you're a smoke detector at 2am",
                "The toast is already committed.",
                "A kitchen near-miss told from the ceiling sensor that has to decide whether to scream.",
                "Everyday object + high stakes + rapid payoff inside a few seconds.",
            ),
            (
                "POV: you're a wedding ring while the owner opens Tinder",
                "Don't mind me. I'm just legally binding.",
                "An object with a relationship contract watches a social situation it cannot stop.",
                "Social situation mutation of the same impossible perspective mechanic.",
            ),
            (
                "POV: you're the hold music on a three-hour insurance call",
                "Verse twelve is where people start bargaining.",
                "A looping melody becomes the only character in a bureaucratic waiting room.",
                "Profession/institution setting with a curiosity gap about when the human returns.",
            ),
            (
                "POV: you're the extra chair at a dinner for two",
                "They keep glancing at me like I might confess.",
                "An unused seat implies a missing person without naming them.",
                "Relationship stakes plus visual contradiction in a familiar dining setup.",
            ),
            (
                "POV: you're a medieval drawbridge during rush hour",
                "Your commute has a moat now.",
                "Historical setting grafted onto modern traffic impatience.",
                "Time-period axis keeps the format, changes the world around the same hook.",
            ),
            (
                "POV: you're a barcode that refuses to scan",
                "I contain multitudes. Also, an error.",
                "A grocery object delays the entire line by becoming self-aware at the worst moment.",
                "Object + social situation + escalating embarrassment payoff.",
            ),
            (
                "POV: you're the family group chat after a typo",
                "Twelve unread messages and one missing comma.",
                "A digital space watches a small mistake become a cultural incident.",
                "Cultural/context mutation with rapid social payoff.",
            ),
            (
                "POV: you're a goldfish during a surprise move",
                "The castle just vanished. That's new.",
                "Species/scale shift: a tiny observer of a huge household change.",
                "Same immersion mechanic, different body and stakes.",
            ),
            (
                "POV: you're the office microwave at 12:01",
                "Someone brought fish. History will remember this.",
                "Workplace ritual plus forbidden food creates immediate communal conflict.",
                "Profession axis with a recognizable smell-based contradiction.",
            ),
            (
                "POV: you're a snow globe in July",
                "They keep shaking me like weather is optional.",
                "Scale and season mismatch inside a souvenir that cannot leave the shelf.",
                "Environment/time mutation of the trapped-observer format.",
            ),
            (
                "POV: you're the last slice everyone agreed not to take",
                "We had a treaty.",
                "A social contract collapses over pizza in real time.",
                "Stakes + relationship dynamic with an obvious visual punchline.",
            ),
            (
                "POV: you're a GPS that has given up",
                "Recalculating is a personality now.",
                "Technology that should be certain becomes improvisational in an unknown city.",
                "Tech object + environment + delayed payoff when the human ignores advice.",
            ),
            (
                "POV: you're a library book that has been overdue since 1998",
                "They still think this is a three-week story.",
                "Time-scale mismatch between a quiet object and a decades-long absence.",
                "Scale/time axis with a confession-style hook.",
            ),
            (
                "POV: you're the backup dancer nobody hired",
                "The chorus still needs hips.",
                "A social/performance situation where the mechanic is being in the wrong role on purpose.",
                "Profession + power dynamic while keeping the impossible-role structure.",
            ),
            (
                "POV: you're a vending machine that only likes exact change",
                "Your dollar is philosophically insufficient.",
                "Object vs human negotiation in a hallway at 3am.",
                "Stakes are tiny, the contradiction is immediate, the format stays intact.",
            ),
            (
                "POV: you're the moon during a city blackout",
                "Suddenly everyone remembers I exist.",
                "Scale shift: a distant body becomes the only lighting designer left.",
                "Environment/scale mutation of the observer format.",
            ),
            (
                "POV: you're a toddler's imaginary friend on the first day of school",
                "They packed a sandwich. They did not pack me.",
                "Relationship mechanic: abandonment plus a public social situation.",
                "Emotional payoff without copying any source plot.",
            ),
            (
                "POV: you're a caption that appears one second too early",
                "I spoiled the punchline. Occupational hazard.",
                "The format itself glitches: text arrives before the visual payoff.",
                "Meta variation of curiosity gap / rapid payoff while staying original.",
            ),
        ]
        ideas: list[FormatIdea] = []
        for i in range(count):
            title, hook, premise, why = templates[i % len(templates)]
            axis = VARIATION_AXES[i % len(VARIATION_AXES)]
            if extra != "none":
                premise = f"{premise} Emphasis: {extra}."
            ideas.append(
                FormatIdea(
                    idea_title=title if i < len(templates) else f"{title} ({i + 1})",
                    hook=hook,
                    premise=premise,
                    format_family=family_key,
                    primary_mechanic=mechanic,
                    variation_axis=axis,
                    why_it_could_work=why,
                    production_notes="Original script and original visuals only. Do not clone source clips.",
                    ai_leverage=5,
                    production_complexity=2,
                    variation_potential=4,
                    originality_risk="LOW — original premise, do not copy source dialogue",
                )
            )
        return parse_ideation_payload(
            {"ideas": [idea.model_dump() for idea in ideas]},
            family_key=family_key,
            primary_mechanic=mechanic,
        )


def get_ideation_provider(
    settings: Settings | None = None, force_stub: bool = False
) -> IdeationProvider:
    settings = settings or get_settings()
    if force_stub or not settings.has_openrouter:
        return StubIdeation()
    return OpenRouterIdeation(settings)
