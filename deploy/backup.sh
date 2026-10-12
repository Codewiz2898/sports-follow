#!/bin/sh
# A compressed dump of the database once a day (the first right away), keeping KEEP_DAYS of them.
# Runs in the compose "backup" service; restore with pg_restore (see README.md).
set -eu
export PGPASSWORD="$POSTGRES_PASSWORD"
while true; do
  file="/backups/sports_follow-$(date -u +%Y%m%d-%H%M).dump"
  if pg_dump --format=custom --file="$file.partial" && mv "$file.partial" "$file"; then
    echo "backup: $file ($(du -h "$file" | cut -f1))"
  else
    echo "backup: pg_dump failed" >&2
    rm -f "$file.partial"
  fi
  find /backups -name 'sports_follow-*.dump' -mtime "+${KEEP_DAYS:-14}" -delete
  sleep 86400
done
