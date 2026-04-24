import csv
from collections import defaultdict
from pathlib import Path

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from ..tasks.models import OutboxEvent, WorkerError

REPORT_COLUMNS = ["date", "metric_name", "metric_value", "errors_count", "notes"]
TASK_METRICS = {
    "task.created": "task.created",
    "task.status_changed": "task.status_changed",
    "task.deleted": "task.deleted",
}


def _date_key(value) -> str:
    return str(value)


def _load_task_metrics(db: Session) -> dict[str, dict[str, int]]:
    grouped: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    rows = db.execute(
        select(func.date(OutboxEvent.created_at), OutboxEvent.event_type, func.count())
        .where(OutboxEvent.event_type.in_(TASK_METRICS.keys()))
        .group_by(func.date(OutboxEvent.created_at), OutboxEvent.event_type)
    ).all()
    for date_value, event_type, count in rows:
        grouped[_date_key(date_value)][TASK_METRICS[event_type]] = int(count)
    return grouped


def _load_error_counts(db: Session) -> dict[str, int]:
    rows = db.execute(
        select(func.date(WorkerError.created_at), func.count()).group_by(func.date(WorkerError.created_at))
    ).all()
    return {_date_key(date_value): int(count) for date_value, count in rows}


def _load_login_metrics(auth_database_url: str | None) -> dict[str, int]:
    if not auth_database_url:
        return {}

    engine = create_engine(auth_database_url, future=True)
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "select date(created_at) as login_date, count(*) as login_count "
                "from audit_log where action = 'auth.login' group by date(created_at)"
            )
        ).all()
    return {_date_key(row.login_date): int(row.login_count) for row in rows}


def generate_audit_report(
    *,
    db: Session,
    output_path: str,
    auth_database_url: str | None = None,
) -> Path:
    task_metrics = _load_task_metrics(db)
    error_counts = _load_error_counts(db)
    login_metrics = _load_login_metrics(auth_database_url)
    dates = sorted(set(task_metrics) | set(error_counts) | set(login_metrics))

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, str | int]] = []
    for date_value in dates:
        errors_count = error_counts.get(date_value, 0)
        metrics_for_date = task_metrics.get(date_value, {})
        for metric_name in TASK_METRICS.values():
            rows.append(
                {
                    "date": date_value,
                    "metric_name": metric_name,
                    "metric_value": metrics_for_date.get(metric_name, 0),
                    "errors_count": errors_count,
                    "notes": "source=outbox_events",
                }
            )
        rows.append(
            {
                "date": date_value,
                "metric_name": "auth.login",
                "metric_value": login_metrics.get(date_value, 0),
                "errors_count": errors_count,
                "notes": "source=auth.audit_log" if auth_database_url else "auth source not configured",
            }
        )
        if errors_count:
            rows.append(
                {
                    "date": date_value,
                    "metric_name": "worker.errors",
                    "metric_value": errors_count,
                    "errors_count": errors_count,
                    "notes": "source=worker_errors",
                }
            )

    with output.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=REPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    return output
