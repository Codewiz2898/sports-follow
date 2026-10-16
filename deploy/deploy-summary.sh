#!/usr/bin/env bash
# What a deploy waiting for your approval brings: the changes since the live commit, the BHA issues they
# close, whether CI passed, and the link to approve it. GitHub Actions runs it (gh with GH_TOKEN, jq, curl,
# and this repo with its history); it works from a checkout too.
#   deploy-summary.sh [--send] [<commit> <run-url>]
# prints it as Markdown, for the deploy waiting now or for that Deploy run, and with --send also sends it:
# by email through Resend (RESEND_KEY, DEPLOY_SUMMARY_TO) and as a push to an ntfy topic
# (DEPLOY_SUMMARY_URL), whichever are set. With nothing waiting, it says so and sends nothing.
# deploy.yml sends the first deploy to wait; deploy-summary.yml, every morning, the one waiting then.
# What's live: the commit /api/health names, or, from an image built before it did, the last deploy GitHub
# recorded. The site's address: APP_URL, else the production environment's, from that deploy.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"
REPO="${GITHUB_REPOSITORY:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}"
GITHUB="${GITHUB_SERVER_URL:-https://github.com}/$REPO"
APP_NAME=$(jq -r .name sports_follow/app.json)
MAX_CHANGES=25

usage() { echo "usage: deploy-summary.sh [--send] [<commit> <run-url>]" >&2; exit 2; }
api() { local path=$1; shift; gh api "repos/$REPO/$path" "$@"; }
when() { date -u -d "$1" '+%-d %b %H:%M UTC' 2>/dev/null || echo "$1"; }   # GNU date; macOS's shows it as it is
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

send=0 want="" run_url="" since=""
if [ "${1:-}" = --send ]; then send=1; shift; fi
case $# in
  0)
    # The newest run waiting for approval (deploy.yml cancels older ones). Its name carries its commit.
    IFS='|' read -r run_id title since < <(api "actions/workflows/deploy.yml/runs?status=waiting&per_page=1" \
      --jq '.workflow_runs[0] // empty | [.id, .display_title, .created_at] | map(tostring) | join("|")') || true
    if [ -z "${run_id:-}" ]; then echo "No deploy is waiting." >&2; exit 0; fi
    run_url="$GITHUB/actions/runs/$run_id"
    want=$(grep -oE '[0-9a-f]{40}' <<<"$title" | head -1) || true
    ;;
  2) want=$1 run_url=$2 ;;
  *) usage ;;
esac

# The last deploy GitHub recorded as a success: its commit, when, and the site's address.
deployed="" deployed_at="" site=""
while IFS='|' read -r id sha; do
  IFS='|' read -r state url at < <(api "deployments/$id/statuses?per_page=1" \
    --jq '.[0] // empty | [.state, (.environment_url // ""), .created_at] | join("|")') || continue
  if [ "${state:-}" = success ]; then deployed=$sha deployed_at=$at site=$url; break; fi
done < <(api "deployments?environment=production&per_page=30" --jq '.[] | [(.id | tostring), .sha] | join("|")')
site="${APP_URL:-$site}"
site="${site%/}"

live="" live_how=""
if [ -n "$site" ] && live=$(curl -fsS -m 10 "$site/api/health" | jq -r '.commit // empty') && [ -n "$live" ]; then
  live_how="as $site says"
elif [ -n "$deployed" ]; then
  live=$deployed live_how="its last deploy, $(when "$deployed_at"); the site doesn't say which commit it runs yet"
fi

body="$tmp/body"
if [ -z "$want" ]; then
  # A rollback by hand (the only run whose name has no commit).
  title="$APP_NAME: a rollback is waiting for you"
  { echo "Approve or reject it: $run_url"; } >"$body"
