from dataclasses import dataclass

from .config import Settings


@dataclass(frozen=True)
class RabbitMQConfig:
    url: str
    tasks_exchange: str


def build_rabbitmq_config(settings: Settings) -> RabbitMQConfig:
    return RabbitMQConfig(
        url=settings.rabbitmq_url,
        tasks_exchange=settings.rabbitmq_tasks_exchange,
    )
