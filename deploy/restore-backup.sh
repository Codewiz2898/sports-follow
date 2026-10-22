#!/usr/bin/env bash
# On the server, as deploy, in /opt/sports-follow: put a dump (backup.sh's, or one fetch-backup.sh brought back)
# in place of the database, then bring it up to the code with the migrations.
#   deploy/restore-backup.sh /var/backups/sports-follow/<file>.dump
# The site is down while it runs: the API, worker, curator and backup job stop first. The dump loads into a new,
# empty database beside the live one, and the two swap names only once all of it is in. (pg_restore --clean over
# the live one drops only what the dump has: after a migration that added a table, that table's keys kept the
# player table from going, and the migrations then found it already there.) If the load or the migrations fail,
# the live database is as it was and everything starts again on it. After a restore, the database it replaced is
# kept as sports_follow_before_<time> until you drop it, and Redis forgets signed-in sessions and the cards' hot
# copies, which name that database's rows.
set -euo pipefail
DUMP="${1:?usage: deploy/restore-backup.sh <file>.dump}"
if [ ! -f "$DUMP" ] || [ ! -r "$DUMP" ]; then echo "restore: can't read $DUMP" >&2; exit 1; fi
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# A rehearsal points these at a stand-in for compose and a scratch database (README.md, "Rehearsing locally").
read -ra COMPOSE <<< "${RESTORE_COMPOSE:-docker compose -f deploy/compose.yaml}"
DB="${RESTORE_DB:-sports_follow}"
[[ "$DB" =~ ^[a-z_][a-z0-9_]*$ ]] || { echo "restore: not a database name: $DB" >&2; exit 1; }
SERVICES=(api worker curator backup)   # everything that writes to the database
STAMP="$(date -u +%Y%m%d_%H%M)"
LOADING="${DB}_loading"
BEFORE="${DB}_before_${STAMP}"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/sports-follow"

say() { echo "restore: $*" >&2; }
c() { "${COMPOSE[@]}" "$@"; }
sql() { c exec -T postgres psql -X -q -v ON_ERROR_STOP=1 -U sports -d postgres "$@"; }
# Renames the live database to $1 and $2 to the live one's name, in one transaction: either both happen or
# neither. Nothing should be connected now; anything that is (a psql left open) is ended first.
swap() {
  sql >/dev/null <<SQL
SELECT pg_terminate_backend(pid, 5000) FROM pg_stat_activity WHERE datname IN ('$DB', '$2') AND pid <> pg_backend_pid();
BEGIN;
ALTER DATABASE "$DB" RENAME TO "$1";
ALTER DATABASE "$2" RENAME TO "$DB";
COMMIT;
SQL
}

# Not during a deploy or a rollback (deploy.sh takes the same lock), and only a dump that reads as one.
mkdir -p "$STATE"
exec 9>"$STATE/lock"
flock -n 9 || { say "a deploy or rollback is running; try again after it"; exit 1; }
c exec -T postgres pg_restore --list < "$DUMP" > /dev/null || { say "$DUMP isn't a dump pg_restore can read; nothing changed"; exit 1; }

say "stopping ${SERVICES[*]}: the site is down until the end"
c stop "${SERVICES[@]}"
trap 'say "starting ${SERVICES[*]} again"; c start "${SERVICES[@]}"' EXIT

say "loading $DUMP into a new database, $LOADING"
sql -c "SET client_min_messages = warning" -c "DROP DATABASE IF EXISTS $LOADING" -c "CREATE DATABASE $LOADING"
if ! c exec -T postgres pg_restore -U sports -d "$LOADING" --no-owner --exit-on-error < "$DUMP"; then
  sql -c "DROP DATABASE $LOADING"
  say "the dump didn't load all the way; $DB is as it was"
  exit 1
fi

swap "$BEFORE" "$LOADING"
say "$DB is now the dump's; the one it replaced is $BEFORE. Migrating it:"
if ! c run --rm --no-TTY migrate; then
  swap "${DB}_failed_${STAMP}" "$BEFORE"
  say "the migrations failed on the dump, so $DB is the one from before again; the dump's is kept as ${DB}_failed_${STAMP}"
  exit 1
fi

say "Redis: dropping signed-in sessions and the cards' hot copies (they're read again from the database)"
for pattern in 'session:*' 'card:*'; do
  c exec -T redis sh -c "redis-cli --scan --pattern '$pattern' | xargs -r -n 500 redis-cli unlink > /dev/null"
done

trap - EXIT
c start "${SERVICES[@]}"
say "restored $DUMP, and ${SERVICES[*]} started. Once the site looks right, drop the database it replaced:"
say "  docker compose -f deploy/compose.yaml exec -T postgres dropdb -U sports $BEFORE"
