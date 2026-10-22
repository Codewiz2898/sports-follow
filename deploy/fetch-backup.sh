#!/usr/bin/env bash
# From any machine that can log in as deploy: bring a database dump back from the off-site storage onto the
# server, opened, into /var/backups/sports-follow/fetched/, ready for restore-backup.sh (README.md, "Off-site
# backups"). The age secret key is read from stdin (piped from a password manager's command line) or asked
# for; it goes over SSH into the server's memory and is never written to a disk. Only ssh is needed here.
#   deploy/fetch-backup.sh deploy@<server-ip> [<dump>]   (default: the newest; offsite-backup.sh list shows them)
set -euo pipefail
TARGET="${1:?usage: deploy/fetch-backup.sh deploy@<server-ip> [<dump>]}"
WANT="${2:-latest}"
[[ "$WANT" =~ ^(latest|sports_follow-[0-9]{8}-[0-9]{4}\.dump(\.age)?)$ ]] || { echo "fetch-backup: not a dump name: $WANT" >&2; exit 1; }

key=""
if [ -t 0 ]; then
  IFS= read -rs -p "age secret key (AGE-SECRET-KEY-1…): " key
  echo >&2
else
  # The whole key file is fine too (age-keygen's comment lines are skipped).
  while IFS= read -r line; do
    case "$line" in AGE-SECRET-KEY-1*) key="$line"; break ;; esac
  done
fi
case "$key" in AGE-SECRET-KEY-1*) ;; *) echo "fetch-backup: that isn't an age secret key (AGE-SECRET-KEY-1…)" >&2; exit 1 ;; esac

printf '%s\n' "$key" | ssh "$TARGET" "sudo /opt/sports-follow/deploy/offsite-backup.sh fetch $WANT"
echo "fetch-backup: to load it, on the server: deploy/restore-backup.sh /var/backups/sports-follow/fetched/<file>.dump (it replaces the database; deploy/README.md, \"Off-site backups\")."