else
  git cat-file -e "$want^{commit}" 2>/dev/null || git fetch --quiet origin "$want" || true
  range="" range_note=""
  if [ -z "$live" ] || ! git cat-file -e "$live^{commit}" 2>/dev/null; then
    range_note="What's live is unknown, so the changes aren't listed."
  elif [ "$live" = "$want" ]; then
    range_note="It's the commit already live: approving it changes nothing."
  elif git merge-base --is-ancestor "$live" "$want"; then
    range="$live..$want"
  else
    range_note="It's older than what's live (${live:0:7}): approving it takes the site back."
  fi

  # Each change on main: its pull request's title, or the commit's own subject. The issues come from the
  # pull requests' descriptions and every commit's message ("Fixes BHA-n", as CLAUDE.md asks).
  changes="$tmp/changes" texts="$tmp/texts"
  : >"$changes"; : >"$texts"
  if [ -n "$range" ]; then
    while IFS=$'\x1f' read -r sha subject; do
      pr=$(sed -nE 's/^Merge pull request #([0-9]+) .*/\1/p; s/.*\(#([0-9]+)\)$/\1/p' <<<"$subject" | head -1)
      if [ -n "$pr" ] && api "pulls/$pr" >"$tmp/pr.json" 2>/dev/null; then
        echo "- #$pr $(jq -r .title "$tmp/pr.json")" >>"$changes"
        jq -r '.body // ""' "$tmp/pr.json" >>"$texts"
      else
        echo "- ${sha:0:7} $subject" >>"$changes"
      fi
    done < <(git log --first-parent --format='%H%x1f%s' "$range")
    git log --format=%B "$range" >>"$texts"
  fi
  count=$(wc -l <"$changes" | tr -d ' ')
  issues=$(grep -oiE '(^|[^a-z])(fix(e[sd])?|close[sd]?|resolve[sd]?) +BHA-[0-9]+' "$texts" | grep -oE 'BHA-[0-9]+' |
    sort -t- -k2,2n -u | paste -sd, - | sed 's/,/, /g') || true

  # Whether CI passed for it, and for main's newest commit if that isn't it.
  ci() { api "actions/workflows/ci.yml/runs?head_sha=$1&event=push&per_page=1" \
    --jq '.workflow_runs[0] // empty | [(.conclusion // .status), .html_url] | join("|")'; }
  IFS='|' read -r ci_state ci_url < <(ci "$want") || true
  case "${ci_state:-}" in success) ci_said="CI passed" ;; "") ci_said="no CI run found" ;; *) ci_said="CI: $ci_state" ;; esac
  tip=$(api commits/main --jq .sha)
  tip_note=""
  if [ "$tip" != "$want" ]; then
    IFS='|' read -r tip_state tip_url < <(ci "$tip") || true
    case "${tip_state:-}" in
      success) tip_note="Main's newest commit, ${tip:0:7}, passed CI too: its own deploy should be waiting soon, in place of this one." ;;
      failure) tip_note="Main's newest commit, ${tip:0:7}, isn't in it: its CI failed (${tip_url:-})." ;;
      *) tip_note="Main's newest commit, ${tip:0:7}, isn't in it: its CI is ${tip_state:-not started} (${tip_url:-no run yet})." ;;
    esac
  fi

  if [ "$count" -gt 0 ]; then title="$APP_NAME: a deploy is waiting for you ($count change$([ "$count" = 1 ] || echo s))"
  else title="$APP_NAME: a deploy is waiting for you"; fi
  {
    echo "Waiting: ${want:0:7}, ${ci_said}${ci_url:+ ($ci_url)}"
    echo "Live now: ${live:+${live:0:7}, }${live_how:-unknown}"
    [ -z "$since" ] || echo "Waiting since $(when "$since"). Leave it, and ${site:-the site} stays as it is."
    echo
    if [ "$count" -gt 0 ]; then
      echo "What it brings:"
      head -n "$MAX_CHANGES" "$changes"
      [ "$count" -le "$MAX_CHANGES" ] || echo "- and $((count - MAX_CHANGES)) more"
      echo
      echo "Closes: ${issues:-no BHA issue named}"
      echo "Every commit: $GITHUB/compare/${live:0:12}...${want:0:12}"
    else
      echo "$range_note"
    fi
    [ -z "$tip_note" ] || { echo; echo "$tip_note"; }
    echo
    echo "Approve or reject it: $run_url"
  } >"$body"
fi

# Here, and on the run's page when GitHub Actions runs it.
{ echo "### $title"; echo; cat "$body"; } | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
[ "$send" = 1 ] || exit 0

sent=0
if [ -n "${RESEND_KEY:-}" ] && [ -n "${DEPLOY_SUMMARY_TO:-}" ]; then
  # From the domain sign-in codes come from (settings.env), already verified in Resend.
  from="${DEPLOY_SUMMARY_FROM:-$APP_NAME <deploys@$(sed -n 's/^SPORTS_FOLLOW_MAIL_FROM=.*@//p' deploy/settings.env)>}"
  jq -n --arg from "$from" --arg to "$DEPLOY_SUMMARY_TO" --arg subject "$title" --rawfile text "$body" \
      '{from: $from, to: ($to | split(",") | map(gsub("^ +| +$"; ""))), subject: $subject, text: $text}' |
    curl -fsS -m 20 --retry 3 -o /dev/null https://api.resend.com/emails \
      -H "Authorization: Bearer $RESEND_KEY" -H 'Content-Type: application/json' --data-binary @-
  echo "Emailed it." >&2
  sent=1
fi
if [ -n "${DEPLOY_SUMMARY_URL:-}" ]; then
  # ntfy: the notification opens the run, and its button is the way to approve.
  curl -fsS -m 20 --retry 3 -o /dev/null -H "Title: $title" -H "Tags: rocket" -H "Click: $run_url" \
    -H "Actions: view, Approve or reject, $run_url" --data-binary @"$body" "$DEPLOY_SUMMARY_URL"
  echo "Pushed it." >&2
  sent=1
fi
if [ "$sent" = 0 ]; then
  echo "::error::Nowhere to send it: set RESEND_KEY and DEPLOY_SUMMARY_TO, or DEPLOY_SUMMARY_URL (deploy/README.md, Deploying)"
  exit 1
fi
