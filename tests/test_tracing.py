"""Traces (tracing.py, BHA-193): off without an exporter; one trace from a request through the jobs it
queues, each job and poll round a span; a source request's span has no query string; log lines carry
their trace. Nothing here needs a collector."""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest
from opentelemetry.trace import SpanKind, StatusCode

from sports_follow import logs, tracing, worker

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = ("OTEL_SDK_DISABLED", "OTEL_TRACES_EXPORTER", "OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "OTEL_SERVICE_NAME")


def _alone(fn):
    """fn in a context of its own: logs.begin and the current span end with it."""
    return contextvars.copy_context().run(fn)


def test_off_without_an_exporter(monkeypatch):
    for key in SETTINGS:
        monkeypatch.delenv(key, raising=False)
    assert tracing._exporter() is None and tracing.setup("sports-follow-test") is False and tracing.on is False

    def queued():
        logs.begin(rid="ab12cd34ef56ab78")
        with tracing.span("POST /api/follows", SpanKind.SERVER) as span:
            return span.is_recording(), logs.carry()

    assert _alone(queued) == (False, {"rid": "ab12cd34ef56ab78"})  # spans cost nothing, and there's no trace to carry


def test_opentelemetrys_own_settings_choose_the_exporter(monkeypatch):
    for key in SETTINGS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://otlp.example/otlp")
    assert type(tracing._exporter()).__name__ == "OTLPSpanExporter"
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "console")
    assert type(tracing._exporter()).__name__ == "ConsoleSpanExporter"
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "none")
    assert tracing._exporter() is None
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "otlp")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    assert tracing._exporter() is None


def test_a_job_continues_the_trace_of_the_request_that_queued_it(spans, caplog):
    def request():
        logs.begin(rid="ab12cd34ef56ab78")
        with tracing.span("POST /api/follows", SpanKind.SERVER):
            return logs.carry()

    carried = _alone(request)
    assert set(carried) == {"rid", "traceparent"}

    async def build_card(ctx, player_id: int) -> None:
        logging.getLogger("sports_follow.pipeline").info("build %s: bound", player_id)
        with tracing.span("poll round", event_id=5):
            pass

    caplog.set_level(logging.INFO, "sports_follow")
    asyncio.run(worker._logged(build_card)({"job_id": "0f8e2a6c", "job_try": 1}, 7, trace=carried))
    server, poll_round, job = spans.get_finished_spans()
    assert (job.name, job.kind, job.parent.span_id) == ("job build_card", SpanKind.CONSUMER, server.context.span_id)
    assert job.context.trace_id == server.context.trace_id and poll_round.parent.span_id == job.context.span_id
    assert job.attributes["player_id"] == 7 and job.attributes["job.id"] == "0f8e2a6c"
    [built] = [r for r in caplog.records if r.getMessage() == "build 7: bound"]
    assert built.context["rid"] == "ab12cd34ef56ab78" and built.context["trace_id"] == format(server.context.trace_id, "032x")


def test_a_failed_or_cancelled_job_is_an_error_span_of_its_own(spans):
    async def poll_lane(ctx, system: str) -> None:
        raise ValueError("no such round")

    async def poll_event(ctx, event_id: int) -> None:
        raise asyncio.CancelledError

    with pytest.raises(ValueError):
        asyncio.run(worker._logged(poll_lane)({}, "lichess_chess"))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker._logged(poll_event)({}, 5))
    failed, cancelled = spans.get_finished_spans()
    assert failed.parent is None and failed.status.status_code == StatusCode.ERROR and failed.events[0].name == "exception"
    assert cancelled.status.status_code == StatusCode.ERROR and cancelled.status.description == "cancelled"


def test_health_checks_streams_and_the_web_apps_files_have_no_span():
    from opentelemetry.instrumentation.utils import is_instrumentation_enabled

    for path in ("/api/health", "/api/me/stream", "/api/players/1/stream", "/assets/index.js", "/player/1"):
        with tracing.request_span("GET", path) as span:
            assert span is None
            # Nor do a health check's or a stream's queries and Redis commands make spans of their own.
            assert is_instrumentation_enabled() is path.startswith(("/assets", "/player"))


def test_a_model_calls_span_says_what_it_cost(spans):
    class Usage:
        input_tokens, output_tokens, cache_read_tokens, cost_usd = 1200, 300, 1000, 0.0042

    class Result:
        model, provider, finish, usage = "moonshotai/kimi-k2.6", "openrouter", "tool_use", Usage()

    with tracing.span("model call") as call:
        tracing.usage(call, Result())
        tracing.usage(call, object())  # whatever comes back, the span never fails the call
    [span] = spans.get_finished_spans()
    assert span.attributes["gen_ai.usage.input_tokens"] == 1200 and span.attributes["sports_follow.cost_usd"] == 0.0042
    assert span.attributes["gen_ai.response.model"] == "moonshotai/kimi-k2.6" and span.attributes["gen_ai.response.finish_reasons"] == ("tool_use",)


# What setup turns on, in a process of its own (it instruments httpx, Redis and every SQLAlchemy engine
# process-wide): a source request and a query inside a job are the job's children, the request without
# its query string, in its lane; the query without its values; a job that isn't deep has only its own span.
LIBRARIES = """
import asyncio, http.server, json, threading
import httpx
from sqlalchemy import create_engine, text
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sports_follow import tracing, worker
from sports_follow.adapters import budget

exporter = InMemorySpanExporter()
assert tracing.setup("sports-follow-test", exporter=exporter)

class Source(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")
    def log_message(self, *args):
        pass

source = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Source)
threading.Thread(target=source.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{source.server_port}/apis/search/v2?query=virat+kohli&limit=10"

engine = create_engine("sqlite://")

def search():
    with budget.in_lane("fan"):
        httpx.get(url)
    with engine.connect() as db:
        db.execute(text("SELECT :name"), {"name": "virat kohli"})

async def build_card(ctx, player_id):
    await asyncio.to_thread(search)

async def import_registry(ctx):
    await asyncio.to_thread(search)

asyncio.run(worker._logged(build_card)({}, 7))
asyncio.run(worker._logged(import_registry, deep=False)({}))
trace.get_tracer_provider().force_flush()
print(json.dumps([{"name": s.name, "kind": s.kind.name, "id": s.context.span_id, "parent": s.parent.span_id if s.parent else None,
                   "attributes": dict(s.attributes), "service": s.resource.attributes["service.name"]} for s in exporter.get_finished_spans()]))
"""


def test_a_source_request_is_a_span_of_its_job_without_its_query_string():
    env = {k: v for k, v in os.environ.items() if not k.startswith("OTEL_")}
    out = subprocess.run([sys.executable, "-c", LIBRARIES], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    request, query, job, shallow = json.loads(out.stdout.splitlines()[-1])
    assert (request["name"], request["kind"], request["parent"]) == ("GET 127.0.0.1", "CLIENT", job["id"])
    assert (query["name"], query["parent"], query["attributes"]["db.query.text"]) == ("SELECT", job["id"], "SELECT ?")  # never the values
    urls = [v for k, v in request["attributes"].items() if k in ("url.full", "http.url")]
    assert urls and all(u.endswith("/apis/search/v2") for u in urls)  # a fan's search words stay out
    assert request["attributes"]["sports_follow.lane"] == "fan"
    assert (job["name"], shallow["name"]) == ("job build_card", "job import_registry")  # and no request span under the import
    assert {s["service"] for s in (request, query, job, shallow)} == {"sports-follow-test"}
