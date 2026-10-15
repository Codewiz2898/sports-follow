"""Read production's logs, traces, metrics and alerts in Grafana Cloud from the Mac, without the web UI.

It only reads (HTTP GETs), with the token in ~/.config/sports-follow/grafana-admin.env (GRAFANA_URL and
GRAFANA_SA_TOKEN; GRAFANA_ADMIN_ENV=<file> for another), and prints no secrets. Run it with the
checkout's .venv:

    .venv/bin/python .claude/skills/debug/grafana.py logs '<LogQL>' [minutes=60] [limit=40]
    .venv/bin/python .claude/skills/debug/grafana.py trace <trace id>
    .venv/bin/python .claude/skills/debug/grafana.py traces '<TraceQL>' [minutes=60]
    .venv/bin/python .claude/skills/debug/grafana.py metric '<PromQL>'
    .venv/bin/python .claude/skills/debug/grafana.py alerts

logs prints each line oldest first: its time (UTC), level, logger and fields, then the message, and the
last lines of its stack if it has one. Examples are in SKILL.md.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx

FIELDS = ("rid", "fan", "job", "player_id", "event_id", "system", "route", "status", "duration_ms", "outcome",
          "error", "place", "page", "call_rid", "push_service", "trace_id")


def client() -> httpx.Client:
    path = Path(os.environ.get("GRAFANA_ADMIN_ENV", Path.home() / ".config/sports-follow/grafana-admin.env"))
    env = dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line and not line.lstrip().startswith("#"))
    clean = lambda value: value.strip().strip('"').strip("'")  # noqa: E731
    return httpx.Client(base_url=clean(env["GRAFANA_URL"]).rstrip("/"), headers={"Authorization": f"Bearer {clean(env['GRAFANA_SA_TOKEN'])}"}, timeout=60)


def source(g: httpx.Client, kind: str) -> str:
    """The uid of the stack's own data source of this kind (not its usage or alert-history ones)."""
    for d in g.get("/api/datasources").json():
        if d["type"] == kind and not any(x in d["name"] for x in ("usage", "alert-state-history", "ml-metrics")):
            return d["uid"]
    sys.exit(f"no {kind} data source")


def logs(g: httpx.Client, query: str, minutes: int = 60, limit: int = 40) -> None:
    now = time.time_ns()
    r = g.get(f"/api/datasources/proxy/uid/{source(g, 'loki')}/loki/api/v1/query_range",
              params={"query": query, "start": now - minutes * 60 * 10**9, "end": now, "limit": limit, "direction": "backward"})
    body = r.json()
    if r.status_code != 200:
        sys.exit(f"Loki: HTTP {r.status_code}: {body.get('message') or body.get('error') or body}")
    lines = sorted((int(ts), result["stream"], line) for result in body["data"]["result"] for ts, line in result["values"])
    for ts, labels, line in lines:
        fields = " ".join(f"{k}={labels[k]}" for k in FIELDS if labels.get(k))
        level = labels.get("detected_level") or labels.get("severity_text") or ""
        print(f"{time.strftime('%m-%d %H:%M:%S', time.gmtime(ts / 1e9))} {level:5} {labels.get('scope_name', labels.get('service_name', ''))} {fields}")
        print(f"    {line}")
        stack = labels.get("exception_stacktrace")
        if stack:
            print("    | " + "\n    | ".join(stack.rstrip().splitlines()[-6:]))
    print(f"({len(lines)} line(s) in the last {minutes} min{'; more exist, raise the limit' if len(lines) >= limit else ''})")


def traces(g: httpx.Client, query: str, minutes: int = 60) -> None:
    now = int(time.time())
    r = g.get(f"/api/datasources/proxy/uid/{source(g, 'tempo')}/api/search", params={"q": query, "start": now - minutes * 60, "end": now, "limit": 20})
    body = r.json()
    if r.status_code != 200:
        sys.exit(f"Tempo: HTTP {r.status_code}: {body}")
    for t in body.get("traces", []):
        start = time.strftime("%m-%d %H:%M:%S", time.gmtime(int(t.get("startTimeUnixNano", 0)) / 1e9))
        print(f"{start} {t['traceID']} {t.get('rootServiceName')} {t.get('rootTraceName')} {t.get('durationMs', 0)} ms")
    print(f"({len(body.get('traces', []))} trace(s))")


