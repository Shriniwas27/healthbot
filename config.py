"""
App Configuration
=================
Uses pydantic-settings to load environment variables.
Place a .env file in the project root with the values below.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    google_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db_name: str = "healthbot"

    session_ttl_seconds: int = 3600         
    summary_sync_interval: int = 10          

    app_host: str = "0.0.0.0"
    app_port: int = 8000
    debug: bool = True

    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""           
    twilio_whatsapp_number: str = ""        

    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = ""
    sns_sender_id: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()