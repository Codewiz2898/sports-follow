#!/usr/bin/env bash
# Runs on the server as deploy: put a commit of main live, or go back to the one before.
#   deploy/deploy.sh deploy [<commit>]   build and start <commit> (default: the tip of main), wait until healthy;
#                                        if it doesn't get there, the previous deploy comes back by itself
#   deploy/deploy.sh rollback            start the previous deploy's image again (the newer migrations stay)
#   deploy/deploy.sh status              what's live, what rollback would start, the containers
# GitHub Actions' key may run only this script (server-setup.sh, DEPLOY_KEY): `ssh deploy@<server> deploy <sha>`
# arrives here as SSH_ORIGINAL_COMMAND. Only commits on GitHub's main are deployed.
# Each deploy keeps its image as sports-follow:<commit>; the live one and the one before are kept.
# Everything runs from the last line, which bash has read in full, so a deploy that changes this file
# doesn't change it mid-run.
main() {
  set -euo pipefail
  set -f   # SSH_ORIGINAL_COMMAND is split into words below, never expanded
  APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  STATE="${XDG_STATE_HOME:-$HOME/.local/state}/sports-follow"
  HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-240}"
  cd "$APP_DIR"
  mkdir -p "$STATE"
  if [ -n "${SSH_ORIGINAL_COMMAND:-}" ]; then
    # shellcheck disable=SC2086
    set -- $SSH_ORIGINAL_COMMAND
  fi
  case "${1:-}" in
    deploy) [ $# -le 2 ] || usage; locked do_deploy "${2:-}" ;;
    rollback) [ $# -eq 1 ] || usage; locked do_rollback ;;
    status) [ $# -eq 1 ] || usage; do_status ;;
    *) usage ;;
  esac
}

usage() { echo "usage: deploy.sh deploy [<commit>] | rollback | status" >&2; exit 2; }
say() { echo "deploy: $*"; }
die() { echo "deploy: $*" >&2; exit 1; }
c() { docker compose -f deploy/compose.yaml "$@"; }
live() { cat "$STATE/live" 2>/dev/null || true; }
previous() { cat "$STATE/previous" 2>/dev/null || true; }

locked() {
  exec 9>"$STATE/lock"
  flock -n 9 || die "another deploy or rollback is running"
  "$@"
}

do_deploy() {
  local want="$1" sha was
  [ -z "$want" ] || [[ "$want" =~ ^[0-9a-f]{7,40}$ ]] || die "not a commit id: $want"
  git fetch --quiet origin main
  sha=$(git rev-parse --verify --quiet "${want:-origin/main}^{commit}") || die "no commit $want on GitHub"
  git merge-base --is-ancestor "$sha" origin/main || die "$sha isn't on main"
  was=$(live)
  say "deploying $(git log -1 --format='%h %s' "$sha")${was:+ (live now: ${was:0:7})}"

  git checkout --quiet --detach "$sha"
  if ! SPORTS_FOLLOW_COMMIT="$sha" c build --quiet; then   # the image knows its commit: /api/health says it
    # Back to what runs: Caddy's and Alloy's config come from this checkout, for a restart to pick up.
    [ -z "$was" ] || git checkout --quiet --detach "$was"
    die "${sha:0:7} didn't build, so nothing was replaced${was:+ (${was:0:7} is still live)}"
  fi
  docker tag sports-follow:latest "sports-follow:$sha"
  # Migrations on their own first: `up` would stop the old API and worker before running them, so a
  # failed migration would leave nothing running. This way the old code keeps serving.
  if ! c run --rm --no-TTY migrate; then
    if [ -n "$was" ]; then
      git checkout --quiet --detach "$was"   # Caddy's and Alloy's config, as after a failed build
      if docker image inspect "sports-follow:$was" >/dev/null 2>&1; then
        docker tag "sports-follow:$was" sports-follow:latest
      else
        say "there's no image of ${was:0:7} here, so sports-follow:latest is still ${sha:0:7}'s: deploy again before recreating any container"
      fi
    fi
    die "the migrations failed, so nothing was replaced${was:+ (${was:0:7} is still live)}"
  fi
  # From here a rollback goes back to what was live before this deploy, whatever happens next.
  if [ -n "$was" ] && [ "$was" != "$sha" ]; then echo "$was" >"$STATE/previous"; fi
  # A first deploy is named live only once it's healthy: until then a new server can still be set up again
  # (migrate-to-server.sh refuses one that names a live commit).
  [ -z "$was" ] || echo "$sha" >"$STATE/live"
  if ! c up -d --remove-orphans || ! wait_healthy || ! pick_up_files; then
    # Its migrations are in by now, so going back is no riskier than a rollback by hand, and it's sooner.
    [ -n "$was" ] && [ "$was" != "$sha" ] || die "${sha:0:7} didn't start, and there's no earlier deploy to go back to"
    say "${sha:0:7} didn't start; going back to ${was:0:7}"
    do_rollback
    die "${sha:0:7} failed to start, so ${was:0:7} is live again"
  fi
  echo "$sha" >"$STATE/live"

  # Keep the live image and the previous one; older ones go.
  local keep tag
  keep="sports-follow:latest sports-follow:$sha sports-follow:$(previous)"
  for tag in $(docker image ls sports-follow --format '{{.Repository}}:{{.Tag}}'); do
    case " $keep " in
      *" $tag "*) ;;
      *) docker image rm "$tag" >/dev/null 2>&1 || say "kept $tag (still in use)" ;;
    esac
  done
  # Alloy's config is in this checkout (deploy/alloy/): pick up any change. Nothing if it isn't running yet.
  sudo -n systemctl try-reload-or-restart alloy 2>/dev/null || true
  say "${sha:0:7} is live"
}

