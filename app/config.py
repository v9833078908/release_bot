from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    release_bot_token: str
    channel_id: str
    admin_chat_id: int
    github_token: str
    github_repo: str
    prod_version_url: str = "https://tools.herocraft.com/api/v1/version"
    openrouter_api_key: str
    llm_model: str = "google/gemini-2.5-flash"
    deploy_poll_seconds: int = 180
    schedule_tz: str = "Europe/Moscow"
    min_features_to_publish: int = 1
    initial_marker_sha: str
    db_path: str = "data/release_bot.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
