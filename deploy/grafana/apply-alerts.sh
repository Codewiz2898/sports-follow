#!/usr/bin/env bash
# From the Mac: put the alert rules into Grafana Cloud (the server's, rules.json, and the app's, app-rules.json),
# with an email contact point and the notification policy that sends every alert there, and the uptime check
# from outside the server (README.md, "Alerts"). Safe to run again after changing any of them: everything is
# replaced by what's here.
#   deploy/grafana/apply-alerts.sh            the folder, contact point, policy, rules and uptime check
#   deploy/grafana/apply-alerts.sh --test     also send a test email through the contact point
# Settings from ~/.config/sports-follow/grafana-admin.env (GRAFANA_ADMIN_ENV=<file> for another):
#   GRAFANA_URL       the stack's Grafana, https://<stack>.grafana.net
#   GRAFANA_SA_TOKEN  a service account token with the Admin role
#   ALERT_EMAIL       where alerts go (several: comma-separated)
#   GRAFANA_DATASOURCE_UID  optional: the Prometheus data source, if it isn't the stack's grafanacloud-…-prom
#   APP_URL           optional: the app's address, https://<domain>; else the production environment's APP_URL (gh)
# The notification policy replaces the stack's whole policy tree: fine for a stack that only alerts on this.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${GRAFANA_ADMIN_ENV:-$HOME/.config/sports-follow/grafana-admin.env}"
FOLDER_UID=sports-follow
CONTACT_UID=sports-follow-email
CONTACT_NAME="Sports Follow email"
TEST=""
case "${1:-}" in --test) TEST=1 ;; "") ;; *) echo "usage: deploy/grafana/apply-alerts.sh [--test]" >&2; exit 2 ;; esac

die() { echo "apply-alerts: $*" >&2; exit 1; }
say() { echo "apply-alerts: $*"; }
[ -f "$ENV_FILE" ] || die "no $ENV_FILE (README.md, \"Alerts\")"
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a
: "${GRAFANA_URL:?set GRAFANA_URL in $ENV_FILE}" "${GRAFANA_SA_TOKEN:?set GRAFANA_SA_TOKEN in $ENV_FILE}" "${ALERT_EMAIL:?set ALERT_EMAIL in $ENV_FILE}"
command -v jq >/dev/null || die "needs jq"
GRAFANA_URL="${GRAFANA_URL%/}"

# api <method> <path> [json body]: the response body; fails with Grafana's message on an error status.
# X-Disable-Provenance keeps what this makes editable in Grafana's UI (running this again puts it back).
api() {
  local out code
  out=$(mktemp)
  code=$(curl -sS -o "$out" -w '%{http_code}' -X "$1" "$GRAFANA_URL$2" \
    -H "Authorization: Bearer $GRAFANA_SA_TOKEN" -H 'Content-Type: application/json' -H 'X-Disable-Provenance: true' \
    ${3:+--data-binary "$3"}) || { rm -f "$out"; die "$1 $2: Grafana didn't answer"; }
  if [ "${code:0:1}" != 2 ]; then
    echo "apply-alerts: $1 $2: HTTP $code: $(head -c 400 "$out")" >&2
    rm -f "$out"
    return 1
  fi
  cat "$out"
  rm -f "$out"
}

# The Prometheus data source the server's metrics land in.
ds="${GRAFANA_DATASOURCE_UID:-}"
if [ -z "$ds" ]; then
  ds=$(api GET /api/datasources | jq -r '[.[] | select(.type == "prometheus" and (.name | test("^grafanacloud-.+-prom$")))][0].uid // empty') \
    || die "couldn't list the data sources: is GRAFANA_SA_TOKEN an Admin service account's?"
  [ -n "$ds" ] || die "no grafanacloud-…-prom data source: set GRAFANA_DATASOURCE_UID in $ENV_FILE"
fi
say "data source $ds"

