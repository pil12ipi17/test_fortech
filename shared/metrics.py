import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import Response as FastAPIResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest, start_http_server

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests handled by the service.",
    ("service", "method", "endpoint", "status_code"),
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ("service", "method", "endpoint", "status_code"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

OUTBOX_EVENTS_PUBLISHED_TOTAL = Counter(
    "outbox_events_published_total",
    "Total outbox publish attempts by event type and result.",
    ("service", "event_type", "result"),
)

OUTBOX_PUBLISH_DURATION_SECONDS = Histogram(
    "outbox_publish_duration_seconds",
    "Outbox event publish duration in seconds.",
    ("service", "event_type", "result"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

WORKER_EVENTS_TOTAL = Counter(
    "worker_events_total",
    "Total worker event handling outcomes.",
    ("consumer", "event_type", "result"),
)

WORKER_ERRORS_TOTAL = Counter(
    "worker_errors_total",
    "Total worker event handling errors.",
    ("consumer", "event_type"),
)

WORKER_EVENT_PROCESSING_DURATION_SECONDS = Histogram(
    "worker_event_processing_duration_seconds",
    "Worker event processing duration in seconds.",
    ("consumer", "event_type", "result"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

WORKER_ACTIVE_HANDLERS = Gauge(
    "worker_active_handlers",
    "Number of active worker message handlers.",
    ("consumer",),
)

EVENT_PIPELINE_DLQ_MESSAGES = Gauge(
    "event_pipeline_dlq_messages",
    "Observed messages moved to DLQ by workers.",
    ("consumer", "event_type"),
)

_METRICS_SERVER_STARTED_PORTS: set[int] = set()


def start_metrics_http_server(port: int | None) -> None:
    if port is None or port <= 0 or port in _METRICS_SERVER_STARTED_PORTS:
        return
    start_http_server(port)
    _METRICS_SERVER_STARTED_PORTS.add(port)


def _endpoint_label(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if path:
        return str(path)
    if request.url.path == "/metrics":
        return "/metrics"
    return "unmatched"


def register_prometheus_metrics(app: FastAPI, *, service_name: str) -> None:
    @app.middleware("http")
    async def prometheus_metrics_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path == "/metrics":
            return await call_next(request)

        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration_seconds = time.perf_counter() - start
            endpoint = _endpoint_label(request)
            labels = {
                "service": service_name,
                "method": request.method,
                "endpoint": endpoint,
                "status_code": str(status_code),
            }
            HTTP_REQUESTS_TOTAL.labels(**labels).inc()
            HTTP_REQUEST_DURATION_SECONDS.labels(**labels).observe(duration_seconds)

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> FastAPIResponse:
        return FastAPIResponse(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)