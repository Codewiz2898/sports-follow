#!/usr/bin/env bash
# Cancels the Deploy runs after CI that this one replaces while they wait for approval at the production
# environment. A run waiting there holds the production concurrency group, so this run's deploy would queue
# behind it, and approving it would deploy the older commit. deploy.yml's newest job runs it, with gh
# (GH_TOKEN), GITHUB_REPOSITORY and GITHUB_RUN_ID:
#   cancel-replaced.sh <commit>
# and writes count=<runs cancelled> to $GITHUB_OUTPUT, when that's set.
#
# A run this one replaces: one after CI (a run started by hand is left alone), started before this one, for
# <commit> or a commit before it on main. Each unfinished run is read on its own, and waits for approval when
# its pending deployments name production. Not only from GitHub's list of waiting runs (status=waiting), which
# on 22 Oct 2026 twice listed nothing while a run had waited 17 and 49 minutes, nor from a run's own status,
# which lags its jobs (BHA-663).
# A run still on its way to the approval gate (its first job running, its deploy job about to wait) is looked
# at again every 10 seconds for 2 minutes, so one that gets there the second after a look is cancelled too.
# A deploy that's running is never stopped: a run is cancelled only right after GitHub says it's waiting.
# GitHub has no "cancel only if waiting", so an approval in that second could still be cancelled; a cancel
# or a read that fails only warns, and the run is read again on the next look.
set -uo pipefail

[ $# -eq 1 ] || { echo "usage: cancel-replaced.sh <commit>" >&2; exit 2; }
SHA=$1
REPO=${GITHUB_REPOSITORY:?}
ME=${GITHUB_RUN_ID:?}
LOOKS=12 EVERY=10

api() { local path=$1; shift; gh api "repos/$REPO/$path" "$@"; }

count=0 cancelled=" " told=" " includes=" " excludes=" " mine="" answer=""

say_once() {  # a line about a run, the first time it's said
  case "$told" in *" $1 "*) return ;; esac
  told="$told$1 "
  echo "$2"
}

# Whether this run replaces run $1 (its event $2, started $3, named $4), in $answer: yes, no, or unknown (a
# read failed). What a commit comparison says is kept for the next look.
replaces() {
  local id=$1 event=$2 started=$3 title=$4 theirs status
  answer=no
  [ "$event" = workflow_run ] || return 0
  # Started before this one (the id settles the same second), so two runs never cancel each other.
  [[ "$started" < "$mine" ]] || { [ "$started" = "$mine" ] && [ "$id" -lt "$ME" ]; } || return 0
  theirs=$(grep -oE '[0-9a-f]{40}' <<<"$title" | head -1)   # deploy.yml's run-name carries the commit
  [ -n "$theirs" ] || return 0
  case "$excludes" in *" $theirs "*) return 0 ;; esac
  case "$includes" in *" $theirs "*) answer=yes; return 0 ;; esac
  if [ "$theirs" = "$SHA" ]; then status=identical
  elif ! status=$(api "compare/$theirs...$SHA" --jq .status); then answer=unknown; return 0
  fi
  case $status in
    identical | ahead) includes="$includes$theirs " answer=yes ;;
    *) excludes="$excludes$theirs " ;;
  esac
}

# Whether run $1 waits for approval at production now, in $answer: yes, no, or unknown.
waiting() {
  local n
  if ! n=$(api "actions/runs/$1/pending_deployments" --jq '[.[] | select(.environment.name == "production")] | length'); then
    answer=unknown
  elif [ "${n:-0}" -gt 0 ] 2>/dev/null; then answer=yes
  else answer=no
  fi
}

cancel() {
  echo "Cancelling \"$2\" (run $1), which was waiting for approval"
  if api "actions/runs/$1/cancel" -X POST --silent; then
    count=$((count + 1)) cancelled="$cancelled$1 "
  else
    echo "::warning::Couldn't cancel run $1; it may have been approved or rejected just now"
    return 1
  fi
}

RUNS='.workflow_runs[] | select(.status != "completed") | [.id, .event, .run_started_at, .display_title] | @tsv'

# One look at the unfinished Deploy runs: cancels the replaced ones waiting for approval, and counts those still
# on their way to the gate (coming), those queued for the production group (queued), and whether a deploy runs.
look() {
  local rows id event started title deploy
  coming=0 queued=0 deploying=0
  [ -n "$mine" ] || mine=$(api "actions/runs/$ME" --jq .run_started_at) || mine=""
  if [ -z "$mine" ] || ! rows=$(api "actions/workflows/deploy.yml/runs?per_page=50" --jq "$RUNS"); then
    echo "::warning::Couldn't read the Deploy runs"
    coming=1
    return
  fi
  # GitHub's list of waiting runs too, in case the other one lags: it costs a call.
  rows=$(printf '%s\n%s\n' "$rows" "$(api "actions/workflows/deploy.yml/runs?status=waiting&per_page=50" --jq "$RUNS")" | sort -u)
  while IFS=$'\t' read -r id event started title; do
    [ -n "$id" ] && [ "$id" != "$ME" ] || continue
    case "$cancelled" in *" $id "*) continue ;; esac
    # Its deploy job (deploy.yml's job of that name): queued, pending (queued for the production group), waiting,
    # in_progress (deploying) or completed; none while its first job runs.
    deploy=$(api "actions/runs/$id/jobs" --jq '[.jobs[] | select(.name == "deploy") | .status][0] // "none"') || deploy=unknown
    if [ "$deploy" = in_progress ]; then
      deploying=1
      say_once "$id:deploying" "\"$title\" (run $id) is deploying: left alone"
      continue
    fi
    replaces "$id" "$event" "$started" "$title"
    case $answer in
      no) continue ;;
      unknown) coming=$((coming + 1)); continue ;;
    esac
    waiting "$id"
    case $answer in
      yes) cancel "$id" "$title" || coming=$((coming + 1)); continue ;;   # a failed one is read again
      unknown) coming=$((coming + 1)); continue ;;
    esac
    case $deploy in
      completed) ;;
      pending) queued=$((queued + 1)) ;;
      *) coming=$((coming + 1))
         say_once "$id:coming" "\"$title\" (run $id) is on its way to the approval gate: looking again every $EVERY seconds" ;;
    esac
  done <<<"$rows"
}

settled=0
for n in $(seq "$LOOKS"); do
  look
  # Settled once nothing it replaces is on its way to the gate. A run queued behind a deploy that's running
  # stays queued until this run's deploy job takes its place (GitHub keeps one run queued per group).
  if [ "$coming" -eq 0 ] && { [ "$queued" -eq 0 ] || [ "$deploying" -eq 1 ]; }; then settled=1; break; fi
  [ "$n" -eq "$LOOKS" ] || sleep "$EVERY"
done
if [ "$settled" -eq 0 ]; then
  echo "::warning::A Deploy run this one replaces hadn't reached the approval gate after $((LOOKS * EVERY / 60)) minutes. If it waits for approval later, this run's deploy queues behind it until that one is cancelled or approved (one for an older commit then stops without deploying), and this one waits next."
fi
echo "Cancelled $count waiting deploy$([ "$count" -eq 1 ] || echo s) that this one replaces"
echo "count=$count" >>"${GITHUB_OUTPUT:-/dev/null}"
