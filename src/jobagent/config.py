from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]

load_dotenv(ROOT / ".env")


class Settings:
    def __init__(self) -> None:
        self.root = ROOT
        self.db_path = ROOT / "jobagent.db"
        self.resumes_dir = ROOT / "resumes"
        self.output_dir = ROOT / "output"
        self.preferences_path = ROOT / "config" / "preferences.yaml"

        self.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.openai_api_key = os.getenv("OPENAI_API_KEY", "")
        self.draft_provider = os.getenv("DRAFT_PROVIDER", "")
        self.openai_draft_model = os.getenv("OPENAI_DRAFT_MODEL", "gpt-4o")
        self.ollama_draft_model = os.getenv("OLLAMA_DRAFT_MODEL", "llama3.2")
        self.adzuna_app_id = os.getenv("ADZUNA_APP_ID", "")
        self.adzuna_app_key = os.getenv("ADZUNA_APP_KEY", "")
        self.ollama_rank_model = os.getenv("OLLAMA_RANK_MODEL", "llama3.2")
        self.rank_provider = os.getenv("RANK_PROVIDER", "ollama")
        self.openai_rank_model = os.getenv("OPENAI_RANK_MODEL", "gpt-4o-mini")
        self.ollama_embed_model = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")

    def load_preferences(self) -> dict:
        if not self.preferences_path.exists():
            return {}
        with open(self.preferences_path) as f:
            return yaml.safe_load(f) or {}


settings = Settings()
