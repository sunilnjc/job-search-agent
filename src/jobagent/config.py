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
        self.answers_path = ROOT / "config" / "answers.yaml"

        self.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.openai_api_key = os.getenv("OPENAI_API_KEY", "")
        self.draft_provider = os.getenv("DRAFT_PROVIDER", "")
        self.openai_draft_model = os.getenv("OPENAI_DRAFT_MODEL", "gpt-4o")
        self.ollama_draft_model = os.getenv("OLLAMA_DRAFT_MODEL", "llama3.2")
        self.adzuna_app_id = os.getenv("ADZUNA_APP_ID", "")
        self.adzuna_app_key = os.getenv("ADZUNA_APP_KEY", "")
        # Aggregator links often expire or redirect by region. Keep Adzuna opt-in for
        # discovery only; the actionable board should be direct employer postings.
        self.enable_adzuna = os.getenv("ENABLE_ADZUNA", "false").lower() == "true"
        self.enable_discovery_feeds = os.getenv("ENABLE_DISCOVERY_FEEDS", "false").lower() == "true"
        self.ollama_rank_model = os.getenv("OLLAMA_RANK_MODEL", "llama3.2")
        self.rank_provider = os.getenv("RANK_PROVIDER", "ollama")
        self.openai_rank_model = os.getenv("OPENAI_RANK_MODEL", "gpt-4o-mini")
        self.ollama_embed_model = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
        # Telegram is intentionally opt-in. The bot will not send or accept application
        # actions until both the token and the owner's private chat ID are configured.
        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.telegram_allowed_chat_id = os.getenv("TELEGRAM_ALLOWED_CHAT_ID", "")
        self.telegram_dashboard_url = os.getenv("TELEGRAM_DASHBOARD_URL", "").rstrip("/")
        self.telegram_min_score = int(os.getenv("TELEGRAM_MIN_SCORE", "8"))
        # Autopilot is deliberately narrower than the review feed.  An unknown eligibility
        # classification is useful for a human to review, but never enough to start an
        # application on its own.
        self.autopilot_min_score = int(os.getenv("AUTOPILOT_MIN_SCORE", "9"))
        self.autopilot_batch_size = int(os.getenv("AUTOPILOT_BATCH_SIZE", "5"))
        self.autopilot_include_unknown_outside_us_uk = (
            os.getenv("AUTOPILOT_INCLUDE_UNKNOWN_OUTSIDE_US_UK", "false").lower() == "true"
        )
        self.autopilot_browser_profile = ROOT / "playwright" / "profile"
        self.autopilot_headless = os.getenv("AUTOPILOT_HEADLESS", "true").lower() == "true"
        self.autopilot_require_direct_ats = os.getenv("AUTOPILOT_REQUIRE_DIRECT_ATS", "true").lower() == "true"
        # Optional SMTP account used only when the owner presses Send on an outreach draft.
        self.outreach_smtp_host = os.getenv("OUTREACH_SMTP_HOST", "")
        self.outreach_smtp_port = int(os.getenv("OUTREACH_SMTP_PORT", "465"))
        self.outreach_smtp_username = os.getenv("OUTREACH_SMTP_USERNAME", "")
        self.outreach_smtp_password = os.getenv("OUTREACH_SMTP_PASSWORD", "")
        self.outreach_from_email = os.getenv("OUTREACH_FROM_EMAIL", "")

    def load_preferences(self) -> dict:
        if not self.preferences_path.exists():
            return {}
        with open(self.preferences_path) as f:
            return yaml.safe_load(f) or {}


settings = Settings()
