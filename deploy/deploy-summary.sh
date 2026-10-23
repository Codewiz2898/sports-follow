#!/usr/bin/env bash
# What a deploy waiting for your approval brings: what to look at before approving (a database migration,
# server changes, a step by hand, Android changes it doesn't ship), the changes since the live commit with
# what each touches, the BHA issues they close, whether CI passed, and the link to approve it. GitHub
# Actions runs it (gh with GH_TOKEN, jq, curl, and this repo with its history); it works from a checkout too.
#   deploy-summary.sh [--send] [<commit> <run-url>]
# prints it as Markdown, for the deploy waiting now or for that Deploy run, and with --send also sends it:
# by email through Resend (RESEND_KEY, DEPLOY_SUMMARY_TO) and as a push to an ntfy topic
# (DEPLOY_SUMMARY_URL), whichever are set. With nothing waiting, it says so and sends nothing.
# deploy.yml sends the first deploy to wait; deploy-summary.yml, every morning, the one waiting then.
# What's live: the commit /api/health names, or, from an image built before it did, the last deploy GitHub
# recorded. The site's address: APP_URL (a repository variable), else the one GitHub recorded with that deploy.
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
joined() { paste -sd, - | sed 's/,/, /g'; }

# What a change touches, from its files (one a line), in words a review can go by. Tests, docs and tooling
# aren't named.
touches() {
  local path area seen=""
  while IFS= read -r path; do
    case $path in
      *.md | tests/* | docs/* | .claude/*) continue ;;
      web/src/admin/* | sports_follow/admin.py) area="admin panel" ;;
      web/*) area=website ;;
      sports_follow/adapters/*) area="data sources" ;;
      sports_follow/*) area="API and worker" ;;
      migrations/* | alembic.ini) area=database ;;
      mobile/* | android/*) area="Android app" ;;
      .github/* | deploy/deploy-summary.sh | deploy/cancel-replaced.sh) area="CI and deploys" ;;
      deploy/grafana/*) area=Grafana ;;
      Dockerfile | .dockerignore | requirements.txt | deploy/*) area="server setup" ;;
      *) continue ;;
    esac
    case "|$seen|" in *"|$area|"*) ;; *) seen="${seen:+$seen|}$area" ;; esac
  done
  echo "${seen//|/, }"
}

# What a deploy doesn't do for the files (one a line) that need it: the step to run by hand after it.
# Alloy reloads its config but not its environment.
by_hand() {
  local path
  while IFS= read -r path; do
    case $path in
      deploy/grafana/dashboards/* | deploy/grafana/apply-dashboards.sh) echo deploy/grafana/apply-dashboards.sh ;;
      deploy/grafana/*) echo deploy/grafana/apply-alerts.sh ;;
      deploy/systemd/* | deploy/server-setup.sh) echo "deploy/server-setup.sh on the server" ;;
      deploy/alloy/alloy.env) echo "sudo systemctl restart alloy on the server" ;;
    esac
  done | sort -u
}

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
done < <(api "deployments?environment=production&per_page=100" --jq '.[] | [(.id | tostring), .sha] | join("|")')
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
  for c in "$want" ${live:+"$live"}; do git cat-file -e "$c^{commit}" 2>/dev/null || git fetch --quiet origin "$c" || true; done
  range="" range_note=""
  if [ -z "$live" ] || ! git cat-file -e "$live^{commit}" 2>/dev/null; then
    range_note="What's live is unknown, so the changes, and what to check before approving, aren't listed."
  elif [ "$live" = "$want" ]; then
    range_note="It's the commit already live: approving it changes nothing."
  elif git merge-base --is-ancestor "$live" "$want"; then
    range="$live..$want"
  else
    range_note="It's older than what's live (${live:0:7}): approving it takes the site back, and the newer migrations, if any, stay in the database."
  fi

  # Each change on main: its pull request's title, or the commit's own subject, and what it touches. The
  # issues come from the pull requests' descriptions and every commit's message ("Fixes BHA-n", as
  # CLAUDE.md asks). Only file names are read: the checkout has no file contents (blob:none).
  changes="$tmp/changes" texts="$tmp/texts" android="$tmp/android" checks="$tmp/checks"
  : >"$changes"; : >"$texts"; : >"$android"; : >"$checks"
  if [ -n "$range" ]; then
    while IFS=$'\x1f' read -r sha subject; do
      area=$(git diff --no-renames --name-only "$sha^" "$sha" | touches)
      pr=$(sed -nE 's/^Merge pull request #([0-9]+) .*/\1/p; s/.*\(#([0-9]+)\)$/\1/p' <<<"$subject" | head -1)
      if [ -n "$pr" ] && api "pulls/$pr" >"$tmp/pr.json" 2>/dev/null; then
        ref="#$pr" change="$(jq -r .title "$tmp/pr.json")"
        jq -r '.body // ""' "$tmp/pr.json" >>"$texts"
      else
        ref="${sha:0:7}" change="$subject"
      fi
      echo "- $ref $change${area:+ ($area)}" >>"$changes"
      case "$area" in *"Android app"*) echo "$ref" >>"$android" ;; esac
    done < <(git log --first-parent --format='%H%x1f%s' "$range")
    git log --format=%B "$range" >>"$texts"

    # What to look at before approving, from every file the deploy changes (deploy.sh, deploy/README.md).
    files="$tmp/files"
    git diff --no-renames --name-only "$live" "$want" >"$files"
    migrations=$(git diff --no-renames --name-only --diff-filter=A "$live" "$want" -- migrations/versions |
      sed -E 's|.*/||; s|\.py$||; s|^[0-9a-f]+_||' | joined)
    server=$(grep -E '^(Dockerfile|\.dockerignore|requirements\.txt|deploy/(compose\.yaml|settings\.env|caddy/|backup\.sh|gateway/|alloy/config\.alloy))' "$files" | joined) || true
    secrets=$(grep -E '^deploy/(push|install)-secrets\.sh$' "$files" | joined) || true
    steps=$(by_hand <"$files" | joined)
    apps=$(joined <"$android")
    {
      [ -z "$migrations" ] || echo "- Database migration: $migrations. It runs before the new version starts, and a rollback leaves it in place."
      [ -z "$secrets" ] || echo "- The secrets scripts changed ($secrets): if a change needs a new key, run deploy/push-secrets.sh from the Mac before approving."
      [ -z "$server" ] || echo "- Server changes this deploy applies: $server."
      # The server runs the deploy.sh it has: deploy.sh reads all of itself before checking out the new commit.
      ! grep -qx deploy/deploy.sh "$files" || echo "- deploy/deploy.sh changed: this deploy still runs the old one, and the new one runs from the next deploy or rollback."
      [ -z "$steps" ] || echo "- Not done by the deploy; run after it: $steps."
      [ -z "$apps" ] || echo "- Android app changes ($apps) reach phones with the next app build, not with this deploy."
    } >"$checks"
    [ -s "$checks" ] || echo "- Nothing unusual: no database migration, no server changes, nothing to run by hand." >"$checks"
  fi
  count=$(wc -l <"$changes" | tr -d ' ')
  issues=$(grep -oiE '(^|[^a-z])(fix(e[sd])?|close[sd]?|resolve[sd]?) +BHA-[0-9]+((,| and|, and) +BHA-[0-9]+)*' "$texts" | grep -oE 'BHA-[0-9]+' |
    sort -t- -k2,2n -u | joined) || true

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
  # Markdown that reads as plain text too: a line ending in two spaces breaks there on the run's page.
  {
    echo "Waiting: ${want:0:7}, ${ci_said}${ci_url:+ ($ci_url)}  "
    echo "Live now: ${live:+${live:0:7}, }${live_how:-unknown}  "
    [ -z "$since" ] || echo "Waiting since $(when "$since"). Leave it, and ${site:-the site} stays as it is."
    echo
    if [ "$count" -gt 0 ]; then
      echo "Before you approve:"
      cat "$checks"
      echo
      echo "What it brings:"
      head -n "$MAX_CHANGES" "$changes"
      [ "$count" -le "$MAX_CHANGES" ] || echo "- and $((count - MAX_CHANGES)) more"
      echo
      echo "Closes: ${issues:-no BHA issue named}  "
      echo "Every commit: $GITHUB/compare/${live:0:12}...${want:0:12}"
    else
      echo "$range_note"
    fi
    [ -z "$tip_note" ] || { echo; echo "$tip_note"; }
    echo
    echo "Approve or reject it: $run_url"
  } >"$body"
fi

# Here, and on the run's page when GitHub Actions runs it, where each pull request is a link.
{ echo "### $title"; echo; cat "$body"; }
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  { echo "### $title"; echo; sed -E "s|^- #([0-9]+) |- [#\1]($GITHUB/pull/\1) |" "$body"; } >>"$GITHUB_STEP_SUMMARY"
fi
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
  # ntfy: the notification opens the run, and its button is the way to approve. ntfy keeps 4,096 bytes
  # as a message; past that, the summary is cut at a line and the rest is on the run's page.
  push="$tmp/push"
  if [ "$(wc -c <"$body")" -le 4000 ]; then cp "$body" "$push"
  else { head -c 3900 "$body" | sed '$d'; echo "…"; echo; echo "The rest: $run_url"; } >"$push"; fi
  curl -fsS -m 20 --retry 3 -o /dev/null -H "Title: $title" -H "Tags: rocket" -H "Click: $run_url" \
    -H "Actions: view, Approve or reject, $run_url" --data-binary @"$push" "$DEPLOY_SUMMARY_URL"
  echo "Pushed it." >&2
  sent=1
fi
if [ "$sent" = 0 ]; then
  echo "::error::Nowhere to send it: set RESEND_KEY and DEPLOY_SUMMARY_TO, or DEPLOY_SUMMARY_URL (deploy/README.md, Deploying)"
  exit 1
fi