# The folder the rules live in.
if ! api GET "/api/folders/$FOLDER_UID" >/dev/null 2>&1; then
  api POST /api/folders "$(jq -n --arg uid "$FOLDER_UID" '{uid: $uid, title: "Sports Follow"}')" >/dev/null
fi

# The email contact point, made or brought up to date.
contact=$(jq -n --arg uid "$CONTACT_UID" --arg name "$CONTACT_NAME" --arg to "$ALERT_EMAIL" \
  '{uid: $uid, name: $name, type: "email", settings: {addresses: $to, singleEmail: true}, disableResolveMessage: false}')
if api GET /api/v1/provisioning/contact-points | jq -e --arg uid "$CONTACT_UID" 'any(.[]; .uid == $uid)' >/dev/null; then
  api PUT "/api/v1/provisioning/contact-points/$CONTACT_UID" "$contact" >/dev/null
else
  api POST /api/v1/provisioning/contact-points "$contact" >/dev/null
fi
say "contact point: email to $ALERT_EMAIL"

# Every alert goes there: grouped per alert, again every 6 hours while it lasts, and once when it clears.
api PUT /api/v1/provisioning/policies "$(jq -n --arg r "$CONTACT_NAME" \
  '{receiver: $r, group_by: ["grafana_folder", "alertname"], group_wait: "30s", group_interval: "5m", repeat_interval: "6h"}')" >/dev/null
say "notification policy: everything to $CONTACT_NAME, repeated every 6 hours"

