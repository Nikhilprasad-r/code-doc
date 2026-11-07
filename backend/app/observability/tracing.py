"""OpenTelemetry tracing, sent over OTLP/HTTP to any compatible backend (Jaeger in the compose override).

Off unless OTEL_EXPORTER_OTLP_ENDPOINT (or OTEL_EXPORTER_OTLP_TRACES_ENDPOINT) is set: until then the OTel API
is a no-op and the spans below cost almost nothing. OTEL_SERVICE_NAME overrides the service name.

Spans use the OTel GenAI attribute names where they exist (`gen_ai.*`) and `codedoc.*` otherwise:

    POST /chat                         (FastAPI instrumentation)
      answer                           source, repo, mode, status, cost
        chat claude-haiku-5-5          request/response model, tokens, finish reason, time to first output
          anthropic.messages.create    the Anthropic SDK's own span for the API call
        execute_tool search_code       tool name, arguments, error, output size
          retrieval.keyword / retrieval.vector / retrieval.rerank
    index_repo                         (worker) with index.fetch / parse / write / embed / graph

Prompt and answer text never go into our spans. The Anthropic SDK (2.6+) traces its API calls whenever a
tracer provider is installed. By default it records metadata only: model, tokens, request ID, and the
Anthropic organization and workspace IDs. It also sends a W3C `traceparent` header to the API.
ANTHROPIC_OPEN_TELEMETRY=false turns that off; ANTHROPIC_OPEN_TELEMETRY_TRACES_CONTENT_MODE=content would add
message content, so leave it unset.
"""

import os

from opentelemetry import trace

tracer = trace.get_tracer("codedoc")
_provider = None


def tracing_configured() -> bool:
    return bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"))


def setup_tracing(service_name: str) -> bool:
    """Install the OTLP exporter as the global tracer provider. Returns whether tracing is on. Idempotent."""
    global _provider
    if _provider is not None:
        return True
    if not tracing_configured():
        return False
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    service = os.environ.get("OTEL_SERVICE_NAME", service_name)
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    _provider = provider
    return True


def instrument_app(app) -> None:
    """Request spans for the FastAPI app. Per-message send/receive spans are skipped: a streamed answer
    would otherwise produce one span per chunk."""
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app, excluded_urls="health", exclude_spans=["receive", "send"])


def tracing_enabled() -> bool:
    return _provider is not None


def shutdown_tracing() -> None:
    """Flush buffered spans (call on shutdown)."""
    if _provider is not None:
        _provider.shutdown()


def current_trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None
