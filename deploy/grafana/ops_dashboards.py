"""Writes the Health and Performance dashboards (dashboards/health.json, performance.json) that the runbook
(deploy/RUNBOOK.md) starts from. Their journey panels group the API's routes into what fans do, from JOURNEYS
below, in every one of a dozen queries: change a journey here, then run

    .venv/bin/python deploy/grafana/ops_dashboards.py && deploy/grafana/apply-dashboards.sh

tests/test_ops_dashboards.py fails when the files differ from what this writes, and when a route of the API is in
no journey or two. The other dashboards are plain JSON, edited by hand or exported from Grafana.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# What fans do, and the API routes each calls (http_route, as FastAPI names it; a regular expression matched
# whole). A route is in one journey at most: the rest, such as /api/errors, are Other.
JOURNEYS = [
    ("Sign in", r"/api/auth/.+"),
    ("App start", r"/api/(config|sports|sports\.css|looks|credits|me/account|me/start|me/source)"),
    ("Today", r"/api/me/(activity|week|following|matches|results|research|sports)|/api/league|/api/starters"),
    ("Live and match pages", r"/api/live|/api/match/[^/]+/[^/]+"),
    ("Player and team pages", r"/api/players/[^/]+/(card|form|results|results/[^/]+|refresh)|/api/teams/[^/]+"),
    ("Feed", r"/api/me/feed(/items)?|/api/players/[^/]+/feed(/.+)?"),
    ("Search and follow", r"/api/search|/api/athletes/.+|/api/follows(/[^/]+)?|/api/teams/[^/]+/follow"
                          r"|/api/match/[^/]+/[^/]+/follow|/api/me/matches/[^/]+|/api/campaigns/[^/]+(/follow)?"),
    ("Alerts settings", r"/api/push(/.+)?|/api/notifications/.+|/api/me/mutes(/[^/]+)?|/api/(follows|teams)/[^/]+/alerts"
                        r"|/api/me/matches/[^/]+/alerts"),
    ("Call it and leagues", r"/api/predictions/[^/]+/pick|/api/me/(predictions|leagues)|/api/leagues(/[^/]+(/(invite|join|me|shared))?)?"),
    ("Clips", r"/api/clips(/[^/]+(/(done|report))?)?"),
    ("Pictures", r"/api/(photos|logos)/[^/]+|/api/(.+/)?share\.png"),
    ("Admin panel", r"/api/admin/.+"),
]

HERE = Path(__file__).resolve().parent
API = 'job="sports-follow-api"'
REQ = "http_server_request_duration_seconds"  # metrics.request: by http_route and http_response_status_code
# Each API request's log line (server.RequestLog), by its route and status: counts come from these, exactly. The
# metric's increase() misses each series' first request (metrics.seed says why), which on a quiet day is most of
# them: one 5xx, or the day's sign-ins. Times come from the metric's buckets, which a missed request barely moves.
LINES = '{service_name="sports-follow-api"} | scope_name="sports_follow.requests" | route != "/api/health" | route !~ ".*/stream"'
RANGE, STEP, AUTO = "$__range", "$__rate_interval", "$__auto"
PROM = {"type": "prometheus", "uid": "${prom}"}
LOKI = {"type": "loki", "uid": "${loki}"}
Json = dict[str, Any]


# ---------------------------------------------------------------- queries by journey

def journeys(by_route: str, label: str = "http_route") -> str:
    """A vector by route (the metric's http_route, a log line's route), each series labelled with its route's
    journey, else Other. label_replace matches whole values, in PromQL and LogQL alike."""
    expr = by_route
    for name, routes in JOURNEYS:
        quoted = routes.replace("\\", "\\\\")
        expr = f'label_replace({expr}, "journey", "{name}", "{label}", "{quoted}")'
    return f'label_replace({expr}, "journey", "Other", "journey", "")'


def lines(more: str = "", window: str = RANGE) -> str:
    """API requests in the window, from their log lines (more: label filters, " | status >= 500")."""
    return f"sum(count_over_time({LINES}{more} [{window}]))"


def lines_by_route(window: str, more: str = "") -> str:
    return f"sum by (route) (count_over_time({LINES}{more} [{window}]))"


def by_journey(window: str, more: str = "") -> str:
    """Requests by journey, from their log lines; with more, 0 for a journey that had requests and none of those."""
    if not more:
        return f"sum by (journey) ({journeys(lines_by_route(window), 'route')})"
    filled = f"({lines_by_route(window, more)} or 0 * ({lines_by_route(window)} > 0))"
    return f"sum by (journey) ({journeys(filled, 'route')})"


def answered_by_journey(window: str) -> str:
    """The share of each journey's requests not answered with a server error."""
    return f"1 - {by_journey(window, ' | status >= 500')} / ({by_journey(window)} > 0)"


