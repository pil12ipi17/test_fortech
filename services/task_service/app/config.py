from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "task-service"
    database_url: str = "postgresql+psycopg://task_user:task_password@task-db:5432/task_db"
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    token_issuer: str = "auth-service"

    model_config = SettingsConfigDict(env_prefix="TASK_", case_sensitive=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()

