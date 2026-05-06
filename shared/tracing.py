import logging
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import extract, inject
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode, format_span_id, format_trace_id

logger = logging.getLogger("observability.tracing")
_TRACE_PROVIDER_CONFIGURED = False
CORRELATION_ID_HEADER = "x-correlation-id"
TRACEPARENT_HEADER = "traceparent"


def configure_tracing(*, service_name: str, otlp_endpoint: str | None) -> None:
    global _TRACE_PROVIDER_CONFIGURED
    if _TRACE_PROVIDER_CONFIGURED:
        return

    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service_name}))
    if otlp_endpoint:
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint)))
        logger.info("otel_exporter_configured service=%s endpoint=%s", service_name, otlp_endpoint)
    else:
        logger.info("otel_exporter_disabled service=%s", service_name)

    trace.set_tracer_provider(provider)
    _TRACE_PROVIDER_CONFIGURED = True


def get_tracer(service_name: str):
    return trace.get_tracer(service_name)


def get_current_trace_ids() -> tuple[str | None, str | None]:
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return None, None
    return format_trace_id(span_context.trace_id), format_span_id(span_context.span_id)


def get_request_correlation_id(request: Request) -> str:
    existing = getattr(request.state, "correlation_id", None)
    if existing:
        return str(existing)
    header_value = request.headers.get(CORRELATION_ID_HEADER)
    if header_value:
        request.state.correlation_id = header_value
        return header_value
    correlation_id = str(uuid4())
    request.state.correlation_id = correlation_id
    return correlation_id


def build_trace_headers(*, correlation_id: str | None = None) -> dict[str, str]:
    headers: dict[str, str] = {}
    inject(headers)
    if correlation_id:
        headers[CORRELATION_ID_HEADER] = correlation_id
    return headers


def register_tracing_middleware(app: FastAPI, *, service_name: str, otlp_endpoint: str | None = None) -> None:
    configure_tracing(service_name=service_name, otlp_endpoint=otlp_endpoint)
    tracer = get_tracer(service_name)

    @app.middleware("http")
    async def tracing_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path == "/metrics":
            return await call_next(request)

        correlation_id = get_request_correlation_id(request)
        context = extract(dict(request.headers))
        span_name = f"HTTP {request.method}"
        with tracer.start_as_current_span(span_name, context=context, kind=SpanKind.SERVER) as span:
            span.set_attribute("service.name", service_name)
            span.set_attribute("http.request.method", request.method)
            span.set_attribute("correlation_id", correlation_id)
            try:
                response = await call_next(request)
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR))
                logger.exception(
                    "http_request_failed service=%s method=%s correlation_id=%s",
                    service_name,
                    request.method,
                    correlation_id,
                )
                raise

            route = request.scope.get("route")
            endpoint = str(getattr(route, "path", "unmatched"))
            span.update_name(f"HTTP {request.method} {endpoint}")
            span.set_attribute("http.route", endpoint)
            span.set_attribute("http.response.status_code", response.status_code)
            if response.status_code >= 500:
                span.set_status(Status(StatusCode.ERROR))

            trace_id, span_id = get_current_trace_ids()
            if trace_id:
                response.headers["X-Trace-Id"] = trace_id
            if span_id:
                response.headers["X-Span-Id"] = span_id
            response.headers["X-Correlation-Id"] = correlation_id
            logger.info(
                "http_request_finished service=%s method=%s route=%s status_code=%s trace_id=%s correlation_id=%s",
                service_name,
                request.method,
                endpoint,
                response.status_code,
                trace_id,
                correlation_id,
            )
            return response