def time_by_journey(q: float, window: str) -> str:
    buckets = f"sum by (le, http_route) (rate({REQ}_bucket{{{API}}}[{window}]))"
    return f"histogram_quantile({q}, sum by (le, journey) ({journeys(buckets)}))"


def time_by_route(q: float, window: str) -> str:
    return f"histogram_quantile({q}, sum by (le, http_route) (rate({REQ}_bucket{{{API}}}[{window}])))"


def api_time(q: float, window: str) -> str:
    return f"histogram_quantile({q}, sum by (le) (rate({REQ}_bucket{{{API}}}[{window}])))"


def sign_ins(route: str, status: str) -> str:
    """Requests to a sign-in route (or routes, a regular expression) answered with a status (status filters)."""
    return lines(f' | route =~ "{route}" | status {status}')


# ---------------------------------------------------------------- panels

def target(expr: str, ref: str = "A", legend: str = "", instant: bool = True, ds: Json = PROM) -> Json:
    t: Json = {"refId": ref, "datasource": ds, "expr": expr, "legendFormat": legend, "editorMode": "code"}
    if ds is LOKI:
        t["queryType"] = "instant" if instant else "range"
    else:
        t.update(instant=instant, range=not instant)
    return t


def panel(kind: str, title: str, description: str, targets: list[Json], pos: tuple[int, int, int, int], **rest: Any) -> Json:
    x, y, w, h = pos
    kinds = {t["datasource"]["type"] for t in targets}
    ds = targets[0]["datasource"] if len(kinds) == 1 else {"type": "datasource", "uid": "-- Mixed --"} if kinds else PROM
    return {"type": kind, "title": title, "description": description, "datasource": ds,
            "targets": targets, **rest, "gridPos": {"x": x, "y": y, "w": w, "h": h}}


def steps(*pairs: tuple[str, float | None]) -> Json:
    return {"mode": "absolute", "steps": [{"color": color, "value": value} for color, value in pairs]}


GREEN = steps(("green", None))
NONE_GOOD = steps(("green", None), ("red", 1))
ANSWERED = steps(("red", None), ("orange", 0.99), ("green", 0.999))
SECONDS = steps(("green", None), ("orange", 1), ("red", 3))


def stat(title: str, description: str, expr: str, pos: tuple[int, int, int, int], unit: str = "short", thresholds: Json = GREEN,
         no_value: str = "0", decimals: int | None = None, ds: Json = PROM, links: list[Json] | None = None) -> Json:
    defaults: Json = {"unit": unit, "thresholds": thresholds, "color": {"mode": "thresholds"}, "noValue": no_value}
    if decimals is not None or unit == "short":  # counts are whole: increase() extrapolates
        defaults["decimals"] = decimals or 0
    return panel("stat", title, description, [target(expr, ds=ds)], pos, **({"links": links} if links else {}),
                 fieldConfig={"defaults": defaults, "overrides": []},
                 options={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                          "colorMode": "value", "graphMode": "none", "textMode": "value"})


def series(title: str, description: str, targets: list[Json], pos: tuple[int, int, int, int], unit: str = "short", bars: bool = False,
           low: float | None = None, high: float | None = None, soft_low: float | None = None, thresholds: Json | None = None,
           log: bool = False) -> Json:
    custom: Json = {"drawStyle": "bars" if bars else "line", "lineWidth": 1, "fillOpacity": 30 if bars else 0, "showPoints": "auto",
                    "pointSize": 5, "spanNulls": False}
    defaults: Json = {"unit": unit, "custom": custom, "noValue": "No data in this range"}
    if low is not None:
        defaults["min"] = low
    if high is not None:
        defaults["max"] = high
    if soft_low is not None:
        custom["axisSoftMin"] = soft_low
    if log:  # job and source times run from milliseconds to minutes
        custom["scaleDistribution"] = {"type": "log", "log": 10}
    if thresholds:
        defaults["thresholds"] = thresholds
        custom["thresholdsStyle"] = {"mode": "line"}
    return panel("timeseries", title, description, targets, pos, interval="10m",  # each point at least 10 minutes: few requests
                 fieldConfig={"defaults": defaults, "overrides": []},
                 options={"legend": {"displayMode": "table", "placement": "bottom", "calcs": ["mean", "max"], "showLegend": True},
                          "tooltip": {"mode": "multi", "sort": "desc"}})


