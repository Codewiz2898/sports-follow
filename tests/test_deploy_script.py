"""deploy/deploy.sh, run as on the server, against a made-up docker and git: what it says is live after a deploy or a
rollback goes wrong, where it leaves the checkout, and what it waits for before calling a deploy healthy. The
script runs from a copy in a folder of the test's own; nothing reaches Docker, GitHub or the repository."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).parent.parent / "deploy"
OLD, NEW = "a" * 40, "b" * 40

# docker, git and the rest, as deploy.sh calls them. Each call is logged; an image's containers answer `docker
# inspect` from health/<commit>/<service> (default running and healthy), the commit being the image tagged latest.
STUB = r"""#!/usr/bin/env bash
name=$(basename "$0")
echo "$name $*" >>"$FAKE/calls"
latest() { cat "$FAKE/latest" 2>/dev/null; }
case "$name" in
  sleep|sudo|flock) exit 0 ;;
  git)
    case "$1" in
      rev-parse) want="${@: -1}"; want="${want%^\{commit\}}"; [ "$want" = origin/main ] && want="$FAKE_MAIN"; echo "$want" ;;
      log) echo "${@: -1} a commit" ;;
    esac
    exit 0 ;;
  docker)
    if [ "$1" = compose ]; then
      shift 3  # compose -f deploy/compose.yaml
      case "$1" in
        build) [ -n "${FAKE_BUILD_FAILS:-}" ] && exit 1; echo "$SPORTS_FOLLOW_COMMIT" >"$FAKE/latest" ;;
        # Long, so a reader that stops at its match (grep -q) closes the pipe while this still writes, as it can
        # under a real docker: under pipefail that failed the curator check, now every time rather than by chance.
        config) printf '%s\n' ${FAKE_SERVICES:-caddy api worker curator gateway backup migrate postgres redis} $(seq 100000) ;;
        ps) [ "$2" = -aq ] && echo "id-$3" ;;
        exec) [ "$3" = backup ] && cat deploy/backup.sh ;;
      esac
      exit 0
    fi
    case "$1" in
      tag) [ "$3" = sports-follow:latest ] && echo "${2#sports-follow:}" >"$FAKE/latest" ;;
      inspect) svc="${@: -1}"; svc="${svc#id-}"; cat "$FAKE/health/$(latest)/$svc" 2>/dev/null \
                 || { [ "$svc" = api ] && echo running,0,healthy || echo running,0,; } ;;
    esac
    exit 0 ;;
esac
"""


@pytest.fixture
def server(tmp_path):
    """A server's checkout of deploy/ (the script and the files it reads), its state folder, and the made-up tools."""
    app = tmp_path / "app"
    (app / "deploy").mkdir(parents=True)
    for f in ("deploy.sh", "backup.sh"):
        shutil.copy(DEPLOY / f, app / "deploy" / f)
    fake = tmp_path / "fake"
    (fake / "bin").mkdir(parents=True)
    for tool in ("docker", "git", "sleep", "sudo", "flock"):
        (fake / "bin" / tool).write_text(STUB)
        (fake / "bin" / tool).chmod(0o755)
    state = tmp_path / "state" / "sports-follow"
    state.mkdir(parents=True)
    return tmp_path


def health(server: Path, commit: str, **services: str) -> None:
    """How a commit's containers answer: api="running,0,unhealthy", curator="restarting,3,"."""
    folder = server / "fake" / "health" / commit
    folder.mkdir(parents=True, exist_ok=True)
    for svc, status in services.items():
        (folder / svc).write_text(status + "\n")


