#!/usr/bin/env bash
# From the Mac (repo root, local stack running): copy the player registry (about 320,000 athletes,
# with the source ids already matched) to the server, instead of an hour of Wikidata queries there.
#   deploy/seed-registry.sh deploy@<server-ip> [--replace]
# For a new server: its weekly import (Mondays 03:30 UTC) keeps the registry current after. A server whose
# registry already has Wikidata's athletes is left alone, unless --replace: then the Mac's replaces it, and
# what only the server had goes (athletes added with `players add`, ids it learned from the sources), though
# those its operators removed stay out. One transaction either way: a copy that fails part way changes nothing.
set -euo pipefail
usage="usage: deploy/seed-registry.sh deploy@<server-ip> [--replace]"
TARGET="${1:?$usage}"
REPLACE=""
case "${2:-}" in --replace) REPLACE=1 ;; "") ;; *) echo "$usage" >&2; exit 2 ;; esac
REMOTE='cd /opt/sports-follow && docker compose -f deploy/compose.yaml exec -T postgres'
held=$(ssh "$TARGET" "$REMOTE psql -At -U sports -d sports_follow -c \"select count(*) from athlete where qid like 'Q%'\"")
if [ "${held:-0}" -gt 0 ] && [ -z "$REPLACE" ]; then
  echo "seed-registry: the server's registry already has $held athletes from Wikidata, so it's left as it is (its weekly import keeps it current). To replace it with the Mac's anyway: deploy/seed-registry.sh $TARGET --replace" >&2
  exit 1
fi
mac=$(docker compose exec -T postgres psql -At -U sports -d sports_follow -c 'select count(*) from athlete')
[ "${mac:-0}" -gt 0 ] || { echo "seed-registry: the Mac's registry is empty, so there's nothing to copy" >&2; exit 1; }
# Emptied, copied and the removed taken out again in one transaction (psql -1), which stops at the first error. A copy
# cut short (the connection lost) is caught by the count: fewer rows than the Mac's divide by zero, and nothing changes.
# The dump sets an empty search_path, hence public. after it. A removal is by registry id in every sport: one removed
# under a group ("esports") is out of each of its games; another sport of the same person comes back with the next import.
docker compose exec -T postgres pg_dump -U sports -d sports_follow --data-only --table=athlete --format=plain \
  | ssh -C "$TARGET" "$REMOTE psql -q -v ON_ERROR_STOP=1 --single-transaction -U sports -d sports_follow -c 'truncate public.athlete' -f - \
      -c 'select 1 / (count(*) = $mac)::int from public.athlete' \
      -c \"delete from public.athlete a using public.athlete_override o where o.action = 'remove' and o.qid = a.qid\"" >/dev/null
ssh "$TARGET" "$REMOTE psql -At -U sports -d sports_follow -c 'select count(*) from athlete'" | sed 's/^/seed-registry: athletes on the server: /'
