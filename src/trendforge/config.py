from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "openai/gpt-4o-mini"

    youtube_api_key: str = ""
    youtube_api_base_url: str = "https://www.googleapis.com/youtube/v3"

    trendforge_db_path: str = "data/trendforge.db"
    trendforge_host: str = "127.0.0.1"
    trendforge_port: int = 8000

    postiz_api_key: str = ""
    postiz_base_url: str = "https://api.postiz.com/public/v1"

    apify_api_token: str = ""
    apify_api_base_url: str = "https://api.apify.com/v2"

    @property
    def db_path(self) -> Path:
        path = Path(self.trendforge_db_path)
        if not path.is_absolute():
            path = ROOT / path
        return path

    @property
    def has_openrouter(self) -> bool:
        return bool(self.openrouter_api_key.strip())

    @property
    def has_youtube(self) -> bool:
        return bool(self.youtube_api_key.strip())

    @property
    def has_apify(self) -> bool:
        return bool(self.apify_api_token.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_scoring_config() -> dict:
    path = CONFIG_DIR / "scoring_weights.json"
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_discovery_config() -> dict:
    path = CONFIG_DIR / "discovery.json"
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_discovery_weights() -> dict:
    path = CONFIG_DIR / "discovery_weights.json"
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_family_weights() -> dict:
    path = CONFIG_DIR / "family_weights.json"
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_generation_config() -> dict:
    path = CONFIG_DIR / "generation.json"
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_data_sources_config() -> dict:
    path = CONFIG_DIR / "data_sources.json"
    with path.open(encoding="utf-8") as f:
        return json.load(f)
