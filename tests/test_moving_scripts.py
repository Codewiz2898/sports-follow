"""deploy/migrate-to-server.sh and deploy/seed-registry.sh, run as from the Mac against a made-up ssh, docker and curl:
moving to a server happens once, and the registry copy never replaces a registry a running server has, nor leaves
it half copied. Nothing reaches a server, Docker or the network."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).parent.parent / "deploy"

# ssh, docker and curl as the scripts call them; each call, and what was piped into it, is logged. The server answers
# from FAKE_LIVE (the commit it runs, if any), FAKE_WIKIDATA (its registry's Wikidata athletes) and FAKE_COUNT (all of
# its athletes, as the Mac's).
STUB = r"""#!/usr/bin/env bash
name=$(basename "$0")
printf '%s %s\n' "$name" "$*" >>"$FAKE/calls"
case "$name" in
  ssh)
    [ -t 0 ] || cat >>"$FAKE/piped"
    remote="${@: -1}"
    case "$remote" in
      *DOMAIN=*) echo stann.test ;;
      *sports-follow/live*) [ -n "${FAKE_LIVE:-}" ] && echo "$FAKE_LIVE" ;;
      *"qid like 'Q%'"*) echo "${FAKE_WIKIDATA:-0}" ;;
      *"count(*) from athlete"*) echo "${FAKE_COUNT:-320000}" ;;
    esac ;;
  docker)
    case "$*" in
      *pg_dump*) echo "COPY public.athlete (id, qid) FROM stdin;"; printf '1\tQ1\n\\.\n' ;;
      *"count(*) from athlete"*) echo "${FAKE_COUNT:-320000}" ;;
    esac ;;
  curl)
    case "$*" in
      *api/health*) echo '{"ok":true,"db":true,"redis":true}' ;;
      *api/credits*) echo '{"sources":["Sportmonks","Lichess"]}' ;;
    esac ;;
esac
exit 0
"""


@pytest.fixture
def mac(tmp_path):
    """The Mac: a repo root with the development stack, its tokens, and the made-up tools."""
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "docker-compose.yml").write_text("services: {}\n")
    config = tmp_path / "config"
    config.mkdir()
    for f in ("sportmonks-token", "lichess-token", "openrouter-key"):
        (config / f).write_text("x\n")
    fake = tmp_path / "fake"
    (fake / "bin").mkdir(parents=True)
    for tool in ("ssh", "docker", "curl"):
        (fake / "bin" / tool).write_text(STUB)
        (fake / "bin" / tool).chmod(0o755)
    return tmp_path


def run(mac: Path, script: str, *args: str, **env: str) -> subprocess.CompletedProcess:
    run_env = {**os.environ, "PATH": f"{mac / 'fake' / 'bin'}:{os.environ['PATH']}", "FAKE": str(mac / "fake"), "SPORTS_FOLLOW_CONFIG": str(mac / "config"), **env}
    return subprocess.run(["bash", str(DEPLOY / script), *args], cwd=mac / "repo", env=run_env, capture_output=True, text=True, timeout=60)


def calls(mac: Path) -> str:
    path = mac / "fake" / "calls"
    return path.read_text() if path.exists() else ""


def test_moving_off_the_mac_refuses_a_server_already_live(mac):
    # BUG-222: "safe to run again", but run against stann.in it would have pushed the Mac's tokens, deployed main's tip
    # with nobody's approval, and swapped the live registry for the Mac's.
    for args in ((), ("--check",)):
        done = run(mac, "migrate-to-server.sh", "deploy@203.0.113.7", *args, FAKE_LIVE="c" * 40)
        assert done.returncode == 1 and "already runs ccccccc" in done.stderr
    assert "deploy.sh deploy" not in calls(mac) and "install-secrets" not in calls(mac) and "pg_dump" not in calls(mac)


def test_moving_off_the_mac_moves_a_new_server(mac):
    done = run(mac, "migrate-to-server.sh", "deploy@203.0.113.7")
    assert done.returncode == 0, done.stderr
    assert "/opt/sports-follow/deploy/deploy.sh deploy" in calls(mac) and "pg_dump" in calls(mac)


def test_the_registry_copy_leaves_a_servers_own_registry_alone(mac):
    # BUG-222: the copy emptied the server's registry for the Mac's, whatever it held: athletes its operators added and
    # ids it learned, gone; athletes they removed, back.
    done = run(mac, "seed-registry.sh", "deploy@203.0.113.7", FAKE_WIKIDATA="318000")
    assert done.returncode == 1 and "already has 318000 athletes" in done.stderr
    assert "truncate" not in calls(mac) and "pg_dump" not in calls(mac)


@pytest.mark.parametrize("wikidata, args", [("0", ()), ("318000", ("--replace",))])
def test_the_registry_copy_is_one_transaction_that_keeps_removed_athletes_out(mac, wikidata, args):
    # BUG-222: the server's registry was emptied in one command and filled in another, over SSH: a copy that failed in
    # between left search empty. Now the emptying, the copy and taking out the athletes the server's operators removed
    # are one transaction.
    done = run(mac, "seed-registry.sh", "deploy@203.0.113.7", *args, FAKE_WIKIDATA=wikidata)
    assert done.returncode == 0, done.stderr
    [load] = [line for line in calls(mac).splitlines() if line.startswith("ssh") and "truncate" in line]
    assert "--single-transaction" in load and "ON_ERROR_STOP=1" in load and "athlete_override" in load and "pg_restore" not in load
    assert "(count(*) = 320000)" in load  # every row the Mac has, or none: a copy cut short changes nothing
    assert "COPY public.athlete" in (mac / "fake" / "piped").read_text()