def trace(g: httpx.Client, trace_id: str) -> None:
    """Every span of one trace, in the order they started, with its time, duration, status and key attributes."""
    r = g.get(f"/api/datasources/proxy/uid/{source(g, 'tempo')}/api/traces/{trace_id}")
    if r.status_code != 200:
        sys.exit(f"Tempo: HTTP {r.status_code}: {r.text[:300]}")
    spans = []
    for batch in r.json().get("batches", []):
        service = next((a["value"].get("stringValue") for a in batch["resource"]["attributes"] if a["key"] == "service.name"), "")
        for scope in batch.get("scopeSpans", []):
            for s in scope.get("spans", []):
                attrs = {a["key"]: next(iter(a["value"].values()), "") for a in s.get("attributes", [])}
                keep = {k: v for k, v in attrs.items() if k in ("http.route", "http.response.status_code", "server.address", "url.full", "db.query.text", "job.id", "player_id", "event_id", "error.type")}
                status = (s.get("status") or {}).get("code", "")
                start, end = int(s["startTimeUnixNano"]), int(s["endTimeUnixNano"])
                spans.append((start, service, s["name"], (end - start) / 1e6, status, keep, [e.get("name") for e in s.get("events", [])]))
    for start, service, name, ms, status, keep, events in sorted(spans):
        flag = " ERROR" if "ERROR" in str(status) else ""
        print(f"{time.strftime('%H:%M:%S', time.gmtime(start / 1e9))} {ms:9.1f} ms {service} {name}{flag} {keep}{' events:' + ','.join(events) if events else ''}")


def metric(g: httpx.Client, query: str) -> None:
    r = g.get(f"/api/datasources/proxy/uid/{source(g, 'prometheus')}/api/v1/query", params={"query": query})
    body = r.json()
    if r.status_code != 200 or body.get("status") != "success":
        sys.exit(f"Prometheus: HTTP {r.status_code}: {body.get('error', body)}")
    for result in body["data"]["result"]:
        labels = {k: v for k, v in result["metric"].items() if k not in ("service_instance_id", "deployment_environment_name", "service_name")}
        print(labels, result["value"][1])


def alerts(g: httpx.Client) -> None:
    """Every alert rule in the Sports Follow folder that isn't quiet: pending, firing, or broken."""
    groups = g.get("/api/prometheus/grafana/api/v1/rules").json()["data"]["groups"]
    quiet = 0
    for group in groups:
        if "sports" not in group.get("file", "").lower():
            continue
        for rule in group["rules"]:
            if rule.get("state") == "inactive" and rule.get("health") == "ok":
                quiet += 1
                continue
            since = next((a.get("activeAt") for a in rule.get("alerts", [])), "")
            print(f"{rule.get('state')}/{rule.get('health')} {rule['name']} {since}")
            for a in rule.get("alerts", [])[:5]:
                print("    ", {k: v for k, v in a.get("labels", {}).items() if k not in ("alertname", "grafana_folder", "__alert_rule_uid__")})
    print(f"({quiet} other rule(s) quiet)")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] not in ("logs", "traces", "trace", "metric", "alerts"):
        sys.exit(__doc__)
    g = client()
    match args:
        case ["logs", query, *rest]:
            logs(g, query, *(int(x) for x in rest[:2]))
        case ["traces", query, *rest]:
            traces(g, query, *(int(x) for x in rest[:1]))
        case ["trace", trace_id]:
            trace(g, trace_id)
        case ["metric", query]:
            metric(g, query)
        case ["alerts"]:
            alerts(g)
        case _:
            sys.exit(__doc__)
