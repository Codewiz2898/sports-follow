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
  c build --quiet
  docker tag sports-follow:latest "sports-follow:$sha"
  # Migrations on their own first: `up` would stop the old API and worker before running them, so a
  # failed migration would leave nothing running. This way the old code keeps serving.
  if ! c run --rm --no-TTY migrate; then
    if [ -n "$was" ] && docker image inspect "sports-follow:$was" >/dev/null 2>&1; then
      docker tag "sports-follow:$was" sports-follow:latest
      git checkout --quiet --detach "$was"
    fi
    die "the migrations failed, so nothing was replaced${was:+ (${was:0:7} is still live)}"
  fi
  # From here a rollback goes back to what was live before this deploy, whatever happens next.
  if [ -n "$was" ] && [ "$was" != "$sha" ]; then echo "$was" >"$STATE/previous"; fi
  echo "$sha" >"$STATE/live"
  if ! c up -d --remove-orphans || ! wait_healthy; then
    # Its migrations are in by now, so going back is no riskier than a rollback by hand, and it's sooner.
    [ -n "$was" ] && [ "$was" != "$sha" ] || die "${sha:0:7} didn't start, and there's no earlier deploy to go back to"
    say "${sha:0:7} didn't start; going back to ${was:0:7}"
    do_rollback
    die "${sha:0:7} failed to start, so ${was:0:7} is live again"
  fi

  # Keep the live image and the previous one; older ones go.
  local keep tag
  keep="sports-follow:latest sports-follow:$sha sports-follow:$(previous)"
  for tag in $(docker image ls sports-follow --format '{{.Repository}}:{{.Tag}}'); do
    case " $keep " in
      *" $tag "*) ;;
      *) docker image rm "$tag" >/dev/null 2>&1 || say "kept $tag (still in use)" ;;
    esac
  done
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
  # Not the migrate service: the older code doesn't know the newer migrations, and the database keeps them.
  c up -d --no-build --no-deps caddy api worker gateway backup
  wait_healthy || die "${to:0:7} isn't healthy either; see: docker compose -f deploy/compose.yaml logs api"
  echo "$to" >"$STATE/live"
  rm -f "$STATE/previous"   # a second rollback would only go back to the bad deploy
  say "${to:0:7} is live again; ${was:0:7}'s migrations, if it had any, are still applied"
}

do_status() {
  local l p
  l=$(live); p=$(previous)
  echo "live:     ${l:+$(git log -1 --format='%h %s (%cr)' "$l")}"
  echo "previous: ${p:+$(git log -1 --format='%h %s (%cr)' "$p")}"
  c ps --format 'table {{.Service}}\t{{.Status}}'
}

# The API's own health check (the database and Redis answer), and no crash from the API or the worker.
wait_healthy() {
  local deadline=$((SECONDS + HEALTH_TIMEOUT)) api=missing worker=missing
  while [ $SECONDS -lt $deadline ]; do
    sleep 5
    api=$(container api)
    worker=$(container worker)
    case "$api" in
      *,healthy) [[ "$worker" == running,0,* ]] && return 0 ;;
      *,unhealthy|restarting,*|exited,*|*,[1-9]*,*) break ;;   # unhealthy, or crashed at least once
    esac
    case "$worker" in restarting,*|exited,*|*,[1-9]*,*) break ;; esac
  done
  echo "deploy: api: $api; worker: $worker" >&2
  c logs --tail 30 api worker >&2 || true
  return 1
}
# status,restarts,health of a service's container (stopped ones too): e.g. running,0,healthy
container() {
  docker inspect -f '{{.State.Status}},{{.RestartCount}},{{if .State.Health}}{{.State.Health.Status}}{{end}}' \
    "$(c ps -aq "$1" | head -1)" 2>/dev/null || echo "missing,0,"
}

main "$@"; exit $?
