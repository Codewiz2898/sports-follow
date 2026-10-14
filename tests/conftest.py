"""What every test shares."""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from sports_follow import tracing
from sports_follow.adapters import budget


@pytest.fixture(autouse=True)
def source_budgets(monkeypatch):
    """Source budgets (adapters/budget.py) count in memory, fresh for each test: no test reaches Redis, or
    spends what another counted."""
    store = budget.MemoryStore()
    monkeypatch.setattr(budget, "STORE", store)
    return store


@pytest.fixture
def spans(monkeypatch):
    """The spans this app makes itself (tracing.span and the rest), recorded in memory: no exporter, and
    nothing set process-wide, so the libraries stay uninstrumented for every other test."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(tracing, "_tracer", provider.get_tracer("test"))
    return exporter
