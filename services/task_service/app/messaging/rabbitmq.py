from dataclasses import dataclass

from ..core.config import Settings


@dataclass(frozen=True)
class RabbitMQConfig:
    url: str
    tasks_exchange: str
    tasks_dead_letter_exchange: str
    tasks_exchange_type: str = "topic"


def build_rabbitmq_config(settings: Settings) -> RabbitMQConfig:
    return RabbitMQConfig(
        url=settings.rabbitmq_url,
        tasks_exchange=settings.rabbitmq_tasks_exchange,
        tasks_dead_letter_exchange=f"{settings.rabbitmq_tasks_exchange}.dlx",
    )