def table(title: str, description: str, targets: list[Json], pos: tuple[int, int, int, int], key: str, names: dict[str, str],
          sort: tuple[str, bool], columns: list[Json]) -> Json:
    """One row per value of the label key, a column per target (named by its refId in names), joined by Grafana. A row
    without a value to sort by is left out: Grafana sorts no further past one."""
    for t in targets:
        if t["datasource"] is PROM:
            t["format"] = "table"
    rename = {f"Value #{ref}": name for ref, name in names.items()} | {key: "Route" if key == "http_route" else key.capitalize()}
    width = {"matcher": {"id": "byName", "options": rename[key]}, "properties": [{"id": "custom.width", "value": 230 if key == "http_route" else 180}]}
    return panel("table", title, description, targets, pos,
                 fieldConfig={"defaults": {"decimals": 0, "custom": {"align": "auto", "minWidth": 60}}, "overrides": [width, *columns]},
                 options={"showHeader": True, "cellHeight": "sm"},
                 transformations=[{"id": "merge", "options": {}},
                                  {"id": "organize", "options": {"excludeByName": {"Time": True}, "renameByName": rename,
                                                                  "indexByName": {key: 0, **{f"Value #{ref}": i for i, ref in enumerate(names, 1)}}}},
                                  {"id": "filterByValue", "options": {"type": "exclude", "match": "any",
                                                                      "filters": [{"fieldName": sort[0], "config": {"id": "isNull", "options": {}}}]}},
                                  {"id": "sortBy", "options": {"sort": [{"field": sort[0], "desc": sort[1]}]}}])


def column(name: str, unit: str, thresholds: Json, decimals: int | None = None) -> Json:
    properties: list[Json] = [{"id": "unit", "value": unit}, {"id": "thresholds", "value": thresholds},
                              {"id": "custom.cellOptions", "value": {"type": "color-text"}}]
    if decimals is not None:
        properties.append({"id": "decimals", "value": decimals})
    return {"matcher": {"id": "byName", "options": name}, "properties": properties}


def link(title: str, uid: str) -> list[Json]:
    return [{"title": title, "url": f"/d/{uid}?${{__url_time_range}}"}]


def board(uid: str, title: str, description: str, panels: list[Json], logs: bool = False) -> Json:
    """The dashboard, as the others are made: it finds its own data sources (not Grafana Cloud's usage or ML ones)."""
    sources = [{"name": "prom", "label": "Metrics", "type": "datasource", "query": "prometheus",
                "regex": "/^(?!.*(usage|ml-metrics)).*$/", "hide": 0, "current": {}, "refresh": 1}]
    if logs:
        sources.insert(0, {"name": "loki", "label": "Logs", "type": "datasource", "query": "loki",
                           "regex": "/^(?!.*(alert-state-history|usage)).*$/", "hide": 0, "current": {}, "refresh": 1})
    return {"uid": uid, "title": title, "description": description, "tags": ["sports-follow"], "timezone": "browser",
            "editable": True, "schemaVersion": 39, "time": {"from": "now-24h", "to": "now"}, "refresh": "5m", "graphTooltip": 1,
            "templating": {"list": sources}, "annotations": {"list": []},
            "links": [{"title": "Sports Follow", "type": "dashboards", "tags": ["sports-follow"], "asDropdown": True,
                       "includeVars": False, "keepTime": True},
                      {"title": "Runbook", "type": "link", "icon": "doc", "targetBlank": True,
                       "url": "https://github.com/Codewiz2898/sports-follow/blob/main/deploy/RUNBOOK.md"}],
            "panels": [p | {"id": i} for i, p in enumerate(panels, 1)]}


# ---------------------------------------------------------------- Health

