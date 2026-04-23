from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "task-service"
    database_url: str = "postgresql+psycopg://task_user:task_password@task-db:5432/task_db"
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    token_issuer: str = "auth-service"
    rabbitmq_url: str = "amqp://task_user:task_password@rabbitmq:5672/task-system"
    rabbitmq_tasks_exchange: str = "tasks.events"
    outbox_publish_batch_size: int = 50
    outbox_publish_poll_interval_seconds: float = 2.0
    worker_max_retry_attempts: int = 3
    worker_retry_backoff_base_seconds: float = 1.0
    worker_retry_backoff_max_seconds: float = 10.0

    model_config = SettingsConfigDict(env_prefix="TASK_", case_sensitive=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
