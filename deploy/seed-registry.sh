#!/usr/bin/env bash
# From the Mac (repo root, local stack running): copy the player registry (about 320,000 athletes,
# with the source ids already matched) to the server, instead of an hour of Wikidata queries there.
#   deploy/seed-registry.sh root@<server-ip>
# The server's registry is replaced. Its weekly import (Mondays 03:30 UTC) keeps it current after.
set -euo pipefail
TARGET="${1:?usage: deploy/seed-registry.sh root@<server-ip>}"
REMOTE='cd /opt/sports-follow && docker compose -f deploy/compose.yaml exec -T postgres'
docker compose exec -T postgres pg_dump -U sports -d sports_follow --data-only --table=athlete --format=custom \
  | ssh "$TARGET" "$REMOTE psql -q -U sports -d sports_follow -c 'truncate athlete' && $REMOTE pg_restore -U sports -d sports_follow --data-only --no-owner"
ssh "$TARGET" "$REMOTE psql -At -U sports -d sports_follow -c 'select count(*) from athlete'" | sed 's/^/seed-registry: athletes on the server: /'
