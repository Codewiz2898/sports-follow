"""Traces (OpenTelemetry): a span for each API request, each worker job and each round of a live poll,
with the source requests, database queries, Redis commands and model calls made inside it, as one
trace from a fan's request through the jobs it queued (logs.carry takes the trace along).

Off unless an exporter is set, with OpenTelemetry's own settings, so any backend takes them:
OTEL_EXPORTER_OTLP_ENDPOINT (or _TRACES_ENDPOINT) and OTEL_EXPORTER_OTLP_HEADERS (its key: a secret)
send them over OTLP; OTEL_TRACES_EXPORTER=console prints them, for development. OTEL_TRACES_SAMPLER and
_SAMPLER_ARG keep a share of traces (all by default); OTEL_SERVICE_NAME and OTEL_RESOURCE_ATTRIBUTES
name the process. Off, nothing is instrumented, and the spans this app makes itself cost nothing.

Never in a span: a query string (a fan's search words, a source's parameters), a fan's id (logs.fan's
hash instead), a Redis value (the instrumentation records "GET ?").
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, nullcontext
from typing import Any

from opentelemetry import propagate, trace
from opentelemetry.context import Context
from opentelemetry.trace import SpanKind, Status, StatusCode

_tracer = trace.get_tracer("sports_follow")
on = False

# API paths with no span: the health check (every 15 s). Live streams (…/stream) have none either: one
# stays open as long as the app does.
UNTRACED = ("/api/health",)


def _exporter() -> Any:
    """The exporter OpenTelemetry's settings ask for, or None: tracing stays off."""
    if os.environ.get("OTEL_SDK_DISABLED", "").strip().lower() == "true":
        return None
    kind = os.environ.get("OTEL_TRACES_EXPORTER", "").strip().lower()
    if kind == "console":
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter

        return ConsoleSpanExporter()
    if kind in ("", "otlp") and (os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        return OTLPSpanExporter()
    return None


def setup(service: str, exporter: Any = None) -> bool:
    """Start tracing this process as `service` (unless OTEL_SERVICE_NAME names it), if an exporter is
    set: spans for its own work, and for the source requests (httpx), Redis commands and database
    queries. Returns whether tracing is on."""
    global on
    if on:
        return True
    exporter = exporter or _exporter()
    if exporter is None:
        return False
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({} if os.environ.get("OTEL_SERVICE_NAME") else {"service.name": service}))
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    HTTPXClientInstrumentor().instrument(request_hook=_outgoing)
    RedisInstrumentor().instrument()
    _watch_queries()
    on = True
    return True


def _watch_queries() -> None:
    """A span for each database query on every engine (db.py makes a new one when the worker sizes its
    pool), from SQLAlchemy's own events: the statement as sent, its values left out, and how long it
    took. OpenTelemetry's SQLAlchemy instrumentation lags SQLAlchemy's releases (it refuses 2.1), and
    this is all it would do here."""
    from opentelemetry.instrumentation.utils import is_instrumentation_enabled
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    @event.listens_for(Engine, "before_cursor_execute")
    def started(conn, cursor, statement, parameters, context, executemany) -> None:
        if context is not None and is_instrumentation_enabled():
            operation = statement.split(None, 1)[0].upper() if statement.strip() else "QUERY"
            context._span = _tracer.start_span(operation, kind=SpanKind.CLIENT, attributes={"db.system.name": conn.dialect.name, "db.query.text": statement[:2000]})

    @event.listens_for(Engine, "after_cursor_execute")
    def finished(conn, cursor, statement, parameters, context, executemany) -> None:
        if (current := getattr(context, "_span", None)) is not None:
            current.end()

    @event.listens_for(Engine, "handle_error")
    def broke(failure) -> None:
        if (current := getattr(failure.execution_context, "_span", None)) is not None:
            current.record_exception(failure.original_exception)
            current.set_status(Status(StatusCode.ERROR, type(failure.original_exception).__name__))
            current.end()


