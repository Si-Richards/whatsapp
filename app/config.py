from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "VoiceHost WhatsApp POC"
    database_url: str = "sqlite:///./data/whatsapp.db"

    meta_api_version: str = "v23.0"
    meta_app_id: str = ""
    meta_app_secret: str = ""
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_business_account_id: str = ""
    whatsapp_verify_token: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
