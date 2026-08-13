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

    trendforge_db_path: str = "data/trendforge.db"
    trendforge_host: str = "127.0.0.1"
    trendforge_port: int = 8000

    postiz_api_key: str = ""
    postiz_base_url: str = "https://api.postiz.com/public/v1"

    @property
    def db_path(self) -> Path:
        path = Path(self.trendforge_db_path)
        if not path.is_absolute():
            path = ROOT / path
        return path

    @property
    def has_openrouter(self) -> bool:
        return bool(self.openrouter_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_scoring_config() -> dict:
    path = CONFIG_DIR / "scoring_weights.json"
    with path.open(encoding="utf-8") as f:
        return json.load(f)
