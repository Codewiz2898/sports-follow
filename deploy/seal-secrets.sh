#!/usr/bin/env bash
# Runs on the server as root. Seals everything in /etc/sports-follow (the API tokens, the database
# password, the gateway token, the push key and the fan cookie key) into one file that only the age keys in
# secrets-recipients.txt can open, or opens such a file and puts its secrets back. The secret keys that open
# it live in a password manager, never on the Mac or the server. Nothing secret is printed.
#   seal-secrets.sh seal <recipients file>   writes /var/backups/sports-follow/secrets.age
#   seal-secrets.sh open <sealed file>       the age secret key on stdin; puts the secrets back
# install-secrets.sh seals after every change; backup-secrets.sh and restore-secrets.sh run this over SSH.
set -euo pipefail
umask 077
D=/etc/sports-follow
SEALED=/var/backups/sports-follow/secrets.age
APP_UID=10001   # the app containers' user, which reads files/
grp=$(getent group deploy >/dev/null && echo deploy || echo root)

usage() { echo "usage: seal-secrets.sh seal <recipients file> | open <sealed file>" >&2; exit 2; }
die() { echo "seal-secrets: $*" >&2; exit 1; }

need_age() {  # servers set up before server-setup.sh installed it
  command -v age >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq age >/dev/null
}

seal() {
  local recipients="$1" keys count
  keys=$(grep -cE '^[[:space:]]*age1' "$recipients" 2>/dev/null || true)
  if [ "${keys:-0}" -eq 0 ]; then
    echo "seal-secrets: secrets-recipients.txt has no age key, so the secrets aren't sealed (deploy/README.md, \"Secrets\")" >&2
    exit 3
  fi
  [ -f "$D/app.env" ] || die "$D has no secrets yet (deploy/push-secrets.sh)"
  need_age
  install -d -m 750 -o root -g "$grp" "$(dirname "$SEALED")"
  if ! tar -C "$D" --numeric-owner -czf - . | age --armor -R "$recipients" -o "$SEALED.partial"; then
    rm -f "$SEALED.partial"
    die "sealing failed; $SEALED is unchanged"
  fi
  # Sealed, so deploy may read it: backup-secrets.sh downloads it, and it goes off-site with the dumps.
  chown "root:$grp" "$SEALED.partial"
  chmod 640 "$SEALED.partial"
  mv "$SEALED.partial" "$SEALED"
  count=$(find "$D" -type f | wc -l)
  echo "seal-secrets: $count secret files sealed in $SEALED for $keys key(s)"
}

open_sealed() {
  local sealed="$1" key="" tmp f count=0 db_changed=""
  IFS= read -r key || true
  case "$key" in AGE-SECRET-KEY-1*) ;; *) die "expected an age secret key (AGE-SECRET-KEY-1…) on stdin" ;; esac
  [ -f "$sealed" ] || die "no sealed file at $sealed"
  need_age
  # Opened in memory (/run), never on disk; the key reaches age through a pipe, never a file.
  tmp=$(mktemp -d -p /run sports-follow-secrets.XXXXXX)
  # shellcheck disable=SC2064  # expanded now on purpose: tmp is local, and gone by the time the script exits
  trap "rm -rf '$tmp'" EXIT
  age --decrypt -i <(printf '%s\n' "$key") "$sealed" >"$tmp/secrets.tgz" 2>"$tmp/age.err" \
    || die "couldn't open $sealed: $(head -c 300 "$tmp/age.err")"
  mkdir "$tmp/in"
  tar -xzf "$tmp/secrets.tgz" -C "$tmp/in" --no-same-owner --no-same-permissions \
    || die "$sealed opened, but what's inside isn't a secrets archive"
  [ -f "$tmp/in/app.env" ] && [ -d "$tmp/in/files" ] || die "$sealed doesn't hold the server's secrets"

  # A database already made with another password won't take the restored one (see the README).
  if [ -f "$D/postgres.env" ] && [ -f "$tmp/in/postgres.env" ] && ! cmp -s "$D/postgres.env" "$tmp/in/postgres.env" \
     && docker volume inspect sports-follow_pgdata >/dev/null 2>&1; then
    db_changed=1
  fi

  # The same owners and modes install-secrets.sh gives them. Only plain files, in the two places secrets go.
  install -d -m 750 -o root -g "$grp" "$D"
  install -d -m 500 -o "$APP_UID" -g "$APP_UID" "$D/files"
  for f in "$tmp"/in/*.env; do
    [ -f "$f" ] && [ ! -L "$f" ] || continue
    install -m 640 -o root -g "$grp" "$f" "$D/"
    count=$((count + 1))
  done
  for f in "$tmp"/in/files/*; do
    [ -f "$f" ] && [ ! -L "$f" ] || continue
    install -m 400 -o "$APP_UID" -g "$APP_UID" "$f" "$D/files/"
    count=$((count + 1))
  done
  # Keep this sealed copy as the server's own, ready for the off-site backups.
  if [ "$(realpath "$sealed")" != "$(realpath -m "$SEALED")" ]; then
    install -d -m 750 -o root -g "$grp" "$(dirname "$SEALED")"
    install -m 640 -o root -g "$grp" "$sealed" "$SEALED"
  fi
  echo "seal-secrets: $count secret files put back in $D"
  if [ -n "$db_changed" ]; then
    echo "seal-secrets: this server's database was made with a different password than the restored one." >&2
    echo "seal-secrets: either delete it (no data yet: docker volume rm sports-follow_pgdata) or set its password to the restored one (deploy/README.md, \"Secrets\")" >&2
  fi
}

case "${1:-}" in
  seal) [ $# -eq 2 ] || usage; seal "$2" ;;
  open) [ $# -eq 2 ] || usage; open_sealed "$2" ;;
  *) usage ;;
esac
