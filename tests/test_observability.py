from opentelemetry.sdk.resources import SERVICE_NAME

from shared import tracing


def test_tracing_keeps_distinct_service_providers_in_one_process():
    first_service = "test-observability-first-service"
    second_service = "test-observability-second-service"

    tracing.configure_tracing(service_name=first_service, otlp_endpoint=None)
    tracing.configure_tracing(service_name=second_service, otlp_endpoint=None)

    first_provider = tracing._TRACER_PROVIDERS[first_service]
    second_provider = tracing._TRACER_PROVIDERS[second_service]

    assert first_provider is not second_provider
    assert first_provider.resource.attributes[SERVICE_NAME] == first_service
    assert second_provider.resource.attributes[SERVICE_NAME] == second_service

    first_tracer = tracing.get_tracer(first_service)
    second_tracer = tracing.get_tracer(second_service)

    with first_tracer.start_as_current_span("first-service-span"):
        first_trace_id, first_span_id = tracing.get_current_trace_ids()

    with second_tracer.start_as_current_span("second-service-span"):
        second_trace_id, second_span_id = tracing.get_current_trace_ids()

    assert first_trace_id
    assert first_span_id
    assert second_trace_id
    assert second_span_id