def health() -> Json:
    probe = 'job="sports-follow-uptime"'
    errors = f'({lines(" | status >= 500")} or vector(0))'
    pushes = 'job=~"sports-follow-(api|worker)"'
    p = [
        stat("Reachable from outside", "Share of Grafana's uptime checks (Mumbai and Singapore, each minute) that got a healthy answer from /api/health over the range: DNS, the certificate, Caddy and the API together. Under 100%: the Synthetics page says which probe and why.",
             f"sum(increase(probe_all_success_sum{{{probe}}}[{RANGE}])) / sum(increase(probe_all_success_count{{{probe}}}[{RANGE}]))",
             (0, 0, 6, 4), unit="percentunit", thresholds=steps(("red", None), ("orange", 0.99), ("green", 0.995)), no_value="No checks", decimals=2),
        stat("API answers without a server error", "Share of API requests over the range not answered with a 5xx (health checks and live streams aren't counted). Few requests make this jumpy: read it with the count beside it.",
             f"1 - {errors} / ({lines()} > 0)", (6, 0, 6, 4), unit="percentunit", thresholds=ANSWERED, no_value="No requests", decimals=2, ds=LOKI),
        stat("Server errors (5xx)", "API requests answered with a server error over the range. Each is on the API dashboard's Failed requests, with its trace.",
             errors, (12, 0, 6, 4), thresholds=NONE_GOOD, ds=LOKI, links=link("API dashboard", "sports-follow-api")),
        stat("Errors logged", "Exceptions logged by the API and worker, and errors the web app reported, over the range. The Errors dashboard groups them by what failed and where.",
             'sum(count_over_time({service_name=~"sports-follow-(api|worker)"} | error != "" [$__range]))', (18, 0, 6, 4),
             thresholds=steps(("green", None), ("orange", 1)), ds=LOKI, links=link("Errors dashboard", "sports-follow-errors")),

        panel("alertlist", "Alerts firing now", "The Sports Follow folder's alert rules (deploy/grafana/rules.json and app-rules.json) that are firing or about to. The runbook says what each means and what to do.",
              [], (0, 4, 8, 16),
              options={"viewMode": "list", "groupMode": "default", "groupBy": [], "maxItems": 30, "sortOrder": 1, "dashboardAlerts": False,
                       "alertName": "", "alertInstanceLabelFilter": "", "folder": {"uid": "sports-follow", "title": "Sports Follow"},
                       "stateFilter": {"firing": True, "pending": True, "noData": False, "normal": False, "error": True}}),
        table("Fans' journeys over the range", "Each thing fans do, by the API routes it calls (the runbook lists them): its requests, how many were answered with a server error (5xx) or refused (4xx: a wrong code, a missing page), and the share answered without a server error.",
              [target(f"{by_journey(RANGE)} > 0", "A", "requests", ds=LOKI), target(by_journey(RANGE, " | status >= 500"), "B", "5xx", ds=LOKI),
               target(by_journey(RANGE, " | status >= 400 | status < 500"), "C", "4xx", ds=LOKI), target(answered_by_journey(RANGE), "D", "answered", ds=LOKI)],
              (8, 4, 16, 16), "journey", {"A": "Requests", "B": "Server errors", "C": "Refused", "D": "Answered"}, ("Answered", False),
              [column("Answered", "percentunit", ANSWERED, 2), column("Server errors", "short", NONE_GOOD)]),
        series("Answered without a server error, by journey", "The share of each journey's requests not answered with a 5xx, each interval of the chart (10 minutes or more). A gap: no requests then.",
               [target(answered_by_journey(AUTO), legend="{{journey}}", instant=False, ds=LOKI)], (0, 20, 12, 14), unit="percentunit", high=1, soft_low=0.95,
               thresholds=ANSWERED),
        series("Requests, by journey", "Requests each interval of the chart (10 minutes or more).",
               [target(by_journey(AUTO), legend="{{journey}}", instant=False, ds=LOKI)], (12, 20, 12, 14), bars=True),

        stat("Codes sent", "Sign-in codes emailed over the range (POST /api/auth/email/start answered 200; a second tap within the minute counts again).",
             f"{sign_ins('/api/auth/email/start', '= 200')} or vector(0)", (0, 34, 4, 4), ds=LOKI),
        stat("Signed in", "Codes that signed a fan in (POST /api/auth/email/verify answered 200).",
             f"{sign_ins('/api/auth/email/verify', '= 200')} or vector(0)", (4, 34, 4, 4), ds=LOKI),
        stat("Codes used", "Sign-ins by code for each code sent, over the range. Low: codes aren't arriving (Resend, the spam folder) or fans give up. Few sign-ins make it jumpy.",
             f"({sign_ins('/api/auth/email/verify', '= 200')} or vector(0)) / ({sign_ins('/api/auth/email/start', '= 200')} > 0)", (8, 34, 4, 4),
             unit="percentunit", thresholds=steps(("orange", None), ("green", 0.5)), no_value="No codes sent", decimals=0, ds=LOKI),
        stat("Wrong codes", "Codes refused at sign-in (verify answered 400: wrong, run out, or not 6 digits).",
             f"{sign_ins('/api/auth/email/verify', '= 400')} or vector(0)", (12, 34, 4, 4), ds=LOKI),
        stat("Code limits hit", "Sign-in requests refused with 429: an address or network asked for too many codes or tried too many wrong ones, or the day's codes ran out (fans read \"has sent all the sign-in codes it can today\").",
             f"{sign_ins('/api/auth/email/.+', '= 429')} or vector(0)", (16, 34, 4, 4), thresholds=steps(("green", None), ("orange", 1)), ds=LOKI),
        stat("Send failed", "email/start answered 502 (Resend refused or didn't answer) or 503 (email isn't set up on the server). Fans can't sign in by email while this grows.",
             f"{sign_ins('/api/auth/email/start', '>= 500')} or vector(0)", (20, 34, 4, 4), thresholds=NONE_GOOD, ds=LOKI),
        series("Sign-in requests, by step and answer", "Each interval of the chart: start = a code asked for, verify = a code tried, google/start and google/callback = Continue with Google (the callback always answers 303: its failures are in the API's warnings).",
               [target(f'sum by (step, status) (label_replace(sum by (route, status) (count_over_time({LINES} | route =~ "/api/auth/.+" [{AUTO}])), "step", "$1", "route", "/api/auth/(?:email/)?(.+)"))',
                       legend="{{step}} {{status}}", instant=False, ds=LOKI)], (0, 38, 24, 10), bars=True),

        stat("Games behind", "Live games not polled for three of their rounds (at least 3 minutes), now: their scores and alerts are late. The Jobs and live polls dashboard shows why.",
             'sum(sports_follow_live_games_behind{job="sports-follow-worker"})', (0, 48, 4, 4), thresholds=NONE_GOOD, no_value="No reading"),
        stat("Oldest score", "How old the live score is on the live card that's furthest behind, now.",
             'max(sports_follow_cards_live_data_age_seconds{job="sports-follow-worker"})', (4, 48, 4, 4), unit="s",
             thresholds=steps(("green", None), ("orange", 180), ("red", 600)), no_value="No live cards"),
        stat("Not pushed", "Moments found over 3 minutes ago that nobody has pushed yet, now. Over 0: fans aren't getting alerts.",
             'max(sports_follow_moments_unsent{job="sports-follow-worker"})', (8, 48, 4, 4), thresholds=NONE_GOOD, no_value="No reading"),
        stat("Delivered", "Share of pushes over the range that their push service took (unsubscribed devices, 404 and 410, are left out). The Notifications dashboard has the failures by push service.",
             f'1 - (sum(increase(sports_follow_pushes_total{{{pushes}, result="failed"}}[{RANGE}])) or vector(0))'
             f' / sum(increase(sports_follow_pushes_total{{{pushes}, result=~"delivered|failed"}}[{RANGE}]))',
             (12, 48, 4, 4), unit="percentunit", thresholds=steps(("red", None), ("orange", 0.95), ("green", 0.99)), no_value="No pushes", decimals=1),
        stat("Feed, 24 h", "New videos about followed players the curator kept in the last day, whatever the range. 0 for a day: the Feed is going stale, and the Feed dashboard shows where its funnel stops.",
             'sum(increase(sports_follow_feed_kept_total{job="sports-follow-curator"}[24h]))', (16, 48, 4, 4),
             thresholds=steps(("red", None), ("green", 1)), no_value="No reading"),
        stat("Cards failed", "Player pages (cards) whose build failed, now: fans see an error on them. The Jobs and live polls dashboard has the builds; the worker's warnings say why.",
             'sum(sports_follow_cards{job="sports-follow-worker", status="failed"})', (20, 48, 4, 4), thresholds=steps(("green", None), ("orange", 1))),
    ]
    return board("sports-follow-health", "Sports Follow · Health",
                 "Is it working for fans? Reachability from outside, alerts firing, each fan journey's success rate, sign-in, and whether live scores, pushes, the Feed and player pages are fresh. Open it first: deploy/RUNBOOK.md says what to look for.",
                 p, logs=True)


