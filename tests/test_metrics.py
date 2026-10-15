"""Metrics (metrics.py, BHA-194): what's counted where it happens, with labels that never name a fan or a
player; the worker's minute readings (live games, the queue, source allowances); nothing needs a collector."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from sports_follow import bus, metrics, worker
from sports_follow.adapters import budget

from .test_api import api  # noqa: F401  (the fixture)


@pytest.fixture
def measured(monkeypatch):
    """Metrics recorded in memory: a meter of the test's own, nothing set process-wide."""
    reader = InMemoryMetricReader()
    monkeypatch.setattr(metrics, "_meter", MeterProvider(metric_readers=[reader]).get_meter("test"))
    return reader


def points(reader: InMemoryMetricReader) -> dict[str, list]:
    """Each metric's data points, by name."""
    data = reader.get_metrics_data()
    found: dict[str, list] = {}
    for rm in data.resource_metrics if data else []:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                found.setdefault(m.name, []).extend(m.data.data_points)
    return found


def test_what_happens_is_counted_by_route_job_service_and_kind(measured):
    metrics.request("GET", "/api/players/{player_id}/card", 200, 0.012)
    metrics.request("POST", None, 403, 0.001)  # turned away before routing
    metrics.job("poll_event", "ok", 600.0, 0.4)
    metrics.pushes([("fcm.googleapis.com", None), ("fcm.googleapis.com", None), ("web.push.apple.com", 410), ("web.push.apple.com", 500)], 3.0)
    metrics.moment("goal")
    metrics.card("build", "failed")
    got = points(measured)
    requests = {(p.attributes["http.route"], p.attributes["http.response.status_code"]): p.count for p in got["http.server.request.duration"]}
    assert requests == {("/api/players/{player_id}/card", 200): 1, ("unmatched", 403): 1}
    assert [(p.attributes, p.count) for p in got["sports_follow.job.duration"]] == [({"job_name": "poll_event", "outcome": "ok"}, 1)]
    assert [p.sum for p in got["sports_follow.job.wait"]] == [0.4]
    pushes = {(p.attributes["push_service"], p.attributes["result"]): p.value for p in got["sports_follow.pushes"]}
    assert pushes == {("fcm.googleapis.com", "delivered"): 2, ("web.push.apple.com", "gone"): 1, ("web.push.apple.com", "failed"): 1}
    assert [p.sum for p in got["sports_follow.push.wait"]] == [3.0]
    assert [(p.attributes, p.value) for p in got["sports_follow.moments"]] == [({"kind": "goal"}, 1)]
    assert [(p.attributes, p.value) for p in got["sports_follow.cards.updates"]] == [({"step": "build", "result": "failed"}, 1)]


def test_an_api_request_is_counted_by_its_route_and_a_health_check_isnt(api, measured):  # noqa: F811
    assert api.call("GET", "/api/sports?q=private+words").status_code == 200
    api.call("GET", "/api/health")
    [point] = points(measured)["http.server.request.duration"]
    assert dict(point.attributes) == {"http.request.method": "GET", "http.route": "/api/sports", "http.response.status_code": 200}


def test_a_quiet_job_is_counted_though_its_line_isnt_logged(measured, caplog):
    async def tick(ctx) -> None:
        pass

    caplog.set_level(logging.INFO, "sports_follow")
    asyncio.run(worker._logged(tick, quiet=True)({}))
    assert not [r for r in caplog.records if r.name == "sports_follow.worker"]
    assert [p.attributes["job_name"] for p in points(measured)["sports_follow.job.duration"]] == ["tick"]


class Queue:
    """arq's queue as Redis holds it: a sorted set of job ids by when each is due (ms), and running jobs' keys."""

    def __init__(self, due: dict[str, float], running: list[str]):
        self.due, self.running = due, running

    def zcount(self, key, low, high):
        return sum(1 for at in self.due.values() if at <= high)

    def zcard(self, key):
        return len(self.due)

    def zrangebyscore(self, key, low, high, start=0, num=None, withscores=False):
        ready = sorted((at, job) for job, at in self.due.items() if at <= high)
        return [(job, at) for at, job in ready][start:start + num]

    def scan_iter(self, match=None, count=None):
        return iter(f"arq:in-progress:{job}" for job in self.running)


def test_the_queue_reading_says_how_many_wait_and_for_how_long(monkeypatch):
    now = 1_000_000.0
    monkeypatch.setattr(bus, "sync_redis", lambda: Queue({"a": (now - 90) * 1000, "b": (now - 5) * 1000, "later": (now + 60) * 1000}, ["c"]))
    reading = metrics.queue(now)
    assert sorted((v, a["state"]) for v, a in reading["jobs"]) == [(1, "deferred"), (1, "running"), (2, "ready")]
    assert reading["oldest_wait"] == [(90.0, {})]


def test_a_sources_reading_has_its_spend_by_bucket_its_allowance_and_what_it_says_is_left(monkeypatch):
    plan = budget.Budget("plan", windows=(budget.Window(3600, 2000),))
    monkeypatch.setattr(metrics, "_running_budgets", lambda: {"plan": plan})
    for bucket in ("Fixture", "Fixture", "Player"):
        budget.take(plan, bucket, lane_name="live")
    budget.observe(plan, "Fixture", 1500, 600)
    budget.pause(plan)
    reading = metrics.sources()
    used = {(a["bucket"], a["window"]): v for v, a in reading["used"]}
    assert used == {("", "hour"): 0, ("Fixture", "hour"): 2, ("Player", "hour"): 1}
    assert reading["allowance"] == [(2000, {"source": "plan", "window": "hour"})]
    assert reading["left"] == [(1500, {"source": "plan", "bucket": "Fixture"})]
    assert reading["paused"] == [(1, {"source": "plan"})]
    assert sorted((v, a["lane"], a["result"]) for v, a in reading["hourly"]) == [(3, "live", "sent")]