do_rollback() {
  local to was
  to=$(previous)
  was=$(live)
  [ -n "$to" ] || die "nothing to roll back to"
  docker image inspect "sports-follow:$to" >/dev/null 2>&1 || die "the image for ${to:0:7} is gone"
  say "rolling back from ${was:0:7} to $(git log -1 --format='%h %s' "$to")"
  git checkout --quiet --detach "$to"
  docker tag "sports-follow:$to" sports-follow:latest
  # From here its image is the one any start runs, healthy or not, and whether or not this `up` works: status, the
  # deploy summary and the next deploy go by what's written here. Its previous stays until it runs, so a rollback
  # whose containers didn't start can be tried again.
  echo "$to" >"$STATE/live"
  # Not the migrate service: the older code doesn't know the newer migrations, and the database keeps them.
  # The curator too, where that commit has one; one from before it had none, and --remove-orphans stops this one's.
  c up -d --no-build --no-deps --remove-orphans caddy api worker gateway backup $(c config --services | grep -x curator || true) \
    || die "${to:0:7} is tagged live, but its containers didn't start; a rollback tries again"
  rm -f "$STATE/previous"   # a second rollback would only go back to the bad deploy
  wait_healthy || die "${to:0:7} isn't healthy either; see: docker compose -f deploy/compose.yaml logs api"
  pick_up_files || die "${to:0:7} is live again, but Caddy or the backup container didn't take its files"
  say "${to:0:7} is live again; ${was:0:7}'s migrations, if it had any, are still applied"
}

do_status() {
  local l p
  l=$(live); p=$(previous)
  echo "live:     ${l:+$(git log -1 --format='%h %s (%cr)' "$l")}"
  echo "previous: ${p:+$(git log -1 --format='%h %s (%cr)' "$p")}"
  c ps --format 'table {{.Service}}\t{{.Status}}'
}

# The API's own health check (the database and Redis answer), and no crash from the API, the worker or the
# curator (where the commit has one: the same image, with no health check of its own).
wait_healthy() {
  local deadline=$((SECONDS + HEALTH_TIMEOUT)) api=missing worker=missing curator=""
  # grep reads the list to its end: -q would stop at the match, and under pipefail the writer it left, killed
  # by SIGPIPE, would fail the check, so a crashing curator would go unseen.
  if c config --services 2>/dev/null | grep -x curator >/dev/null; then curator=missing; fi
  while [ $SECONDS -lt $deadline ]; do
    sleep 5
    api=$(container api)
    worker=$(container worker)
    [ -z "$curator" ] || curator=$(container curator)
    case "$api" in
      *,healthy) [[ "$worker" == running,0,* ]] && [[ -z "$curator" || "$curator" == running,0,* ]] && return 0 ;;
      *,unhealthy|restarting,*|exited,*|*,[1-9]*,*) break ;;   # unhealthy, or crashed at least once
    esac
    case "$worker" in restarting,*|exited,*|*,[1-9]*,*) break ;; esac
    case "$curator" in restarting,*|exited,*|*,[1-9]*,*) break ;; esac
  done
  echo "deploy: api: $api; worker: $worker${curator:+; curator: $curator}" >&2
  c logs --tail 30 api worker ${curator:+curator} >&2 || true
  return 1
}
# status,restarts,health of a service's container (stopped ones too): e.g. running,0,healthy
container() {
  docker inspect -f '{{.State.Status}},{{.RestartCount}},{{if .State.Health}}{{.State.Health.Status}}{{end}}' \
    "$(c ps -aq "$1" | head -1)" 2>/dev/null || echo "missing,0,"
}

# Caddy's config and the backup script come from this checkout, and `up` recreates neither container when
# only that file changed. Caddy reloads its Caddyfile, from a mount of its folder (compose.yaml): nothing
# happens when it's unchanged, and one it refuses leaves the running config serving. The backup container
# mounts its one file, which a checkout replaces, so it sees the old one until it restarts.
pick_up_files() {
  local out
  if ! out=$(c exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile </dev/null 2>&1); then
    echo "$out" >&2
    echo "deploy: Caddy didn't reload deploy/caddy/Caddyfile" >&2
    return 1
  fi
  if ! c exec -T backup cat /backup.sh </dev/null 2>/dev/null | cmp -s - deploy/backup.sh; then
    say "deploy/backup.sh changed: restarting the backup container"
    c restart backup || return 1
  fi
}

main "$@"; exit $?
