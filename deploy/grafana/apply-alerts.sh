#!/usr/bin/env bash
# From the Mac: put the server's alert rules (rules.json) into Grafana Cloud, with an email contact point and
# the notification policy that sends every alert there (README.md, "Alerts"). Safe to run again after
# changing rules.json: everything is replaced by what's here.
#   deploy/grafana/apply-alerts.sh            the folder, contact point, policy and rules
#   deploy/grafana/apply-alerts.sh --test     also send a test email through the contact point
# Settings from ~/.config/sports-follow/grafana-admin.env (GRAFANA_ADMIN_ENV=<file> for another):
#   GRAFANA_URL       the stack's Grafana, https://<stack>.grafana.net
#   GRAFANA_SA_TOKEN  a service account token with the Admin role
#   ALERT_EMAIL       where alerts go (several: comma-separated)
#   GRAFANA_DATASOURCE_UID  optional: the Prometheus data source, if it isn't the stack's grafanacloud-…-prom
# The notification policy replaces the stack's whole policy tree: fine for a stack that only alerts on this.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${GRAFANA_ADMIN_ENV:-$HOME/.config/sports-follow/grafana-admin.env}"
FOLDER_UID=sports-follow
GROUP=server
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

# The rules: each query returns a series, with a value above 0, only while something is wrong (rules.json).
# When the metrics stop, a rule keeps its last state (KeepLast), so a silent server doesn't send false
# "Resolved" mails; only sf-server-down, whose query has data only while the server is silent, takes no data as OK.
group=$(jq --arg ds "$ds" --arg folder "$FOLDER_UID" --arg group "$GROUP" '{
  title: $group, folderUid: $folder, interval: 60,
  rules: [ .[] | {
    uid, title, for, folderUID: $folder, ruleGroup: $group, condition: "C",
    noDataState: (.no_data // "KeepLast"), execErrState: "Error",
    labels: {app: "sports-follow"},
    annotations: {summary: .summary},
    data: [
      {refId: "A", relativeTimeRange: {from: 900, to: 0}, datasourceUid: $ds,
       model: {refId: "A", expr: .expr, instant: true, range: false}},
      {refId: "C", relativeTimeRange: {from: 0, to: 0}, datasourceUid: "__expr__",
       model: {refId: "C", type: "threshold", expression: "A", conditions: [{evaluator: {type: "gt", params: [0]}}]}}
    ]
  } ]
}' "$HERE/rules.json")
api PUT "/api/v1/provisioning/folder/$FOLDER_UID/rule-groups/$GROUP" "$group" >/dev/null
say "$(jq length "$HERE/rules.json") rules in Sports Follow / $GROUP, checked every minute"

if [ -n "$TEST" ]; then
  api POST /api/alertmanager/grafana/config/api/v1/receivers/test "$(jq -n --arg uid "$CONTACT_UID" --arg name "$CONTACT_NAME" --arg to "$ALERT_EMAIL" \
    '{receivers: [{name: $name, grafana_managed_receiver_configs: [{uid: $uid, name: $name, type: "email", settings: {addresses: $to, singleEmail: true}}]}]}')" >/dev/null
  say "test email sent to $ALERT_EMAIL"
fi
