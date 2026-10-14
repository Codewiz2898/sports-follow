#!/usr/bin/env bash
# From any machine that can log in as deploy: put the secrets from a sealed copy (backup-secrets.sh, or
# secrets.age from the off-site backups) back on a server, usually a new one before its first deploy, so
# fans stay signed in and their devices keep getting notifications. The age secret key is read from stdin
# (piped from a password manager's command line) or asked for; it goes over SSH into the server's memory
# and is never written to a disk, here or there. This machine needs nothing but ssh.
#   deploy/restore-secrets.sh deploy@<server-ip> <sealed file>
set -euo pipefail
TARGET="${1:?usage: deploy/restore-secrets.sh deploy@<server-ip> <sealed file>}"
SEALED="${2:?usage: deploy/restore-secrets.sh deploy@<server-ip> <sealed file>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
head -1 "$SEALED" | grep -q '^-----BEGIN AGE ENCRYPTED FILE-----' || { echo "restore-secrets: $SEALED isn't a sealed copy" >&2; exit 1; }

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
case "$key" in AGE-SECRET-KEY-1*) ;; *) echo "restore-secrets: that isn't an age secret key (AGE-SECRET-KEY-1…)" >&2; exit 1 ;; esac

stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
cp "$HERE/seal-secrets.sh" "$stage/"
cp "$SEALED" "$stage/secrets.age"
# The key goes first, as one line; the server's shell reads just that line, and tar takes the rest.
{ printf '%s\n' "$key"; tar --no-xattrs -C "$stage" -cf - .; } \
  | ssh "$TARGET" 'IFS= read -r key; tmp=$(mktemp -d) && tar -xf - -C "$tmp" && printf "%s\n" "$key" | sudo bash "$tmp/seal-secrets.sh" open "$tmp/secrets.age"; status=$?; rm -rf "$tmp"; exit $status'
echo "restore-secrets: done. Deploy (or restart) to use them: ssh $TARGET /opt/sports-follow/deploy/deploy.sh deploy"
