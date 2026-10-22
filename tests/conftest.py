"""What every test shares."""

from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

# A run on a developer's machine is the run CI makes: no Redis (CI has none), the test database or none, and
# none of ~/.config/sports-follow's keys. Else a test that goes further than its fakes reaches the main stack's
# Redis, the dev database, a paid source or a real phone's notifications. Set before sports_follow is imported:
# its modules read these once.
_NONE = str(Path(__file__).with_name("no-such-key"))
os.environ["REDIS_URL"] = "redis://127.0.0.1:9/0"
os.environ["DATABASE_URL"] = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL") or "postgresql+psycopg://sports:sports@127.0.0.1:9/none"
for _key in ("SPORTMONKS_TOKEN", "PANDASCORE_TOKEN", "PUBG_TOKEN", "LIQUIPEDIA_TOKEN", "LICHESS_TOKEN", "YOUTUBE_KEY", "FCM_KEY", "RESEND_KEY", "GOOGLE_CLIENT_SECRET", "LINEAR_KEY", "CLIPS_KEY"):
    os.environ[f"SPORTS_FOLLOW_{_key}_FILE"] = _NONE
    os.environ.pop(f"SPORTS_FOLLOW_{_key}", None)
os.environ["SPORTS_FOLLOW_VAPID_KEY"] = _NONE
os.environ["SPORTS_FOLLOW_ADMIN_PASSWORD_FILE"] = _NONE
# The fan cookies' key is made on first use: in a folder of the run's own, never in ~/.config.
os.environ["SPORTS_FOLLOW_COOKIE_SECRET_FILE"] = str(Path(tempfile.mkdtemp(prefix="sports-follow-tests-")) / "cookie-secret")
os.environ.pop("SPORTS_FOLLOW_COOKIE_SECRET", None)
os.environ["LLM_GATEWAY_URL"] = "http://127.0.0.1:9"
os.environ["LLM_PROVIDERS_KEYS_FILE"] = _NONE

import pytest  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402

from sports_follow import bus, controls, registry, shutdown, tracing, wiki  # noqa: E402
from sports_follow.adapters import budget, cache  # noqa: E402


@pytest.fixture(autouse=True)
def source_budgets(monkeypatch):
    """Source budgets (adapters/budget.py) count in memory, fresh for each test: no test reaches Redis, or
    spends what another counted."""
    store = budget.MemoryStore()
    monkeypatch.setattr(budget, "STORE", store)
    return store


@pytest.fixture(autouse=True)
def source_cache(monkeypatch):
    """What the sources answered (adapters/cache.py) is kept in memory, empty for each test, and so is each
    process's copy: no test reaches Redis, or is answered from what another test read."""
    store = cache.MemoryStore()
    monkeypatch.setattr(cache, "STORE", store)
    cache.forget()
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


@pytest.fixture(autouse=True)
def no_wikidata(monkeypatch):
    """No test reaches the Wikidata query service (registry.sparql): a test that needs its answers gives
    them, so a run never loads the service or writes what it says into a real database."""

    def sparql(query: str) -> list[dict[str, str]]:
        raise RuntimeError("tests don't query Wikidata")

    monkeypatch.setattr(registry, "sparql", sparql)

    def get(*args, **kwargs):
        raise RuntimeError("tests don't ask Wikidata or Wikipedia for profiles")

    # Nor the Wikidata API or Wikipedia for a player's profile (wiki.py): test_wiki.py gives their answers.
    monkeypatch.setattr(wiki._client, "get", get)


@pytest.fixture
def spans(monkeypatch):
    """The spans this app makes itself (tracing.span and the rest), recorded in memory: no exporter, and
    nothing set process-wide, so the libraries stay uninstrumented for every other test."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(tracing, "_tracer", provider.get_tracer("test"))
    return exporter
