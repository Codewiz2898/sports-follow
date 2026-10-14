#!/usr/bin/env bash
# From the Mac: put the app's dashboards (dashboards/*.json) into the stack's Grafana, in the Sports Follow
# folder the alert rules use (README.md, "Dashboards"). Safe to run again after changing one: each is
# replaced by what's here. A dashboard changed in Grafana's UI is kept by exporting its JSON over the file.
#   deploy/grafana/apply-dashboards.sh
# Settings from ~/.config/sports-follow/grafana-admin.env (GRAFANA_ADMIN_ENV=<file> for another), as
# apply-alerts.sh: GRAFANA_URL and GRAFANA_SA_TOKEN (an Admin service account's).
# The dashboards pick their Loki and Tempo data sources themselves (a variable each), so they need no ids.
# Also links each trace to its log lines in Explore (a correlation from the stack's Tempo to its Loki; a
# log line already links to its trace through the Loki data source's traceID field).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${GRAFANA_ADMIN_ENV:-$HOME/.config/sports-follow/grafana-admin.env}"
FOLDER_UID=sports-follow
die() { echo "apply-dashboards: $*" >&2; exit 1; }
say() { echo "apply-dashboards: $*"; }
[ -f "$ENV_FILE" ] || die "no $ENV_FILE (README.md, \"Alerts\")"
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a
: "${GRAFANA_URL:?set GRAFANA_URL in $ENV_FILE}" "${GRAFANA_SA_TOKEN:?set GRAFANA_SA_TOKEN in $ENV_FILE}"
command -v jq >/dev/null || die "needs jq"
GRAFANA_URL="${GRAFANA_URL%/}"

# api <method> <path> [json body]: the response body; fails with Grafana's message on an error status.
api() {
  local out code
  out=$(mktemp)
  code=$(curl -sS -o "$out" -w '%{http_code}' -X "$1" "$GRAFANA_URL$2" \
    -H "Authorization: Bearer $GRAFANA_SA_TOKEN" -H 'Content-Type: application/json' \
    ${3:+--data-binary "$3"}) || { rm -f "$out"; die "$1 $2: Grafana didn't answer"; }
  if [ "${code:0:1}" != 2 ]; then
    echo "apply-dashboards: $1 $2: HTTP $code: $(head -c 400 "$out")" >&2
    rm -f "$out"
    return 1
  fi
  cat "$out"
  rm -f "$out"
}

if ! api GET "/api/folders/$FOLDER_UID" >/dev/null 2>&1; then
  api POST /api/folders "$(jq -n --arg uid "$FOLDER_UID" '{uid: $uid, title: "Sports Follow"}')" >/dev/null \
    || die "couldn't make the Sports Follow folder: is GRAFANA_SA_TOKEN an Admin service account's?"
fi

# A trace's log lines, from the trace: made or brought up to date.
tempo=$(api GET /api/datasources | jq -r '[.[] | select(.type == "tempo")][0].uid // empty')
loki=$(api GET /api/datasources | jq -r '[.[] | select(.type == "loki" and (.name | test("alert-state-history|usage") | not))][0].uid // empty')
if [ -n "$tempo" ] && [ -n "$loki" ]; then
  label="Log lines of this trace"
  correlation=$(jq -n --arg loki "$loki" --arg label "$label" '{targetUID: $loki, label: $label, type: "query",
    description: "Sports Follow: the API'"'"'s and worker'"'"'s log lines in this trace (deploy/grafana/apply-dashboards.sh)",
    config: {field: "traceID", target: {expr: "{service_name=~\"sports-follow.*\"} | trace_id=\"${traceID}\"", queryType: "range"}}}')
  existing=$(api GET "/api/datasources/uid/$tempo/correlations" 2>/dev/null | jq -r --arg label "$label" '[.[]? | select(.label == $label)][0].uid // empty' || true)
  if [ -n "$existing" ]; then
    api PATCH "/api/datasources/uid/$tempo/correlations/$existing" "$(jq 'del(.targetUID)' <<<"$correlation")" >/dev/null
  else
    api POST "/api/datasources/uid/$tempo/correlations" "$correlation" >/dev/null
  fi
  say "correlation: $label (Tempo $tempo to Loki $loki)"
else
  say "no Tempo or Loki data source: no link from traces to their log lines"
fi

for file in "$HERE"/dashboards/*.json; do
  body=$(jq -c --arg folder "$FOLDER_UID" '{dashboard: (. + {id: null}), folderUid: $folder, overwrite: true, message: "deploy/grafana/apply-dashboards.sh"}' "$file")
  url=$(api POST /api/dashboards/db "$body" | jq -r .url) || die "$(basename "$file") wasn't taken"
  say "$(jq -r .title "$file"): $GRAFANA_URL$url"
done
