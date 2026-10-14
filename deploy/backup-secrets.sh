#!/usr/bin/env bash
# From any machine that can log in as deploy: seal the server's secrets again and download the sealed copy,
# to keep in the password manager. Only the age keys in secrets-recipients.txt open it; this machine needs
# nothing but ssh, and never sees the secrets themselves.
#   deploy/backup-secrets.sh deploy@<server-ip> [<file>]   (default: ./sports-follow-secrets-<date>.age)
set -euo pipefail
TARGET="${1:?usage: deploy/backup-secrets.sh deploy@<server-ip> [<file>]}"
OUT="${2:-sports-follow-secrets-$(date +%Y%m%d).age}"
HERE="$(cd "$(dirname "$0")" && pwd)"
[ ! -e "$OUT" ] || { echo "backup-secrets: $OUT already exists" >&2; exit 1; }

stage=$(mktemp -d)
trap 'rm -rf "$stage" "$OUT.partial"' EXIT
cp "$HERE/seal-secrets.sh" "$HERE/secrets-recipients.txt" "$stage/"
tar --no-xattrs -C "$stage" -cf - . | ssh "$TARGET" 'tmp=$(mktemp -d) && tar -xf - -C "$tmp" && sudo bash "$tmp/seal-secrets.sh" seal "$tmp/secrets-recipients.txt" >&2 && cat /var/backups/sports-follow/secrets.age; status=$?; rm -rf "$tmp"; exit $status' >"$OUT.partial"
head -1 "$OUT.partial" | grep -q '^-----BEGIN AGE ENCRYPTED FILE-----' || { echo "backup-secrets: the server sent something that isn't sealed" >&2; exit 1; }
mv "$OUT.partial" "$OUT"
echo "backup-secrets: saved $OUT. Keep it in the password manager (it's text: a secure note works), next to"
echo "backup-secrets: the age secret key that opens it. deploy/restore-secrets.sh puts it back on a server."
