from __future__ import annotations

import json
from typing import Any, Protocol

import httpx

from trendforge.analysis.client import MissingAPIKeyError
from trendforge.config import Settings, get_settings
from trendforge.production.spec_parse import parse_production_spec
from trendforge.production.spec_prompt import (
    DEFAULT_ASPECT_RATIO,
    DEFAULT_DURATION_SECONDS,
    SPEC_SYSTEM_PROMPT,
    build_spec_user_prompt,
)
from trendforge.production.spec_schema import ProductionSpecDocument
from trendforge.production.spec_stub import stub_production_spec


class SpecProvider(Protocol):
    def generate_spec(
        self,
        *,
        family: dict[str, Any],
        idea: dict[str, Any],
        duration_seconds: float,
        aspect_ratio: str,
    ) -> ProductionSpecDocument: ...


class OpenRouterSpec:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def generate_spec(
        self,
        *,
        family: dict[str, Any],
        idea: dict[str, Any],
        duration_seconds: float,
        aspect_ratio: str,
    ) -> ProductionSpecDocument:
        if not self.settings.has_openrouter:
            raise MissingAPIKeyError(
                "OPENROUTER_API_KEY is not set. Add a key to .env for live production specs."
            )
        payload = {
            "model": self.settings.openrouter_model,
            "messages": [
                {"role": "system", "content": SPEC_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": build_spec_user_prompt(
                        family=family,
                        idea=idea,
                        duration_seconds=duration_seconds,
                        aspect_ratio=aspect_ratio,
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.3,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://localhost/trendforge",
            "X-Title": "TrendForge",
        }
        url = f"{self.settings.openrouter_base_url.rstrip('/')}/chat/completions"
        with httpx.Client(timeout=120.0) as client:
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
            raise ValueError("Production spec response was not valid JSON") from exc
        return parse_production_spec(
            parsed,
            family_key=str(family.get("format_family") or ""),
            primary_mechanic=str(family.get("primary_mechanic") or ""),
            duration_seconds=duration_seconds,
        )


class StubSpec:
    def generate_spec(
        self,
        *,
        family: dict[str, Any],
        idea: dict[str, Any],
        duration_seconds: float,
        aspect_ratio: str,
    ) -> ProductionSpecDocument:
        return stub_production_spec(
            family=family,
            idea=idea,
            duration_seconds=duration_seconds,
            aspect_ratio=aspect_ratio,
        )


def get_spec_provider(
    settings: Settings | None = None, force_stub: bool = False
) -> SpecProvider:
    settings = settings or get_settings()
    if force_stub or not settings.has_openrouter:
        return StubSpec()
    return OpenRouterSpec(settings)
