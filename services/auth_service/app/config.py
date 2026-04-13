from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "auth-service"
    database_url: str = "postgresql+psycopg://auth_user:auth_password@auth-db:5432/auth_db"
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7
    token_issuer: str = "auth-service"

    model_config = SettingsConfigDict(env_prefix="AUTH_", case_sensitive=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
