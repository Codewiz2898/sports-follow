#!/usr/bin/env bash
# Off-site copies of the daily database dumps and the sealed secrets, in S3-compatible object storage
# (Cloudflare R2, Backblaze B2, DigitalOcean Spaces, AWS S3…) through rclone. Each dump is sealed with age for
# the keys in secrets-recipients.txt before it leaves, so the storage never holds fan data it can read.
#   offsite-backup.sh upload          hourly as deploy (systemd/sports-follow-offsite.timer): sends the dumps
#                                     not there yet and the sealed secrets, then thins out old dumps
#   offsite-backup.sh list            what's there
#   offsite-backup.sh fetch [<dump>]  as root, with the age secret key on stdin: brings a dump (default: the
#                                     newest) back, opened, into /var/backups/sports-follow/fetched/
# Where to: /etc/sports-follow/offsite.env (push-secrets.sh), with rclone's RCLONE_CONFIG_OFFSITE_* settings
# and OFFSITE_PATH, the bucket (and folder). Kept: every dump of the last KEEP_DAYS days, the first of each
# month for KEEP_MONTHS months, and always the newest KEEP_NEWEST.
set -euo pipefail
umask 077
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/sports-follow}"
CONF="${OFFSITE_CONF:-/etc/sports-follow/offsite.env}"
RECIPIENTS="${RECIPIENTS:-$APP_DIR/deploy/secrets-recipients.txt}"
STATE="${STATE_DIRECTORY:-/var/lib/sports-follow-offsite}"
KEEP_DAYS="${KEEP_DAYS:-30}"
KEEP_MONTHS="${KEEP_MONTHS:-12}"
KEEP_NEWEST="${KEEP_NEWEST:-7}"
DUMP_RE='^sports_follow-[0-9]{8}-[0-9]{4}\.dump\.age$'

usage() { echo "usage: offsite-backup.sh upload | list | fetch [<dump>]" >&2; exit 2; }
die() { echo "offsite-backup: $*" >&2; exit 1; }
say() { echo "offsite-backup: $*"; }
rc() { rclone --quiet "$@"; }

load_conf() {
  if [ ! -f "$CONF" ]; then
    say "not set up: no $CONF (deploy/README.md, \"Off-site backups\")"
    exit 0
  fi
  set -a
  # shellcheck disable=SC1090
  . "$CONF"
  set +a
  [ -n "${OFFSITE_PATH:-}" ] || die "$CONF has no OFFSITE_PATH (the bucket, and a folder if you like)"
  [ -n "${RCLONE_CONFIG_OFFSITE_TYPE:-}" ] || die "$CONF has no RCLONE_CONFIG_OFFSITE_TYPE (s3)"
  REMOTE="offsite:${OFFSITE_PATH%/}"
}

# The dumps off-site, oldest first. Fails when the storage can't be listed (not when it's just empty).
remote_dumps() {
  local out
  if ! out=$(rc lsf --files-only "$REMOTE/dumps/" 2>&1); then
    # A folder that was never written to is empty, not an error, on S3; rclone's local backend says otherwise.
    case "$out" in *"directory not found"*) return 0 ;; esac
    echo "$out" >&2
    return 1
  fi
  grep -E "$DUMP_RE" <<<"$out" | sort || true
}

do_upload() {
  local have f name tmp sent=0
  load_conf
  [ "$(grep -cE '^[[:space:]]*age1' "$RECIPIENTS" 2>/dev/null || true)" -gt 0 ] \
    || die "secrets-recipients.txt has no age key, and dumps never leave unsealed (deploy/README.md, \"Secrets\")"
  have=$(remote_dumps) || die "couldn't list $REMOTE/dumps"
  mkdir -p "$STATE"
  tmp="$STATE/upload.age"
  # shellcheck disable=SC2064  # expanded now on purpose: tmp is local
  trap "rm -f '$tmp'" EXIT
  for f in "$BACKUP_DIR"/sports_follow-*.dump; do
    [ -f "$f" ] || continue
    name="$(basename "$f").age"
    grep -qxF "$name" <<<"$have" && continue
    # Sealed to a file first: an upload is all or nothing, so a half-sealed dump never lands under its name.
    age -R "$RECIPIENTS" -o "$tmp" "$f" || die "couldn't seal $f"
    rc copyto "$tmp" "$REMOTE/dumps/$name" || die "couldn't upload $name"
    sent=$((sent + 1))
  done
  if [ -f "$BACKUP_DIR/secrets.age" ]; then
    rc copyto "$BACKUP_DIR/secrets.age" "$REMOTE/secrets.age" || die "couldn't upload secrets.age"
  fi
  prune
  date +%s >"$STATE/last-ok"
  say "$sent new dump(s) sent; $(remote_dumps | wc -l | tr -d ' ') off-site in $REMOTE/dumps"
}

