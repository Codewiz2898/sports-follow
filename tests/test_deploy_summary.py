"""deploy/deploy-summary.sh: what a deploy brings, and when it's sent, in a public and a private repository.

It runs as GitHub Actions runs it, from a copy in a throwaway repository with three commits (live, then two
changes), against a gh and a curl that answer as GitHub and the site would and write down what they send.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
pytestmark = pytest.mark.skipif(not (shutil.which("jq") and shutil.which("git")), reason="needs jq and git")

GH = """#!/bin/sh
# gh api <path> [--jq ...]: the answers the summary asks for, already in the shape its --jq makes.
case "$2" in
  repos/o/r) echo "$FAKE_PRIVATE" ;;
  repos/o/r/commits/main) echo "$FAKE_TIP" ;;
  repos/o/r/actions/workflows/ci.yml/runs\\?branch=main*) echo "$FAKE_TIP" ;;
  repos/o/r/actions/workflows/ci.yml/runs*) echo "success|https://ci.test/run" ;;
  repos/o/r/actions/workflows/deploy.yml/runs\\?status=waiting*) [ -z "$FAKE_WAITING" ] || echo "$FAKE_WAITING" ;;
  repos/o/r/deployments*) ;;
  *) exit 1 ;;
esac
"""

CURL = """#!/bin/sh
# The site's /api/health says what's live; anything else is a send, written down with what it sends.
for a in "$@"; do case $a in */api/health) echo "{\\"commit\\": \\"$FAKE_LIVE\\"}"; exit 0 ;; esac; done
{ echo "curl $*"; for a in "$@"; do case $a in @*) cat "${a#@}" ;; esac; done; } >>"$FAKE_SENT"
"""


@pytest.fixture
def repo(tmp_path):
    work = tmp_path / "repo"
    (work / "deploy").mkdir(parents=True)
    (work / "sports_follow").mkdir()
    shutil.copy(ROOT / "deploy" / "deploy-summary.sh", work / "deploy" / "deploy-summary.sh")
    (work / "sports_follow" / "app.json").write_text(json.dumps({"name": "stann"}))
    (work / "deploy" / "settings.env").write_text("SPORTS_FOLLOW_MAIL_FROM=stann <hello@stann.test>\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, text in (("gh", GH), ("curl", CURL)):
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "HOME": str(tmp_path),
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.test", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t.test", "GITHUB_REPOSITORY": "o/r", "APP_URL": "https://stann.test",
           "DEPLOY_SUMMARY_URL": "https://ntfy.test/topic", "FAKE_SENT": str(tmp_path / "sent")}
    for key in ("GITHUB_STEP_SUMMARY", "RESEND_KEY", "DEPLOY_SUMMARY_TO", "GITHUB_SERVER_URL"):
        env.pop(key, None)
    def git(*args):
        return subprocess.run(["git", *args], cwd=work, env=env, check=True, capture_output=True, text=True)

    git("init", "-q", "-b", "main")
    shas = []
    for i, name in enumerate(["live", "first change", "second change"]):
        (work / "sports_follow" / f"f{i}.py").write_text(name)
        git("add", "-A")
        git("commit", "-q", "-m", name)
        shas.append(git("rev-parse", "HEAD").stdout.strip())
    env |= {"FAKE_LIVE": shas[0], "FAKE_TIP": shas[2], "FAKE_PRIVATE": "true", "FAKE_WAITING": ""}
    return work, env, shas, tmp_path / "sent"


def summary(repo, *args, **fake):
    work, env, _, sent = repo
    done = subprocess.run(["bash", str(work / "deploy" / "deploy-summary.sh"), *args], cwd=work,
                          env={**env, **fake}, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout + done.stderr, sent.read_text() if sent.exists() else ""


def test_a_private_repositorys_first_change_is_sent_as_ready(repo):
    _, _, (live, first, _), _ = repo
    out, sent = summary(repo, "--send-first", first, "https://github.com/o/r/actions/runs/1", FAKE_TIP=first)
    assert "stann: a deploy is ready for you (1 change)" in out
    assert "Before you deploy:" in out and "Before you approve" not in out
    assert f"Deploy it: https://github.com/o/r/actions/workflows/deploy.yml → Run workflow, with {first} as the commit" in out
    assert "Pushed it." in out
    assert "Actions: view, Deploy it, https://github.com/o/r/actions/workflows/deploy.yml" in sent
    assert "Click: https://github.com/o/r/actions/runs/1" in sent and f"Ready: {first[:7]}" in sent


def test_a_private_repositorys_later_change_waits_for_the_morning(repo):
    _, _, (_, _, second), _ = repo
    out, sent = summary(repo, "--send-first", second, "https://github.com/o/r/actions/runs/2")
    assert "stann: a deploy is ready for you (2 changes)" in out
    assert "comes with the morning's summary" in out and sent == ""


def test_the_morning_sends_what_is_ready_and_nothing_once_it_is_live(repo):
    _, _, (live, _, second), _ = repo
    out, sent = summary(repo, "--send")
    assert "a deploy is ready for you (2 changes)" in out and f"with {second} as the commit" in sent
    out, sent_again = summary(repo, "--send", FAKE_LIVE=second)
    assert f"Nothing to deploy: {second[:7]} is live." in out and sent_again == sent


def test_a_run_bharath_started_says_what_it_deploys_and_sends_nothing(repo):
    _, _, (_, first, _), _ = repo
    out, sent = summary(repo, "--deploying", first, "https://github.com/o/r/actions/runs/3")
    assert "stann: deploying " + first[:7] + " (1 change)" in out and "Deploy it:" not in out
    assert sent == ""


def test_a_public_repository_still_asks_for_approval(repo):
    _, _, (_, first, second), _ = repo
    out, sent = summary(repo, "--send", second, "https://github.com/o/r/actions/runs/4", FAKE_PRIVATE="false")
    assert "stann: a deploy is waiting for you (2 changes)" in out and "Before you approve:" in out
    assert "Approve or reject it: https://github.com/o/r/actions/runs/4" in sent
    assert "Actions: view, Approve or reject, https://github.com/o/r/actions/runs/4" in sent
    # The morning's: the waiting run's name carries its commit.
    out, _ = summary(repo, FAKE_PRIVATE="false", FAKE_WAITING=f"9|Deploy {first}|2026-10-21T03:00:00Z")
    assert "a deploy is waiting for you (1 change)" in out and f"Waiting: {first[:7]}" in out
