#!/usr/bin/env bash
# The server checks, every 5 minutes as deploy (systemd/sports-follow-check.timer): disk, memory, the
# out-of-memory killer, the nightly backup, the containers and the HTTPS certificate. A problem is sent to
# ALERT_URL when it starts, again every REPEAT_HOURS while it lasts, and once more when it clears.
# ALERT_URL takes a plain-text POST: an ntfy.sh topic (a push to your phone) or a Healthchecks.io /fail URL.
# HEARTBEAT_URL, if set, gets a GET on every run, so a service like Healthchecks.io can tell you when the
# checks stop (the server is down). Both come from /etc/sports-follow/alerts.env (push-secrets.sh).
# By hand: sudo systemctl start sports-follow-check; sudo journalctl -u sports-follow-check
set -uo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE="${STATE_DIRECTORY:-${XDG_STATE_HOME:-$HOME/.local/state}/sports-follow-check}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/sports-follow}"
PROJECT="${COMPOSE_PROJECT:-sports-follow}"
SERVICES="${SERVICES:-caddy api worker gateway postgres redis backup}"
DISK_MAX_PCT="${DISK_MAX_PCT:-80}"
BACKUP_MAX_HOURS="${BACKUP_MAX_HOURS:-26}"
MEMORY_PRESSURE_MAX="${MEMORY_PRESSURE_MAX:-10}"   # % of the last 5 minutes that tasks waited for memory
MEMORY_FREE_MIN_PCT="${MEMORY_FREE_MIN_PCT:-5}"    # where the kernel doesn't report that
RESTARTS_MAX="${RESTARTS_MAX:-3}"                  # per container, per hour
CERT_MIN_DAYS="${CERT_MIN_DAYS:-10}"
REPEAT_HOURS="${REPEAT_HOURS:-6}"
DOMAIN="${DOMAIN:-$(sed -n 's/^DOMAIN=//p' "$APP_DIR/deploy/.env" 2>/dev/null)}"
HTTPS_PORT="${HTTPS_PORT:-443}"
mkdir -p "$STATE"
now=$(date +%s)

problems=$(mktemp)
events=$(mktemp)
trap 'rm -f "$problems" "$events"' EXIT
problem() { printf '%s\t%s\n' "$1" "$2" >>"$problems"; }   # a state: alerted when it starts and when it clears
event() { echo "$1" >>"$events"; }                         # something that happened: alerted once

check_disk() {
  local line pct mount avail
  # The disks holding the system, Docker and the backups (each one once).
  while read -r line; do
    read -r pct avail mount <<<"$line"
    [ "$pct" -ge "$DISK_MAX_PCT" ] && problem "disk:$mount" "Disk $mount is $pct% full ($avail free)"
  done < <(df -P -h / /var/lib/docker "$BACKUP_DIR" 2>/dev/null | awk 'NR > 1 && !seen[$1]++ { print $5 + 0, $4, $6 }')
}

check_memory() {
  local avg300 avail_kb total_kb free last
  avail_kb=$(awk '/^MemAvailable:/ { print $2 }' /proc/meminfo)
  total_kb=$(awk '/^MemTotal:/ { print $2 }' /proc/meminfo)
  free="$((avail_kb / 1024)) of $((total_kb / 1024)) MB free"
  if [ -r /proc/pressure/memory ]; then
    # Pressure stall information: the share of the last 5 minutes that tasks spent waiting for memory.
    avg300=$(awk '$1 == "some" { split($4, a, "="); print int(a[2]) }' /proc/pressure/memory)
    [ "$avg300" -gt "$MEMORY_PRESSURE_MAX" ] && problem memory "Memory is short: tasks waited for it $avg300% of the last 5 minutes ($free)"
  elif [ $((avail_kb * 100 / total_kb)) -lt "$MEMORY_FREE_MIN_PCT" ]; then
    # Kernels without it: little memory left right now.
    problem memory "Memory is short: $free"
  fi
  # The kernel's count of out-of-memory kills since boot (saved once the alert is out).
  oom_kills=$(awk '$1 == "oom_kill" { print $2 }' /proc/vmstat)
  last=$(cat "$STATE/oom_kill" 2>/dev/null || echo "$oom_kills")
  [ "${oom_kills:-0}" -gt "$last" ] && event "The out-of-memory killer stopped $((oom_kills - last)) process(es) since the last check (sudo journalctl -k | grep -i oom)"
}

check_backup() {
  local newest age
  newest=$(find "$BACKUP_DIR" -maxdepth 1 -name 'sports_follow-*.dump' -printf '%T@\n' 2>/dev/null | sort -n | tail -1)
  if [ -z "$newest" ]; then
    problem backup "No database backup in $BACKUP_DIR"
    return
  fi
  age=$(( (now - ${newest%.*}) / 3600 ))
  [ "$age" -ge "$BACKUP_MAX_HOURS" ] && problem backup "The newest database backup is $age hours old (docker compose -f deploy/compose.yaml logs backup)"
}

