"""The Health and Performance dashboards (deploy/grafana/ops_dashboards.py) and the data sources every dashboard picks."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

from sports_follow import metrics, server, tracing

from .test_metrics import measured, points  # noqa: F401  (measured: the fixture)

GRAFANA = Path(__file__).resolve().parent.parent / "deploy" / "grafana"
spec = importlib.util.spec_from_file_location("ops_dashboards", GRAFANA / "ops_dashboards.py")
assert spec is not None and spec.loader is not None
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)

# API routes that belong to no journey: the web app's error reports, and an /api/ address no route has. (Requests
# turned away before routing have no route at all: Other too.)
OTHER = {"/api/errors", "/{path:path}"}


def measured_routes() -> set[str]:
    """Each route whose requests metrics.request counts (tracing.measured): an API route, but not a health check or a stream."""
    return {r.path for r in server.app.routes if isinstance(r, server.APIRoute) and tracing.measured(r.path)} | {"/{path:path}"}


def test_the_dashboards_are_what_ops_dashboards_writes():
    """After a change to a journey, run deploy/grafana/ops_dashboards.py: the JSON isn't edited by hand."""
    for name, make in ops.BOARDS.items():
        assert (GRAFANA / "dashboards" / name).read_text() == ops.written(make), f"run deploy/grafana/ops_dashboards.py for {name}"


def test_every_route_fans_call_is_in_one_journey():
    """A new route goes in the journey of what fans do with it (JOURNEYS); one in none would count as Other."""
    for route in sorted(measured_routes()):
        homes = [name for name, routes in ops.JOURNEYS if re.fullmatch(routes, route)]
        assert len(homes) == (0 if route in OTHER else 1), (route, homes)


def alternatives(pattern: str) -> list[str]:
    """A regular expression's top-level alternatives: "a|b(c|d)" is "a" and "b(c|d)". Enough for JOURNEYS: it doesn't
    know escaped brackets, or a | inside [...]."""
    parts, depth, start = [], 0, 0
    for i, char in enumerate(pattern):
        depth += {"(": 1, ")": -1}.get(char, 0)
        if char == "|" and depth == 0:
            parts.append(pattern[start:i])
            start = i + 1
    return [*parts, pattern[start:]]


def test_journeys_name_only_routes_the_api_has():
    """Each alternative of each journey still matches a route, so a removed route leaves none behind; and none
    looks around, which Python's re takes and Prometheus's and Loki's RE2 refuse."""
    for name, routes in ops.JOURNEYS:
        assert not re.search(r"\(\?(?:[=!]|<[=!])", routes), name
        for part in alternatives(routes):
            assert any(re.fullmatch(part, route) for route in measured_routes()), (name, part)


def test_every_journey_query_groups_the_routes_alike():
    """Each query by journey, of the metric (http_route) or the log lines (route), takes the routes from JOURNEYS, in
    its order, as label_replace sees them."""
    expected = [(name, routes.replace("\\", "\\\\")) for name, routes in ops.JOURNEYS]
    chains = 0
    for name in ops.BOARDS:
        board = json.loads((GRAFANA / "dashboards" / name).read_text())
        for panel in board["panels"]:
            for target in panel.get("targets", []):
                got = re.findall(r'"journey", "([^"]+)", "(?:http_route|route)", "((?:[^"\\]|\\.)*)"\)', target["expr"])
                assert got == expected * (len(got) // len(expected)), (board["uid"], panel["title"], target["refId"])
                chains += len(got) // len(expected)
    assert chains >= 10  # the tables, the charts by journey


def test_a_metric_label_the_journeys_read_is_one_the_api_sends(measured):
    metrics.request("GET", "/api/me/feed/items", 502, 0.2)
    [point] = points(measured)["http.server.request.duration"]
    # Grafana Cloud stores http.route as http_route and http.response.status_code as http_response_status_code.
    assert {"http.route", "http.response.status_code"} <= set(point.attributes)
    assert ops.REQ == "http_server_request_duration_seconds"


def test_every_dashboard_picks_the_apps_own_data_sources():
    """The stack has Grafana Cloud's own Prometheus data sources besides the app's (ML and usage): a picker that
    took the first match showed every metric panel empty, and a stat 0, on a fresh load (BHA-677)."""
    names = {"prometheus": ("grafanacloud-wisesturgeon3216-prom", ["grafanacloud-ml-metrics", "grafanacloud-usage"]),
             "loki": ("grafanacloud-wisesturgeon3216-logs", ["grafanacloud-wisesturgeon3216-alert-state-history", "grafanacloud-wisesturgeon3216-usage-insights"])}
    for path in sorted((GRAFANA / "dashboards").glob("*.json")):
        for variable in json.loads(path.read_text())["templating"]["list"]:
            if variable["type"] != "datasource" or variable["query"] not in names:
                continue
            ours, theirs = names[variable["query"]]
            pattern = re.compile(variable["regex"].strip("/"))
            assert pattern.search(ours), (path.name, variable["name"])
            assert not any(pattern.search(name) for name in theirs), (path.name, variable["name"])
