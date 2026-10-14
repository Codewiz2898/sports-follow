#!/usr/bin/env bash
# Runs on the server as root (push-secrets.sh calls it with sudo) with a directory holding the Mac's tokens. Third-party
# tokens are replaced; the server's own secrets (database password, gateway token, push key, fan cookie
# key) are made once and kept, because changing them would lock out the database, every push
# subscription or every fan (their cookies would no longer verify, and their follows would be lost).
set -euo pipefail
umask 077
in="$1"
D=/etc/sports-follow
mkdir -p "$D/files"
# deploy runs docker compose, which reads the .env files; the token files are for the containers only.
grp=$(getent group deploy >/dev/null && echo deploy || echo root)
chown "root:$grp" "$D"
chmod 750 "$D"

install -m 400 "$in/sportmonks-token" "$D/files/sportmonks-token"
install -m 400 "$in/lichess-token" "$D/files/lichess-token"
for f in pandascore-token liquipedia-token pubg-token resend-key google-client-secret; do
  [ -f "$in/$f" ] && install -m 400 "$in/$f" "$D/files/$f"
done
[ -f "$D/files/vapid-private.pem" ] || openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$D/files/vapid-private.pem"
[ -f "$D/files/cookie-secret" ] || openssl rand -hex 32 >"$D/files/cookie-secret"
chmod 400 "$D/files"/*
chown -R 10001:10001 "$D/files"   # the app containers' user
chmod 500 "$D/files"

if [ ! -f "$D/postgres.env" ]; then
  pg=$(openssl rand -hex 24)
  printf 'POSTGRES_PASSWORD=%s\n' "$pg" >"$D/postgres.env"
  printf 'DATABASE_URL=postgresql+psycopg://sports:%s@postgres:5432/sports_follow\n' "$pg" >"$D/app.env"
fi
grep -q '^SPORTS_FOLLOW_GATEWAY_TOKEN=' "$D/app.env" || printf 'SPORTS_FOLLOW_GATEWAY_TOKEN=%s\n' "$(openssl rand -hex 32)" >>"$D/app.env"
token=$(grep '^SPORTS_FOLLOW_GATEWAY_TOKEN=' "$D/app.env" | cut -d= -f2-)
printf 'OPENROUTER_API_KEY=%s\nSPORTS_FOLLOW_GATEWAY_TOKEN=%s\n' "$(cat "$in/openrouter")" "$token" >"$D/gateway.env"
# Where the server checks send alerts (deploy/server-check.sh): kept unless new ones are sent.
if [ -f "$in/alert-url" ] || [ -f "$in/heartbeat-url" ]; then
  : >"$D/alerts.env"
  if [ -f "$in/alert-url" ]; then printf 'ALERT_URL=%s\n' "$(cat "$in/alert-url")" >>"$D/alerts.env"; fi
  if [ -f "$in/heartbeat-url" ]; then printf 'HEARTBEAT_URL=%s\n' "$(cat "$in/heartbeat-url")" >>"$D/alerts.env"; fi
fi
# Where the off-site backups go and the storage's keys (deploy/offsite-backup.sh): kept unless a new one is sent.
if [ -f "$in/offsite.env" ]; then install -m 640 "$in/offsite.env" "$D/offsite.env"; fi
# Grafana Cloud's metrics settings for Alloy (deploy/alloy/): kept unless a new one is sent, which (re)starts it.
grafana_new=""
if [ -f "$in/grafana.env" ]; then install -m 640 "$in/grafana.env" "$D/grafana.env"; grafana_new=1; fi
chown "root:$grp" "$D"/*.env
chmod 640 "$D"/*.env
echo "install-secrets: $D updated (Sportmonks, Lichess and OpenRouter replaced; database password, gateway token, push key and cookie key kept)"
if [ -n "$grafana_new" ] && systemctl cat alloy >/dev/null 2>&1; then
  if systemctl restart alloy; then echo "install-secrets: Alloy (re)started with grafana.env: metrics go to Grafana Cloud"
  else echo "install-secrets: Alloy didn't start: sudo journalctl -u alloy" >&2; fi
fi

# A sealed copy of all of it, for the password manager and the off-site backups (seal-secrets.sh). Exit 3
# means secrets-recipients.txt has no key yet, which it has said.
if [ -f "$in/seal-secrets.sh" ]; then
  bash "$in/seal-secrets.sh" seal "$in/secrets-recipients.txt" || [ $? -eq 3 ]
fi
