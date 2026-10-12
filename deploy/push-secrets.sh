#!/usr/bin/env bash
# From the Mac: copy the API tokens this app uses to the server, without printing any of them.
#   deploy/push-secrets.sh root@<server-ip>
# Sends ~/.config/sports-follow/{sportmonks-token,lichess-token} and OPENROUTER_API_KEY from the gateway's
# keys file (OPENROUTER_KEY_FILE=<file holding only a key> to give production its own key). The server
# makes its own database password, gateway token and push key (install-secrets.sh). Run again to rotate
# a token; restart after: docker compose -f deploy/compose.yaml up -d --force-recreate
set -euo pipefail
TARGET="${1:?usage: deploy/push-secrets.sh root@<server-ip>}"
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
if [ -n "${OPENROUTER_KEY_FILE:-}" ]; then
  tr -d '[:space:]' <"$OPENROUTER_KEY_FILE" >"$stage/openrouter"
else
  sed -n 's/^OPENROUTER_API_KEY=//p' "$KEYS" | head -1 | tr -d "\"'[:space:]" >"$stage/openrouter"
fi
[ -s "$stage/openrouter" ] || { echo "push-secrets: no OpenRouter key found" >&2; exit 1; }
cp "$HERE/install-secrets.sh" "$stage/"

tar -C "$stage" -cf - . | ssh "$TARGET" 'tmp=$(mktemp -d) && tar -xf - -C "$tmp" && bash "$tmp/install-secrets.sh" "$tmp"; status=$?; rm -rf "$tmp"; exit $status'
