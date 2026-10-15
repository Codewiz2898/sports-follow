"""What every test shares."""

from __future__ import annotations

import threading

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from sports_follow import bus, controls, shutdown, tracing
from sports_follow.adapters import budget


@pytest.fixture(autouse=True)
def source_budgets(monkeypatch):
    """Source budgets (adapters/budget.py) count in memory, fresh for each test: no test reaches Redis, or
    spends what another counted."""
    store = budget.MemoryStore()
    monkeypatch.setattr(budget, "STORE", store)
    return store


@pytest.fixture(autouse=True)
def admin_controls():
    """What the admin panel turned off or changed (controls.py) is kept in memory, empty for each test: no
    test reads the database for it, or sees another's changes."""
    store = controls.MemoryStore()
    controls.reset(store)
    yield store
    controls.reset(controls.MemoryStore())


@pytest.fixture(autouse=True)
def stopping(monkeypatch):
    """The worker's stop flag (shutdown.py) and the locks it frees (bus.release_held), fresh for each
    test: a test that sets the flag, as the worker's shutdown hook does, never stops another test's
    loops, and a lock one test leaves taken isn't another's to free."""
    event = threading.Event()
    monkeypatch.setattr(shutdown, "stopping", event)
    monkeypatch.setattr(bus, "_held", {})
    return event


@pytest.fixture
def spans(monkeypatch):
    """The spans this app makes itself (tracing.span and the rest), recorded in memory: no exporter, and
    nothing set process-wide, so the libraries stay uninstrumented for every other test."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(tracing, "_tracer", provider.get_tracer("test"))
    return exporter
