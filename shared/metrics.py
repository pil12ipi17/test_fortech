import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import Response as FastAPIResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

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
        response: Response | None = None
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