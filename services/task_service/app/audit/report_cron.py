import argparse
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .report import generate_audit_report
from ..core.config import get_settings
from ..core.db import SessionLocal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("audit-report-cron")


def generate_once() -> str:
    settings = get_settings()
    db = SessionLocal()
    try:
        output = generate_audit_report(
            db=db,
            output_path=settings.audit_report_path,
            auth_database_url=settings.auth_database_url,
        )
        logger.info("Generated audit report path=%s", output)
        return str(output)
    finally:
        db.close()


def create_scheduler() -> BlockingScheduler:
    settings = get_settings()
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        generate_once,
        trigger=IntervalTrigger(seconds=settings.audit_report_interval_seconds),
        id="audit-report-csv",
        name="Generate audit CSV report",
        replace_existing=True,
        next_run_time=datetime.now(timezone.utc),
    )
    return scheduler


def run_forever() -> None:
    settings = get_settings()
    scheduler = create_scheduler()
    logger.info("Audit report cron started interval_seconds=%s", settings.audit_report_interval_seconds)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Audit report cron stopped")
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate audit CSV report")
    parser.add_argument("--once", action="store_true", help="Generate report once and exit")
    args = parser.parse_args()
    if args.once:
        generate_once()
        return
    run_forever()


if __name__ == "__main__":
    main()