def test_the_worker_reports_its_readings_and_one_that_fails_leaves_the_rest(measured, monkeypatch, caplog):
    def broken():
        raise RuntimeError("database down")

    monkeypatch.setattr(metrics, "_watching", False)
    monkeypatch.setattr(metrics, "READINGS", [
        ("sports_follow.jobs", "{job}", "jobs", lambda: {"jobs": [(3, {"state": "ready"})]}, "jobs"),
        ("sports_follow.live.games", "{game}", "games", broken, "games"),
    ])
    metrics.watch()
    got = points(measured)
    assert [(p.value, dict(p.attributes)) for p in got["sports_follow.jobs"]] == [(3, {"state": "ready"})]
    assert "sports_follow.live.games" not in got and "database down" in caplog.text


def test_no_label_names_a_fan_or_a_player():
    import inspect

    source = inspect.getsource(metrics)
    for name in ("fan", "player_id", "fan_id", "event_id", "rid", "job"):  # "job": Prometheus's own label
        assert f'"{name}":' not in source


def test_a_poll_notes_when_it_happened(monkeypatch):
    from sports_follow import structured

    calls: list[tuple] = []
    monkeypatch.setattr(bus, "sync_redis", lambda: SimpleNamespace(set=lambda key, value, ex=None, get=False: calls.append((key, ex)) or None))
    structured._polled(5, 10)
    assert [key for key, _ in calls] == ["polled_at:5", "polled:5"]


# What setup turns on, in a process of its own (the meter provider is process-wide): httpx's metric for
# each source request, by server.address, and this app's own, under a service.instance.id of the process's.
WITH_METRICS = """
import asyncio, http.server, io, json, threading
import httpx
from opentelemetry import metrics
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter
from sports_follow import tracing, worker

out = io.StringIO()
assert tracing.setup("sports-follow-test", metric_exporter=ConsoleMetricExporter(out=out)) is False  # no traces asked for

class Source(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
    def log_message(self, *args):
        pass

source = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Source)
threading.Thread(target=source.serve_forever, daemon=True).start()

async def refresh_structured(ctx, player_id):
    await asyncio.to_thread(httpx.get, f"http://127.0.0.1:{source.server_port}/fixtures?player=7")

asyncio.run(worker._logged(refresh_structured)({}, 7))
metrics.get_meter_provider().force_flush()
seen = {}
for rm in json.loads(out.getvalue())["resource_metrics"]:  # one flush, one export
    instance = rm["resource"]["attributes"].get("service.instance.id")
    for sm in rm["scope_metrics"]:
        for m in sm["metrics"]:
            seen[m["name"]] = [dp["attributes"] for dp in m["data"]["data_points"]]
print(json.dumps({"instance": instance, "metrics": seen}))
"""


def test_setup_measures_source_requests_and_jobs_under_the_processs_own_instance():
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    env = {k: v for k, v in os.environ.items() if not k.startswith("OTEL_")}
    out = subprocess.run([sys.executable, "-c", WITH_METRICS], cwd=Path(__file__).resolve().parent.parent, env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout.splitlines()[-1])
    assert len(got["instance"]) == 36  # a uuid: the API's two processes never write the same series
    [client] = got["metrics"]["http.client.request.duration"]
    assert client["server.address"] == "127.0.0.1" and "url.full" not in client  # by source, never the URL
    assert got["metrics"]["sports_follow.job.duration"] == [{"job_name": "refresh_structured", "outcome": "ok"}]


def test_a_game_is_behind_after_three_of_its_rounds_and_never_under_three_minutes():
    assert metrics.behind_after({"cadence": 10}) == 180  # in play every 10 s, but armed and quiet games once a minute
    assert metrics.behind_after({"cadence": 120}) == 360  # PUBG
    assert metrics.behind_after({"cadence": 300}) == 900  # Liquipedia: polled every 5 minutes on purpose
    assert metrics.behind_after({}) == 180


def test_the_worker_reports_unsent_moments():
    assert ("sports_follow.moments.unsent", "unsent") in {(name, part) for name, _, _, _, part in metrics.READINGS}


def test_every_alert_rule_reads_a_metric_the_app_sends_and_names_its_labels_right():
    """The app's alert rules (deploy/grafana/app-rules.json, applied by apply-alerts.sh): each complete, each
    uid once across both groups, each reading only metrics with the job they come from."""
    import json
    import re
    from pathlib import Path

    grafana = Path(__file__).resolve().parent.parent / "deploy" / "grafana"
    app = json.loads((grafana / "app-rules.json").read_text())
    server = json.loads((grafana / "rules.json").read_text())
    uids = [r["uid"] for r in app + server]
    assert len(uids) == len(set(uids))
    sent = {name.replace(".", "_") for name, *_ in metrics.READINGS} | {
        "http_server_request_duration_seconds", "http_client_request_duration_seconds", "sports_follow_pushes", "sports_follow_cards_updates"}
    for rule in app:
        assert {"uid", "title", "for", "expr", "op", "threshold", "summary"} <= set(rule) and rule["op"] in ("gt", "lt")
        for metric in re.findall(r"\b((?:sports_follow|http)_[a-z_]+)\{", rule["expr"]):
            base = re.sub(r"_(bucket|count|sum|total)$", "", metric)
            assert base in sent or base.removesuffix("_seconds") in sent, (rule["uid"], metric)
        assert 'job="' in rule["expr"] or 'job=~"' in rule["expr"], rule["uid"]  # never another app's series
