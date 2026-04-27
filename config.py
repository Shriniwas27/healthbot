"""
App Configuration
=================
Uses pydantic-settings to load environment variables.
Place a .env file in the project root with the values below.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Google AI ─────────────────────────────────────────
    google_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    # ── MongoDB ───────────────────────────────────────────
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db_name: str = "healthbot"

    # ── Session ───────────────────────────────────────────
    session_ttl_seconds: int = 3600          # 1 hour idle timeout
    summary_sync_interval: int = 10          # sync to MongoDB every N messages

    # ── Server ────────────────────────────────────────────
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    debug: bool = True

    # ── Twilio (optional — SMS + WhatsApp reminders) ──────
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""           # SMS sender e.g. +14155552671
    twilio_whatsapp_number: str = ""        # WhatsApp sender e.g. +14155238886

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()