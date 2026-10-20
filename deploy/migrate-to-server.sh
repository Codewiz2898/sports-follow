#!/usr/bin/env bash
# From the Mac, in the repo with the development stack running: move Sports Follow off the Mac onto a
# server that server-setup.sh has set up, in one go (README.md, "Moving off the Mac"). Each step is one of
# the scripts here, and the whole of it is safe to run again.
#   deploy/migrate-to-server.sh deploy@<server-ip> [--check]   (--check: step 1 only, which changes nothing)
# 1. Checks: the tokens push-secrets.sh needs, the Mac's database with the registry, the server set up.
# 2. Secrets (push-secrets.sh): the API tokens; the server makes its own keys, and seals all of it.
# 3. The first deploy (deploy.sh, on the server): build, migrations, start, wait until healthy.
# 4. The registry (seed-registry.sh): the Mac's athletes, instead of an hour of Wikidata queries there.
# 5. Checks against the live site: healthy, no development-only source (no ESPN in the credits), the registry.
# Not moved, on purpose: the Mac's games, results and news (much of it from ESPN, which only development
# may use), and its fans and follows (development cookies for localhost, which no browser sends to the
# server's address). The server builds pages again as fans follow.
set -euo pipefail
TARGET="${1:?usage: deploy/migrate-to-server.sh deploy@<server-ip> [--check]}"
CHECK_ONLY=""
case "${2:-}" in --check) CHECK_ONLY=1 ;; "") ;; *) echo "usage: deploy/migrate-to-server.sh deploy@<server-ip> [--check]" >&2; exit 2 ;; esac
HERE="$(cd "$(dirname "$0")" && pwd)"
LOCAL="${SPORTS_FOLLOW_CONFIG:-$HOME/.config/sports-follow}"
REMOTE_DB='cd /opt/sports-follow && docker compose -f deploy/compose.yaml exec -T postgres psql -At -U sports -d sports_follow'

step() { printf '\n== %s\n' "$*"; }
say() { echo "migrate: $*"; }
die() { echo "migrate: $*" >&2; exit 1; }

step "1/5 Checks"
[ -s "$LOCAL/sportmonks-token" ] || die "no Sportmonks token in $LOCAL/sportmonks-token"
[ -s "$LOCAL/lichess-token" ] || die "no Lichess token in $LOCAL/lichess-token: make one (scope study:read) at https://lichess.org/account/oauth/token/create, then: pbpaste > $LOCAL/lichess-token && chmod 600 $LOCAL/lichess-token"
if [ -z "${OPENROUTER_KEY_FILE:-}" ] && [ ! -s "$LOCAL/openrouter-key" ]; then
  say "no production OpenRouter key ($LOCAL/openrouter-key): the server gets the Mac's development key for now (README.md, \"Secrets\")"
fi
# The development stack's database (docker-compose.yml here, as seed-registry.sh reads it).
[ -f docker-compose.yml ] || die "run this from the repo's root, where the development stack runs"
mac_athletes=$(docker compose exec -T postgres psql -At -U sports -d sports_follow -c 'select count(*) from athlete' 2>/dev/null) \
  || die "the Mac's database isn't answering: docker compose up -d postgres"
[ "${mac_athletes:-0}" -gt 0 ] || die "the Mac's registry is empty, so there's nothing to copy"
domain=$(ssh -o BatchMode=yes "$TARGET" "sed -n 's/^DOMAIN=//p' /opt/sports-follow/deploy/.env" 2>/dev/null) \
  || die "can't log in as $TARGET, or the server isn't set up: deploy/server-setup.sh (README.md, step 2)"
[ -n "$domain" ] || die "the server has no DOMAIN in /opt/sports-follow/deploy/.env: run deploy/server-setup.sh again"
say "Mac: $mac_athletes athletes in the registry; server: $TARGET, the app at https://$domain"
if [ -n "$CHECK_ONLY" ]; then
  say "ready to migrate (--check: nothing was sent or changed)"
  exit 0
fi

step "2/5 Secrets"
"$HERE/push-secrets.sh" "$TARGET"

step "3/5 First deploy (the build takes a few minutes)"
ssh -o BatchMode=yes "$TARGET" /opt/sports-follow/deploy/deploy.sh deploy

step "4/5 Registry"
"$HERE/seed-registry.sh" "$TARGET"

step "5/5 Checks"
# Caddy gets the certificate on the first request, which can take a little while.
health=""
for _ in $(seq 1 24); do
  health=$(curl -fsS -m 10 "https://$domain/api/health" 2>/dev/null) && break
  sleep 5
done
case "$health" in
  *'"ok":true'*) say "health: $health" ;;
  *) die "https://$domain/api/health didn't answer as healthy (${health:-no answer}): is HTTPS (TCP 443) open in the server's firewall? Then: ssh $TARGET 'cd /opt/sports-follow && docker compose -f deploy/compose.yaml logs caddy api'" ;;
esac
credits=$(curl -fsS -m 10 "https://$domain/api/credits") || die "https://$domain/api/credits didn't answer"
if grep -qi espn <<<"$credits"; then
  die "the credits name ESPN, so the server runs a development-only source: check SPORTS_FOLLOW_SOURCES in deploy/settings.env"
fi
say "sources: no development-only source (no ESPN in the credits)"
server_athletes=$(ssh -o BatchMode=yes "$TARGET" "$REMOTE_DB -c 'select count(*) from athlete'")
[ "$server_athletes" = "$mac_athletes" ] || die "the server has $server_athletes athletes, the Mac $mac_athletes"
say "registry: $server_athletes athletes, the same as the Mac"

printf '\n'
say "done: https://$domain"
say "next (README.md): open the app and search; turn on the alerts and the off-site backups; set up the GitHub deploys;"
say "keep a sealed copy of the secrets (deploy/backup-secrets.sh $TARGET); check the provider's browser SSH once."