def deploy(server: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    run_env = {**os.environ, "PATH": f"{server / 'fake' / 'bin'}:{os.environ['PATH']}", "FAKE": str(server / "fake"), "FAKE_MAIN": NEW,
               "XDG_STATE_HOME": str(server / "state"), "HEALTH_TIMEOUT": "3", **env}
    run_env.pop("SSH_ORIGINAL_COMMAND", None)
    return subprocess.run(["bash", str(server / "app" / "deploy" / "deploy.sh"), *args], env=run_env, capture_output=True, text=True, timeout=60)


def state(server: Path, name: str) -> str | None:
    path = server / "state" / "sports-follow" / name
    return path.read_text().strip() if path.exists() else None


def live_on(server: Path, commit: str, previous: str | None = None) -> None:
    (server / "state" / "sports-follow" / "live").write_text(commit + "\n")
    if previous:
        (server / "state" / "sports-follow" / "previous").write_text(previous + "\n")
    (server / "fake" / "latest").write_text(commit + "\n")


def checkouts(server: Path) -> list[str]:
    calls = (server / "fake" / "calls").read_text().splitlines()
    return [c.split()[-1] for c in calls if c.startswith("git checkout")]


def test_a_rollback_that_isnt_healthy_still_says_what_runs(server):
    # BUG-271: the old image runs again, healthy or not, but `live` went on naming the bad deploy, so `deploy.sh status`,
    # the deploy summary and a later rollback (back to what already runs) all went wrong.
    live_on(server, NEW, previous=OLD)
    health(server, OLD, api="running,0,unhealthy")
    done = deploy(server, "rollback")
    assert done.returncode == 1 and "isn't healthy either" in done.stderr
    assert (state(server, "live"), state(server, "previous")) == (OLD, None)


def test_a_deploy_that_fails_and_whose_rollback_fails_too_names_the_old_commit_live(server):
    # BUG-271's trigger: the new image doesn't get healthy, and the old one, started again, doesn't in time either.
    live_on(server, OLD)
    health(server, NEW, api="running,0,unhealthy")
    health(server, OLD, api="running,0,unhealthy")
    done = deploy(server, "deploy", NEW)
    assert done.returncode == 1 and "going back to" in done.stdout
    assert (state(server, "live"), state(server, "previous")) == (OLD, None)
    assert checkouts(server)[-1] == OLD


def test_a_build_that_fails_leaves_the_checkout_on_the_commit_that_runs(server):
    # BUG-224: the checkout moved to the new commit before the build, and stayed there when it failed, so Caddy's and
    # Alloy's config folders held the new files while the old code ran, for a restart or a reboot to pick up.
    live_on(server, OLD)
    done = deploy(server, "deploy", NEW, FAKE_BUILD_FAILS="1")
    assert done.returncode == 1 and "nothing was replaced" in done.stderr
    assert checkouts(server) == [NEW, OLD] and state(server, "live") == OLD


def test_a_curator_that_keeps_crashing_sends_the_deploy_back(server):
    # BUG-224: only the API and the worker were checked, so a curator crash-looping on the new code stayed live.
    live_on(server, OLD)
    health(server, NEW, curator="restarting,3,")
    done = deploy(server, "deploy", NEW)
    assert done.returncode == 1 and "curator: restarting,3," in done.stderr
    assert state(server, "live") == OLD


@pytest.mark.parametrize("services", ["caddy api worker gateway backup migrate postgres redis", None])
def test_a_healthy_deploy_goes_live(server, services):
    # With a curator that runs, or a commit from before there was one.
    live_on(server, OLD)
    done = deploy(server, "deploy", NEW, **({"FAKE_SERVICES": services} if services else {}))
    assert done.returncode == 0, done.stderr
    assert (state(server, "live"), state(server, "previous")) == (NEW, OLD) and checkouts(server) == [NEW]


def workflow_step(name: str) -> str:
    """The script of a step of .github/workflows/deploy.yml, by its id: its `run: |` block, as GitHub hands it to bash."""
    lines = (DEPLOY.parent / ".github" / "workflows" / "deploy.yml").read_text().splitlines()
    at = next(i for i, line in enumerate(lines) if line.strip() == f"id: {name}")
    start = next(i for i in range(at, len(lines)) if lines[i].strip() == "run: |") + 1
    indent = len(lines[start]) - len(lines[start].lstrip())
    block = []
    for line in lines[start:]:
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        block.append(line[indent:])
    return "\n".join(block) + "\n"


@pytest.mark.parametrize("event", ["workflow_dispatch", "workflow_run"])
def test_no_run_deploys_while_production_has_no_reviewer_however_it_was_started(tmp_path, event):
    # BUG-221: only a run after CI looked for production's required reviewer. With the reviewers cleared, a run started
    # by hand (gh workflow run deploy.yml, which a Claude session may start) would have deployed with no one approving.
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "gh").write_text('#!/usr/bin/env bash\ncase "$*" in *environments/production*) echo "$FAKE_REVIEWERS" ;; *) echo "" ;; esac\n')
    (fake / "gh").chmod(0o755)

    def newest(reviewers: int) -> subprocess.CompletedProcess:
        output = tmp_path / "output"
        output.write_text("")
        env = {**os.environ, "PATH": f"{fake}:{os.environ['PATH']}", "FAKE_REVIEWERS": str(reviewers), "GITHUB_EVENT_NAME": event,
               "GITHUB_REPOSITORY": "o/r", "GITHUB_OUTPUT": str(output), "SHA": NEW, "ACTION": "deploy", "GH_TOKEN": "x"}
        return subprocess.run(["bash", "-e", "-c", workflow_step("newest")], env=env, capture_output=True, text=True, timeout=60)

    stopped = newest(0)
    assert stopped.returncode == 1 and "no required reviewer" in stopped.stdout
    went = newest(1)
    assert went.returncode == 0, went.stdout + went.stderr
    assert (tmp_path / "output").read_text().strip() == "go=true"