def _outgoing(span: trace.Span, request: Any) -> None:
    """A source (or gateway, or Wikidata) request's span: named for the host, its URL without the query
    string, and the rate-limit lane it was made in (adapters/budget.py). httpx's instrumentation doesn't
    catch what a hook raises, so nothing here may: a span must never fail the request it watches."""
    try:
        from .adapters import budget

        url = request.url
        method = request.method.decode() if isinstance(request.method, bytes) else str(request.method)
        span.update_name(f"{method} {url.host}")
        bare = str(url.copy_with(query=None, fragment=None))
        for key in ("url.full", "http.url"):
            if key in (getattr(span, "attributes", None) or {}):
                span.set_attribute(key, bare)
        span.set_attribute("sports_follow.lane", budget.lane())
    except Exception:  # noqa: BLE001
        for key in ("url.full", "http.url"):
            span.set_attribute(key, "")  # whatever was there may hold the query string


@contextmanager
def span(name: str, kind: SpanKind = SpanKind.INTERNAL, context: Context | None = None, **attributes: Any) -> Iterator[trace.Span]:
    """A span around a piece of this app's work, current inside the block; attributes left None are left out."""
    with _tracer.start_as_current_span(name, context=context, kind=kind, attributes={k: v for k, v in attributes.items() if v is not None}) as current:
        yield current


def request_span(method: str, path: str):
    """The span of an API request (server.RequestLog), named for its route once that's known (answered).
    An untraced path has none, nor any span for the queries and Redis commands it makes: a health check
    would be two traces of its own every 15 seconds."""
    if not path.startswith("/api/"):
        return nullcontext(None)
    if path in UNTRACED or path.endswith("/stream"):
        return shallow()
    return span(method, SpanKind.SERVER, **{"http.request.method": method, "url.path": path})


def answered(current: trace.Span | None, method: str, route: str | None, status: int, **attributes: Any) -> None:
    if current is None:
        return
    if route:
        current.update_name(f"{method} {route}")
        current.set_attribute("http.route", route)
    current.set_attribute("http.response.status_code", status)
    for key, value in attributes.items():
        if value is not None:
            current.set_attribute(key, value)
    if status >= 500:
        current.set_status(Status(StatusCode.ERROR))


def job_span(name: str, carried: Mapping[str, Any] | None, **attributes: Any):
    """A worker job's span: in the trace of the request that queued it (logs.carry), else a trace of its own."""
    return span(f"job {name}", SpanKind.CONSUMER, context=propagate.extract(dict(carried or {})), **attributes)


def failed(current: trace.Span, reason: str) -> None:
    current.set_status(Status(StatusCode.ERROR, reason))


def shallow():
    """Inside: no spans from the instrumented libraries, for a job too long to read as one trace (the
    weekly import's tens of thousands of requests); its own span still shows when and how it ran."""
    from opentelemetry.instrumentation.utils import suppress_instrumentation

    return suppress_instrumentation()


def usage(current: trace.Span, result: Any) -> None:
    """What a model call cost: the model that answered, its tokens and its price (llm_providers' result).
    Read with care: a span must never fail the call it watches."""
    if not current.is_recording():
        return
    spent = getattr(result, "usage", None)
    for key, value in (
        ("gen_ai.response.model", getattr(result, "model", None)),
        ("gen_ai.provider.name", getattr(result, "provider", None)),
        ("gen_ai.response.finish_reasons", [result.finish] if getattr(result, "finish", None) else None),
        ("gen_ai.usage.input_tokens", getattr(spent, "input_tokens", None)),
        ("gen_ai.usage.output_tokens", getattr(spent, "output_tokens", None)),
        ("gen_ai.usage.cache_read.input_tokens", getattr(spent, "cache_read_tokens", None)),
        ("sports_follow.cost_usd", getattr(spent, "cost_usd", None)),
    ):
        if value is not None:
            current.set_attribute(key, value)
