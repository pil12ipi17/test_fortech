import argparse
import asyncio
import logging

from services.task_service.app.core.config import get_settings
from services.task_service.app.core.db import SessionLocal
from .dispatcher import dispatch_pending_notifications

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("notification-cron")


def dispatch_once() -> dict[str, int]:
    settings = get_settings()
    db = SessionLocal()
    try:
        result = dispatch_pending_notifications(
            db=db,
            limit=settings.notification_dispatch_batch_size,
        )
        db.commit()
        logger.info("Notification cron dispatched result=%s", result)
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def run_forever() -> None:
    settings = get_settings()
    logger.info("Notification cron started interval_seconds=%s", settings.notification_dispatch_interval_seconds)
    while True:
        dispatch_once()
        await asyncio.sleep(settings.notification_dispatch_interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Dispatch pending notification deliveries")
    parser.add_argument("--once", action="store_true", help="Dispatch notifications once and exit")
    args = parser.parse_args()
    if args.once:
        dispatch_once()
        return
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