# Thin out: keep the last KEEP_DAYS days, the first dump of each of the last KEEP_MONTHS months, and the
# newest KEEP_NEWEST whatever their dates say (in case the clock is wrong). Only names this script writes.
prune() {
  local all keep since_day since_month name gone=0
  all=$(remote_dumps) || die "couldn't list $REMOTE/dumps to thin it out"
  [ -n "$all" ] || return 0
  since_day=$(date -u -d "-$KEEP_DAYS days" +%Y%m%d)
  since_month=$(date -u -d "$(date -u +%Y-%m-15) -$KEEP_MONTHS months" +%Y%m)
  keep=$( {
    awk -v d="$since_day" -v m="$since_month" '
      { day = substr($0, 15, 8); month = substr(day, 1, 6) }
      day >= d { print; next }
      month >= m && !(month in first) { first[month] = 1; print }' <<<"$all"
    tail -n "$KEEP_NEWEST" <<<"$all"
  } | sort -u)
  while read -r name; do
    grep -qxF "$name" <<<"$keep" && continue
    rc deletefile "$REMOTE/dumps/$name" || die "couldn't delete $name"
    gone=$((gone + 1))
  done <<<"$all"
  [ "$gone" -eq 0 ] || say "$gone old dump(s) removed off-site"
}

do_list() {
  load_conf
  rc lsl "$REMOTE/" 2>/dev/null | awk '$4 == "secrets.age" || $4 ~ /^dumps\// { print $1, $2, substr($3, 1, 5), $4 }' | sort -k4
}

do_fetch() {
  local want="${1:-latest}" key="" name out grp err
  [ "$(id -u)" -eq 0 ] || die "fetch writes into $BACKUP_DIR: run it as root"
  IFS= read -r key || true
  case "$key" in AGE-SECRET-KEY-1*) ;; *) die "expected an age secret key (AGE-SECRET-KEY-1…) on stdin" ;; esac
  load_conf
  if [ "$want" = latest ]; then
    name=$(remote_dumps | tail -1) || die "couldn't list $REMOTE/dumps"
    [ -n "$name" ] || die "no dumps in $REMOTE/dumps"
  else
    name="${want%.age}.age"
    grep -qE "$DUMP_RE" <<<"$name" || die "not a dump name: $want"
  fi
  grp=$(getent group deploy >/dev/null && echo deploy || echo root)
  install -d -m 750 -o root -g "$grp" "$BACKUP_DIR/fetched"
  out="$BACKUP_DIR/fetched/${name%.age}"
  err=$(mktemp)
  # shellcheck disable=SC2064  # expanded now on purpose: these are local
  trap "rm -f '$out.age.partial' '$out.partial' '$err'" EXIT
  rc copyto "$REMOTE/dumps/$name" "$out.age.partial" || die "couldn't download $name"
  # The key reaches age through a pipe, never a file.
  age --decrypt -i <(printf '%s\n' "$key") -o "$out.partial" "$out.age.partial" 2>"$err" \
    || die "couldn't open $name: $(head -c 300 "$err")"
  chown "root:$grp" "$out.partial"
  chmod 640 "$out.partial"
  mv "$out.partial" "$out"
  say "$out ($(du -h "$out" | cut -f1)), opened from $REMOTE/dumps/$name"
}

case "${1:-}" in
  upload) [ $# -eq 1 ] || usage; do_upload ;;
  list) [ $# -eq 1 ] || usage; do_list ;;
  fetch) [ $# -le 2 ] || usage; do_fetch "${2:-latest}" ;;
  *) usage ;;
esac
