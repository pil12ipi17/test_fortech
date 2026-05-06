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
    redis_url: str | None = None
    redis_socket_timeout_seconds: float = 0.5
    login_rate_limit_requests: int = 5
    login_rate_limit_window_seconds: int = 60
    otel_exporter_otlp_endpoint: str | None = None

    model_config = SettingsConfigDict(env_prefix="AUTH_", case_sensitive=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