# The rules: each query returns the current value, and its threshold (op, threshold) decides whether that's a
# problem, so a healthy server has data and its alerts resolve. Two groups:
#   server (rules.json)    the server's metrics. When they stop, a rule keeps its last state (KeepLast), so a
#                          silent server sends no false "Resolved" mails; only sf-server-down, whose query has
#                          data only while the server is silent, takes no data as OK.
#   app (app-rules.json)   the app's own metrics (sports_follow/metrics.py). No data means nothing to measure
#                          (no live game, no push), which is fine (OK); sf-app-silent catches the worker going quiet.
# put_group <name> <rules file> <no data, unless a rule says>
put_group() {
  local group
  group=$(jq --arg ds "$ds" --arg folder "$FOLDER_UID" --arg group "$1" --arg nodata "$3" '{
    title: $group, folderUid: $folder, interval: 60,
    rules: [ .[] | {
      uid, title, for, folderUID: $folder, ruleGroup: $group, condition: "C",
      noDataState: (.no_data // $nodata), execErrState: "Error",
      labels: {app: "sports-follow", area: $group},
      annotations: {summary: .summary},
      data: [
        {refId: "A", relativeTimeRange: {from: 900, to: 0}, datasourceUid: $ds,
         model: {refId: "A", expr: .expr, instant: true, range: false}},
        {refId: "C", relativeTimeRange: {from: 0, to: 0}, datasourceUid: "__expr__",
         model: {refId: "C", type: "threshold", expression: "A", conditions: [{evaluator: {type: .op, params: [.threshold]}}]}}
      ]
    } ]
  }' "$2")
  api PUT "/api/v1/provisioning/folder/$FOLDER_UID/rule-groups/$1" "$group" >/dev/null
  say "$(jq length "$2") rules in Sports Follow / $1, checked every minute"
}
put_group server "$HERE/rules.json" KeepLast
put_group app "$HERE/app-rules.json" OK

# The uptime check: two of Grafana's public probes, outside the server and India's nearest first, ask the
# app's /api/health every minute over HTTPS, as a fan's phone would. Everything inside the server can be up
# while fans can't reach it (DNS, Caddy, the certificate, the firewall); sf-unreachable in rules.json emails
# when no probe has had a healthy answer for 3 minutes. Two probes a minute is 86,400 checks a month, within
# the free tier's 100,000. Synthetic Monitoring is switched on once in the stack's Grafana (README.md,
# "Alerts"); until it is, this says so and the rest above still stands.
UPTIME_JOB=sports-follow-uptime
UPTIME_NEAR='["Mumbai", "Singapore", "Sydney", "Tokyo", "Frankfurt"]'
APP_URL="${APP_URL:-$(gh variable get APP_URL --env production 2>/dev/null || true)}"
sm=$(api GET /api/datasources | jq -r '[.[] | select(.type == "synthetic-monitoring-datasource")][0].uid // empty')
if [ -z "$APP_URL" ]; then
  say "uptime check skipped: no APP_URL (set it in $ENV_FILE, or as the production environment's variable)"
elif [ -z "$sm" ]; then
  say "uptime check skipped: switch on Synthetic Monitoring first ($GRAFANA_URL/a/grafana-synthetic-monitoring-app/home, Get started), then run this again"
else
  # The Synthetic Monitoring API, through its data source: Grafana adds the app's own token.
  smapi="/api/datasources/proxy/uid/$sm/sm"
  probes=$(api GET "$smapi/probe/list" | jq -c --argjson near "$UPTIME_NEAR" '
    [.[] | select(.public and .online and (.deprecated | not))] as $up
    | [($near[] as $n | $up[] | select(.name == $n)), $up[]]
    | reduce .[] as $p ([]; if any(.[]; .id == $p.id) then . else . + [$p] end)
    | .[:2]')
  [ "$(jq length <<<"$probes")" = 2 ] || die "Synthetic Monitoring lists fewer than two public probes online"
  target="${APP_URL%/}/api/health"
  check=$(jq -n --arg job "$UPTIME_JOB" --arg target "$target" --argjson probes "$probes" '{
    job: $job, target: $target, enabled: true, frequency: 60000, timeout: 10000,
    probes: [$probes[].id], labels: [], alertSensitivity: "none", basicMetricsOnly: true,
    settings: {http: {method: "GET", ipVersion: "V4", noFollowRedirects: true, failIfNotSSL: true, validStatusCodes: [200]}}
  }')
  old=$(api GET "$smapi/check/list" | jq -c --arg job "$UPTIME_JOB" '[.[] | select(.job == $job)][0] // empty')
  if [ -n "$old" ]; then
    api POST "$smapi/check/update" "$(jq -c --argjson old "$old" '. + {id: $old.id, tenantId: $old.tenantId}' <<<"$check")" >/dev/null
  else
    api POST "$smapi/check/add" "$check" >/dev/null
  fi
  say "uptime check: $target every minute from $(jq -r 'map(.name) | join(" and ")' <<<"$probes")"
fi

if [ -n "$TEST" ]; then
  # Grafana 13 (Grafana Cloud) tests a contact point through its receivers API; older ones through the
  # Alertmanager config API, which 13 removed.
  ns=$(api GET /api/frontend/settings | jq -r '.namespace // "default"')
  receivers="/apis/notifications.alerting.grafana.app/v1beta1/namespaces/$ns/receivers"
  if receiver=$(api GET "$receivers" 2>/dev/null | jq -r --arg t "$CONTACT_NAME" '[.items[] | select(.spec.title == $t)][0].metadata.name // empty') && [ -n "$receiver" ]; then
    api POST "$receivers/$receiver/test" "$(jq -n --arg uid "$CONTACT_UID" --arg to "$ALERT_EMAIL" \
      '{integration: {uid: $uid, type: "email", version: "v1", settings: {addresses: $to, singleEmail: true}},
        alert: {labels: {alertname: "Sports Follow test"}, annotations: {summary: "A test from deploy/grafana/apply-alerts.sh: Sports Follow alerts reach this address."}}}')" >/dev/null
  else
    api POST /api/alertmanager/grafana/config/api/v1/receivers/test "$(jq -n --arg uid "$CONTACT_UID" --arg name "$CONTACT_NAME" --arg to "$ALERT_EMAIL" \
      '{receivers: [{name: $name, grafana_managed_receiver_configs: [{uid: $uid, name: $name, type: "email", settings: {addresses: $to, singleEmail: true}}]}]}')" >/dev/null
  fi
  say "test email sent to $ALERT_EMAIL (from Grafana; check spam too)"
fi
