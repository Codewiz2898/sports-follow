#!/usr/bin/env bash
# Runs on the server, called by push-secrets.sh with a directory holding the Mac's tokens. Third-party
# tokens are replaced; the server's own secrets (database password, gateway token, push key) are made
# once and kept, because changing them would lock out the database or every push subscription.
set -euo pipefail
umask 077
in="$1"
D=/etc/sports-follow
mkdir -p "$D/files"
chmod 700 "$D"

install -m 400 "$in/sportmonks-token" "$D/files/sportmonks-token"
install -m 400 "$in/lichess-token" "$D/files/lichess-token"
[ -f "$D/files/vapid-private.pem" ] || openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$D/files/vapid-private.pem"
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
chmod 600 "$D"/*.env
echo "install-secrets: $D updated (Sportmonks, Lichess and OpenRouter replaced; database password, gateway token and push key kept)"