check_containers() {
  local seen service status count health ids
  ids=$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT" 2>/dev/null) || { problem docker "Docker isn't answering"; return; }
  if [ -z "$ids" ]; then
    problem app "The app isn't running: no containers (deploy/deploy.sh deploy)"
    return
  fi
  seen=$(mktemp)
  # shellcheck disable=SC2086
  docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}} {{.State.Status}} {{.RestartCount}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' $ids >"$seen"
  touch "$STATE/restarts"
  for service in $SERVICES; do
    read -r _ status count health < <(awk -v s="$service" '$1 == s' "$seen" | sort -r -k2,2 | head -1) || true
    if [ -z "${status:-}" ]; then
      problem "container:$service" "$service isn't running (no container)"
    elif [ "$status" != running ]; then
      problem "container:$service" "$service is $status"
    elif [ "${health:-}" = unhealthy ]; then
      problem "container:$service" "$service is unhealthy (docker compose -f deploy/compose.yaml logs $service)"
    fi
    # Restarts in the last hour: now against the lowest count seen within the hour (a deploy starts it at 0).
    if [ -n "${status:-}" ]; then
      echo "$now $service $count" >>"$STATE/restarts"
      local low
      low=$(awk -v s="$service" -v since=$((now - 3600)) '$2 == s && $1 >= since { if (low == "" || $3 < low) low = $3 } END { print low }' "$STATE/restarts")
      [ $((count - low)) -ge "$RESTARTS_MAX" ] && problem "restarts:$service" "$service restarted $((count - low)) times in the last hour"
    fi
    status="" count="" health=""
  done
  awk -v since=$((now - 3600)) '$1 >= since' "$STATE/restarts" >"$STATE/restarts.new" && mv "$STATE/restarts.new" "$STATE/restarts"
  rm -f "$seen"
}

check_certificate() {
  local cert
  [ -n "$DOMAIN" ] || return 0
  # The certificate Caddy serves, checked as a phone would: trusted, for this name, and not about to expire.
  if ! cert=$(openssl s_client -connect "127.0.0.1:$HTTPS_PORT" -servername "$DOMAIN" -verify_hostname "$DOMAIN" \
      -verify_return_error </dev/null 2>/dev/null | openssl x509 2>/dev/null) || [ -z "$cert" ]; then
    problem cert "No trusted HTTPS certificate for $DOMAIN (docker compose -f deploy/compose.yaml logs caddy)"
  elif ! openssl x509 -noout -checkend $((CERT_MIN_DAYS * 86400)) <<<"$cert" >/dev/null; then
    problem cert "The HTTPS certificate for $DOMAIN expires $(openssl x509 -noout -enddate <<<"$cert" | cut -d= -f2) and hasn't renewed"
  fi
}

check_disk
check_memory
check_backup
check_containers
check_certificate

# Compare with the last run: new problems, ones still open REPEAT_HOURS after the last alert, cleared ones.
touch "$STATE/open"
message=$(mktemp)
open_next=$(mktemp)
trap 'rm -f "$problems" "$events" "$message" "$open_next"' EXIT
alerts=0
while IFS=$'\t' read -r key text; do
  last=$(awk -F'\t' -v k="$key" '$1 == k { print $2 }' "$STATE/open")
  if [ -z "$last" ]; then
    echo "• $text" >>"$message"; alerts=$((alerts + 1)); last=$now
  elif [ $((now - last)) -ge $((REPEAT_HOURS * 3600)) ]; then
    echo "• Still: $text" >>"$message"; alerts=$((alerts + 1)); last=$now
  else
    echo "  Still open: $text" >>"$message.open"
  fi
  printf '%s\t%s\t%s\n' "$key" "$last" "$text" >>"$open_next"
done <"$problems"
while read -r text; do echo "• $text" >>"$message"; alerts=$((alerts + 1)); done <"$events"
while IFS=$'\t' read -r key _ text; do
  awk -F'\t' -v k="$key" '$1 == k { found = 1 } END { exit !found }' "$problems" || echo "✓ Cleared: $text" >>"$message"
done <"$STATE/open"
# Anything sent also lists what's still open from before, so each alert tells the whole story.
[ -s "$message" ] && [ -s "$message.open" ] && cat "$message.open" >>"$message"
rm -f "$message.open"

sent=1
if [ -s "$message" ]; then
  host=$(hostname)
  open_count=$(wc -l <"$problems" | tr -d ' ')
  if [ "$alerts" -gt 0 ]; then
    title="$host: $((open_count + $(wc -l <"$events"))) problem(s)"; priority=high; tags=warning
  elif [ "$open_count" -gt 0 ]; then
    title="$host: $open_count problem(s) left"; priority=default; tags=white_check_mark
  else
    title="$host: all clear"; priority=default; tags=white_check_mark
  fi
  echo "$title"; cat "$message"
  if [ -z "${ALERT_URL:-}" ]; then
    echo "server-check: ALERT_URL isn't set, so this went to the journal only" >&2
  elif ! curl -fsS -m 20 --retry 3 -o /dev/null -H "Title: $title" -H "Priority: $priority" -H "Tags: $tags" \
      --data-binary @"$message" "$ALERT_URL"; then
    echo "server-check: couldn't reach ALERT_URL; the same alert goes out next run" >&2
    sent=0
  fi
fi
# Saved only once the alert is out, so a failed one is sent again.
if [ "$sent" = 1 ]; then
  mv "$open_next" "$STATE/open"
  echo "${oom_kills:-0}" >"$STATE/oom_kill"
fi
[ -z "${HEARTBEAT_URL:-}" ] || curl -fsS -m 10 --retry 3 -o /dev/null "$HEARTBEAT_URL" || echo "server-check: couldn't reach HEARTBEAT_URL" >&2
[ "$sent" = 1 ]
