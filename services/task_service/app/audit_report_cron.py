import argparse
import asyncio
import logging

from .audit_report import generate_audit_report
from .config import get_settings
from .db import SessionLocal

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


async def run_forever() -> None:
    settings = get_settings()
    logger.info("Audit report cron started interval_seconds=%s", settings.audit_report_interval_seconds)
    while True:
        generate_once()
        await asyncio.sleep(settings.audit_report_interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate audit CSV report")
    parser.add_argument("--once", action="store_true", help="Generate report once and exit")
    args = parser.parse_args()
    if args.once:
        generate_once()
        return
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()