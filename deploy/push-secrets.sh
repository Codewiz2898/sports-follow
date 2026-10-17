#!/usr/bin/env bash
# From the Mac: copy the API tokens this app uses to the server, without printing any of them.
#   deploy/push-secrets.sh deploy@<server-ip>
# Sends ~/.config/sports-follow/{sportmonks-token,lichess-token}, pandascore-token, liquipedia-token, pubg-token,
# resend-key and google-client-secret (signing in), admin-password (the admin panel's password hash and
# authenticator key, made by python -m sports_follow.admin password and code), linear-key (a read-only Linear API key, for the
# admin panel's plan and issue count), fcm-service-account.json (the Android app's notifications), alert-url
# and heartbeat-url (the server checks, see README.md), offsite.env (the off-site backups), grafana.env (Grafana Cloud, for Alloy) and otlp.env (where the
# app's traces and logs go) if there are, and production's OpenRouter key from ~/.config/sports-follow/openrouter-key
# (or OPENROUTER_KEY_FILE=<file holding only a key>), falling back to the development key in the gateway's
# keys file. The server makes its own database password, gateway token, push key and fan cookie key
# (install-secrets.sh), then seals all of it for the password manager (seal-secrets.sh). Run again to rotate
# a token; restart after: docker compose -f deploy/compose.yaml up -d --force-recreate
set -euo pipefail
TARGET="${1:?usage: deploy/push-secrets.sh deploy@<server-ip>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
LOCAL="${SPORTS_FOLLOW_CONFIG:-$HOME/.config/sports-follow}"
KEYS="${LLM_PROVIDERS_KEYS_FILE:-$HOME/.config/llm-providers/keys.env}"

stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
umask 077
for f in sportmonks-token lichess-token; do
  [ -s "$LOCAL/$f" ] || { echo "push-secrets: $LOCAL/$f is missing or empty" >&2; exit 1; }
  cp "$LOCAL/$f" "$stage/$f"
done
for f in pandascore-token liquipedia-token pubg-token resend-key google-client-secret admin-password linear-key fcm-service-account.json alert-url heartbeat-url offsite.env grafana.env otlp.env; do
  [ -s "$LOCAL/$f" ] && cp "$LOCAL/$f" "$stage/$f"
done
PROD_KEY="${OPENROUTER_KEY_FILE:-$LOCAL/openrouter-key}"
if [ -s "$PROD_KEY" ]; then
  tr -d '[:space:]' <"$PROD_KEY" >"$stage/openrouter"
else
  sed -n 's/^OPENROUTER_API_KEY=//p' "$KEYS" | head -1 | tr -d "\"'[:space:]" >"$stage/openrouter"
  echo "push-secrets: using the Mac's development OpenRouter key; give production its own in $PROD_KEY (deploy/README.md, \"Secrets\")" >&2
fi
[ -s "$stage/openrouter" ] || { echo "push-secrets: no OpenRouter key found" >&2; exit 1; }
cp "$HERE/install-secrets.sh" "$HERE/seal-secrets.sh" "$HERE/secrets-recipients.txt" "$stage/"

tar --no-xattrs -C "$stage" -cf - . | ssh "$TARGET" 'tmp=$(mktemp -d) && tar -xf - -C "$tmp" && sudo bash "$tmp/install-secrets.sh" "$tmp"; status=$?; rm -rf "$tmp"; exit $status'