# ---------------------------------------------------------------- Performance

def performance() -> Json:
    probe = 'job="sports-follow-uptime"'
    jobs = 'job=~"sports-follow-(worker|curator)"'
    pushes = 'job=~"sports-follow-(api|worker)"'
    clients = 'job=~"sports-follow-(api|worker|curator)"'
    node = 'job="sports-follow"'
    quantiles = ((0.5, "p50"), (0.95, "p95"), (0.99, "p99"))
    slowest = {0.5: "Half of", 0.95: "The slowest 5% of", 0.99: "The slowest 1% of"}
    times = [column(label, "s", SECONDS, 2) for _, label in quantiles]
    p = [
        *(stat(f"API {label}", f"{slowest[q]} API requests over the range took longer than this (health checks and live streams aren't counted; the buckets make it approximate). From the API's side: a fan's phone adds the trip to Sydney.",
               api_time(q, RANGE), (4 * i, 0, 4, 4), unit="s", thresholds=SECONDS, no_value="No requests", decimals=2)
          for i, (q, label) in enumerate(quantiles)),
        stat("Outside p95", "The slowest 5% of the uptime checks from Mumbai and Singapore over the range, from DNS to the last byte of /api/health: what the trip to the server costs before any work.",
             f"histogram_quantile(0.95, sum by (le) (rate(probe_all_duration_seconds_bucket{{{probe}}}[{RANGE}])))", (12, 0, 4, 4), unit="s",
             thresholds=steps(("green", None), ("orange", 1.5), ("red", 3)), no_value="No checks", decimals=2),
        stat("Push wait p95", "From a poll or build finding a moment (a goal, a wicket, a result) to its pushes going out, the slowest 5% over the range.",
             f"histogram_quantile(0.95, sum by (le) (rate(sports_follow_push_wait_seconds_bucket{{{pushes}}}[{RANGE}])))", (16, 0, 4, 4), unit="s",
             thresholds=steps(("green", None), ("orange", 30), ("red", 120)), no_value="No pushes", decimals=1),
        stat("Queue wait p95", "How long worker jobs waited for a slot before starting, the slowest 5% over the range. Long: polls, builds and pushes queue behind each other.",
             f"histogram_quantile(0.95, sum by (le) (rate(sports_follow_job_wait_seconds_bucket{{{jobs}}}[{RANGE}])))", (20, 0, 4, 4), unit="s",
             thresholds=steps(("green", None), ("orange", 30), ("red", 120)), no_value="No jobs", decimals=1),

        table("Time taken by journey over the range", "Each fan journey's requests and how long they took (the runbook lists each journey's routes). A p95 over 1 s on a journey fans use often is worth a look; over 3 s for 10 minutes, \"The API is slow\" fires.",
              [target(f"{by_journey(RANGE)} > 0", "A", "requests", ds=LOKI),
               *(target(f"{time_by_journey(q, RANGE)} >= 0", ref, label) for ref, (q, label) in zip("BCD", quantiles))],
              (0, 4, 12, 16), "journey", {"A": "Requests", "B": "p50", "C": "p95", "D": "p99"}, ("p95", True), times),
        table("Slowest routes over the range", "Every API route that answered in the range, slowest first by p95. A route with a handful of requests can top this on one slow one: read it with its count. A route whose few requests the metric hasn't timed (a process's first of each isn't) isn't listed; Health counts every request.",
              [target(f'sum by (http_route) (label_replace({lines_by_route(RANGE, ' | route != ""')}, "http_route", "$1", "route", "(.+)")) > 0', "A", "requests", ds=LOKI),
               *(target(f"{time_by_route(q, RANGE)} >= 0", ref, label) for ref, (q, label) in zip("BCD", quantiles))],
              (12, 4, 12, 16), "http_route", {"A": "Requests", "B": "p50", "C": "p95", "D": "p99"}, ("p95", True), times),

        series("Time taken p95, by journey", "The slowest 5% of each journey's requests, each interval of the chart (10 minutes or more).",
               [target(time_by_journey(0.95, STEP), legend="{{journey}}", instant=False)], (0, 20, 12, 14), unit="s", thresholds=SECONDS),
        series("From outside, by phase", "Each uptime check's time, by phase: resolve (DNS), connect (the trip to the server), tls, processing (Caddy and the API answering, plus one more trip), transfer. Connect and tls are the distance; processing is ours.",
               [target(f"avg by (probe, phase) (avg_over_time(probe_http_duration_seconds{{{probe}}}[{STEP}]))", legend="{{probe}} {{phase}}", instant=False)],
               (12, 20, 12, 14), unit="s"),
        series("API time taken, p50, p95 and p99", "Every API request together, each interval of the chart.",
               [target(api_time(q, STEP), ref, label, instant=False) for ref, (q, label) in zip("ABC", quantiles)], (0, 34, 12, 14), unit="s",
               thresholds=SECONDS),
        series("Moment to push, p95", "From a moment found to its pushes, the slowest 5%, each interval of the chart. A gap: no pushes then.",
               [target(f"histogram_quantile(0.95, sum by (le) (rate(sports_follow_push_wait_seconds_bucket{{{pushes}}}[{STEP}])))", legend="p95", instant=False)],
               (12, 34, 12, 14), unit="s"),
        series("Job time taken p95, by job", "How long each worker (and curator) job ran, the slowest 5%. Lane jobs (engine_lane, poll_lane, history_lane) run for minutes by design; the top bucket is 15 minutes.",
               [target(f"histogram_quantile(0.95, sum by (le, job_name) (rate(sports_follow_job_duration_seconds_bucket{{{jobs}}}[{STEP}])))", legend="{{job_name}}", instant=False)],
               (0, 48, 12, 14), unit="s", log=True),
        series("Job queue wait p95, by job", "How long each job waited for a worker slot, the slowest 5%. \"Jobs are waiting for the worker\" fires when one has waited 2 minutes for 5.",
               [target(f"histogram_quantile(0.95, sum by (le, job_name) (rate(sports_follow_job_wait_seconds_bucket{{{jobs}}}[{STEP}])))", legend="{{job_name}}", instant=False)],
               (12, 48, 12, 14), unit="s", log=True),
        series("Source time taken p95, by host", "Requests to the data sources, the model gateway, push services and email, by host, the slowest 5%. The Sources dashboard has their errors and allowances.",
               [target(f"histogram_quantile(0.95, sum by (le, server_address) (rate(http_client_request_duration_seconds_bucket{{{clients}}}[{STEP}])))", legend="{{server_address}}", instant=False)],
               (0, 62, 24, 14), unit="s", log=True),
        series("Server CPU", "Busy: the share of CPU time spent working. Held back: time Lightsail withheld (steal) once the burst allowance is used up; over 20% for 30 minutes alerts.",
               [target(f'1 - avg(rate(node_cpu_seconds_total{{{node}, mode="idle"}}[{STEP}]))', "A", "busy", instant=False),
                target(f'avg(rate(node_cpu_seconds_total{{{node}, mode="steal"}}[{STEP}]))', "B", "held back", instant=False)],
               (0, 76, 8, 12), unit="percentunit", low=0, high=1),
        series("Server memory free", "Memory available to the app's processes; under 5% for 10 minutes alerts. docker stats on the server says which container holds it.",
               [target(f"node_memory_MemAvailable_bytes{{{node}}} / node_memory_MemTotal_bytes{{{node}}}", "A", "free", instant=False)],
               (8, 76, 8, 12), unit="percentunit", low=0, high=1),
        series("Server load", "Processes running or waiting for the CPU, averaged over 1, 5 and 15 minutes. Past the CPU count for long, work queues.",
               [*(target(f"node_load{m}{{{node}}}", ref, f"{m} min", instant=False) for ref, m in zip("ABC", (1, 5, 15))),
                target(f"count(count by (cpu) (node_cpu_seconds_total{{{node}}}))", "D", "CPUs", instant=False)],
               (16, 76, 8, 12)),
    ]
    return board("sports-follow-performance", "Sports Follow · Performance",
                 "How fast is it? API time taken (p50, p95, p99) overall, by fan journey and by route; from outside, by phase; moment to push; worker jobs' time and queue wait; the sources' time; the server's CPU, memory and load. deploy/RUNBOOK.md says what's normal.",
                 p, logs=True)


BOARDS = {"health.json": health, "performance.json": performance}


def written(make: Any) -> str:
    return json.dumps(make(), indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    for name, make in BOARDS.items():
        (HERE / "dashboards" / name).write_text(written(make))
        print(f"deploy/grafana/dashboards/{name}")
