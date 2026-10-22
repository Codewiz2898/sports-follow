"""deploy/cancel-replaced.sh, as deploy.yml's newest job runs it, against a made-up gh: which waiting Deploy runs a
newer one cancels, and which it leaves alone. The runs change from one look to the next as the script sleeps; and,
as on 22 Oct 2026 (BHA-663), GitHub's list of waiting runs (status=waiting) comes back empty. Nothing reaches
GitHub."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "deploy" / "cancel-replaced.sh"
MAIN = [c * 40 for c in "abcde"]  # main's commits, oldest first
C1, C2, C3, C4 = MAIN[1:]
ME = 500  # this run: for C3, started at 16:59:00

# gh api, as the script calls it. A run's state at each look (the last one holds): "first" (its first job runs),
# "pending" (its deploy job queued for the production group), "gate" (waiting for approval), "deploying" or "done".
GH = r"""#!{python}
import json, os, subprocess, sys
fake = os.environ["FAKE"]
args, method, jq, path = sys.argv[2:], "GET", None, None
while args:
    a = args.pop(0)
    if a in ("-X", "--method"): method = args.pop(0)
    elif a in ("--jq", "-q"): jq = args.pop(0)
    elif a != "--silent": path = a
with open(f"{fake}/calls", "a") as f: f.write(f"{method} {path}\n")
path = path.removeprefix("repos/o/r/")
look = int(open(f"{fake}/looks").read()) if os.path.exists(f"{fake}/looks") else 0
cancelled = open(f"{fake}/cancelled").read().split() if os.path.exists(f"{fake}/cancelled") else []
scenario = json.load(open(f"{fake}/scenario.json"))
runs = {str(r["id"]): r for r in scenario["runs"]}
def state(run):
    return "done" if str(run["id"]) in cancelled else run["states"][min(look, len(run["states"]) - 1)]
parts = path.split("?")[0].split("/")
if path.startswith("actions/workflows/deploy.yml/runs"):
    listed = [] if "status=waiting" in path else sorted(runs.values(), key=lambda r: r["started"], reverse=True)
    out = {"workflow_runs": [{"id": r["id"], "event": r["event"], "run_started_at": r["started"],
                              "display_title": r["title"],
                              "status": "completed" if state(r) == "done" else "queued"} for r in listed]}
elif parts[:2] == ["actions", "runs"] and len(parts) == 3:
    out = {"id": int(parts[2]), "run_started_at": runs[parts[2]]["started"]}
elif parts[:2] == ["actions", "runs"] and parts[3] == "jobs":
    s = state(runs[parts[2]])
    deploy = {"pending": "pending", "gate": "waiting", "deploying": "in_progress", "done": "completed"}.get(s)
    out = {"jobs": [{"name": "The newest commit CI passed", "status": "in_progress" if s == "first" else "completed"}]
                   + ([{"name": "deploy", "status": deploy}] if deploy else [])}
elif parts[:2] == ["actions", "runs"] and parts[3] == "pending_deployments":
    out = [{"environment": {"name": "production"}}] if state(runs[parts[2]]) == "gate" else []
elif parts[:2] == ["actions", "runs"] and parts[3] == "cancel" and method == "POST":
    run = runs[parts[2]]
    if run.get("cancel_fails") or state(run) == "done":
        sys.exit("gh: Cannot cancel a workflow run that is completed. (HTTP 409)")
    with open(f"{fake}/cancelled", "a") as f: f.write(f"{run['id']}\n")
    sys.exit(0)
elif path.startswith("actions/workflows/ci.yml/runs"):
    if scenario.get("ci_fails"):
        sys.exit("gh: Server Error (HTTP 502)")
    out = {"workflow_runs": [{"head_sha": scenario["ci_newest"]}]}
elif parts[0] == "compare":
    main = scenario["main"]
    base, head = (main.index(c) for c in parts[1].split("..."))
    out = {"status": "identical" if base == head else "ahead" if head > base else "behind"}
else:
    sys.exit(f"gh: Not Found (HTTP 404): {path}")
text = json.dumps(out)
if jq:
    text = subprocess.run(["jq", "-r", jq], input=text, capture_output=True, text=True, check=True).stdout
print(text, end="" if jq else "\n")
"""

# The script's sleep between looks: the next look sees the runs' next state.
SLEEP = """#!/usr/bin/env bash
echo "sleep $*" >>"$FAKE/calls"
echo $(( $(cat "$FAKE/looks" 2>/dev/null || echo 0) + 1 )) >"$FAKE/looks"
"""


def deploy_run(run_id: int, commit: str | None, *states: str, started: str = "16:41:22", event: str = "workflow_run",
               **extra) -> dict:
    title = f"Deploy {commit}" if commit else "Roll back to the previous deploy"
    return {"id": run_id, "event": event, "started": f"2026-10-22T{started}Z", "title": title, "states": list(states), **extra}


def cancel_replaced(tmp_path: Path, *runs: dict) -> tuple[subprocess.CompletedProcess, list[str], int]:
    """Runs the script for this run (C3); gives its output, the calls it made, and the count it wrote."""
    fake = tmp_path / "fake"
    (fake / "bin").mkdir(parents=True)
    (fake / "bin" / "gh").write_text(GH.replace("{python}", sys.executable))
    (fake / "bin" / "sleep").write_text(SLEEP)
    for tool in ("gh", "sleep"):
        (fake / "bin" / tool).chmod(0o755)
    me = deploy_run(ME, C3, "first", started="16:59:00")
    (fake / "scenario.json").write_text(json.dumps({"main": MAIN, "runs": [me, *runs]}))
    output = tmp_path / "output"
    env = {**os.environ, "PATH": f"{fake / 'bin'}:{os.environ['PATH']}", "FAKE": str(fake), "GH_TOKEN": "x",
           "GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": str(ME), "GITHUB_OUTPUT": str(output)}
    done = subprocess.run(["bash", str(SCRIPT), C3], env=env, capture_output=True, text=True, timeout=60)
    calls = (fake / "calls").read_text().splitlines() if (fake / "calls").exists() else []
    count = re.findall(r"^count=(\d+)$", output.read_text(), re.M) if output.exists() else []
    return done, calls, int(count[-1]) if count else -1


def cancels(calls: list[str]) -> list[int]:
    return [int(c.split("/")[-2]) for c in calls if c.startswith("POST ")]


def sleeps(calls: list[str]) -> int:
    return sum(c.startswith("sleep ") for c in calls)


@pytest.fixture(autouse=True)
def _needs_jq():
    if subprocess.run(["sh", "-c", "command -v jq"], capture_output=True).returncode:
        pytest.skip("needs jq, as gh's --jq is")


def test_a_run_waiting_for_approval_is_cancelled_though_githubs_list_of_waiting_runs_misses_it(tmp_path):
    # BHA-663, 16:59:06: 270710d's run had waited 17 minutes, but status=waiting listed nothing, so 259d302's run
    # queued behind it and approving the waiting one deployed the older commit.
    done, calls, count = cancel_replaced(tmp_path, deploy_run(101, C2, "gate"))
    assert done.returncode == 0, done.stderr
    assert cancels(calls) == [101] and count == 1 and sleeps(calls) == 0
    # Read just before its cancel: a run is cancelled only while GitHub says it waits for approval.
    assert calls[calls.index("POST repos/o/r/actions/runs/101/cancel") - 1] == "GET repos/o/r/actions/runs/101/pending_deployments"


def test_a_run_that_reaches_the_approval_gate_just_after_a_look_is_cancelled_too(tmp_path):
    # BHA-663, 16:41:43: af5a89e's run looked in the second 270710d's deploy job started to wait, and missed it.
    done, calls, count = cancel_replaced(tmp_path, deploy_run(101, C2, "first", "first", "gate"))
    assert done.returncode == 0, done.stderr
    assert cancels(calls) == [101] and count == 1 and sleeps(calls) == 2
    assert "on its way to the approval gate" in done.stdout


def test_a_run_queued_behind_the_waiting_one_is_cancelled_once_it_waits_in_its_place(tmp_path):
    # Cancelling the waiting run frees the production group, and the run queued behind it goes up to the gate.
    done, calls, count = cancel_replaced(tmp_path, deploy_run(101, C1, "gate", started="16:18:21"),
                                         deploy_run(102, C2, "pending", "gate"))
    assert done.returncode == 0, done.stderr
    assert cancels(calls) == [101, 102] and count == 2


def test_runs_it_doesnt_replace_and_a_deploy_that_runs_are_left_alone(tmp_path):
    done, calls, count = cancel_replaced(
        tmp_path,
        deploy_run(101, C1, "deploying", started="16:18:21"),               # approved: deploying now
        deploy_run(102, C2, "gate", event="workflow_dispatch"),               # started by hand
        deploy_run(103, None, "gate", event="workflow_dispatch"),             # a rollback by hand
        deploy_run(104, C4, "gate"),                                          # a newer commit's
        deploy_run(105, C2, "gate", started="16:59:30"),                     # started after this one
    )
    assert done.returncode == 0, done.stderr
    assert cancels(calls) == [] and count == 0 and sleeps(calls) == 0
    assert '"Deploy ' + C1 + '" (run 101) is deploying: left alone' in done.stdout


def test_a_run_queued_behind_a_deploy_that_runs_isnt_waited_for(tmp_path):
    # It can't reach the gate before this run's deploy job takes its place in the production group's queue.
    done, calls, count = cancel_replaced(tmp_path, deploy_run(101, C1, "deploying", started="16:18:21"),
                                         deploy_run(102, C2, "pending", "pending", "gate"))
    assert done.returncode == 0, done.stderr
    assert cancels(calls) == [] and sleeps(calls) == 0


def test_a_cancel_that_fails_only_warns_and_the_run_is_read_again(tmp_path):
    # Approved in the second between its read and its cancel: the cancel fails, and the next look finds it deploying.
    done, calls, count = cancel_replaced(tmp_path, deploy_run(101, C2, "gate", "deploying", cancel_fails=True))
    assert done.returncode == 0, done.stderr
    assert cancels(calls) == [101] and count == 0 and sleeps(calls) == 1
    assert "::warning::Couldn't cancel run 101" in done.stdout and "is deploying: left alone" in done.stdout


def test_a_run_that_doesnt_reach_the_gate_is_watched_for_two_minutes_then_left_with_a_warning(tmp_path):
    done, calls, count = cancel_replaced(tmp_path, deploy_run(101, C2, "first"))
    assert done.returncode == 0, done.stderr
    assert cancels(calls) == [] and count == 0
    assert sleeps(calls) == 11 and calls.count("sleep 10") == 11
    assert "::warning::A Deploy run this one replaces hadn't reached the approval gate after 2 minutes" in done.stdout


def guard(tmp_path: Path, ci_newest: str, **scenario) -> subprocess.CompletedProcess:
    """deploy.yml's deploy job's first step, as GitHub runs it, for an approved run of C3."""
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text()
    step = re.search(r"- name: Still the newest commit CI passed\n(?:.*\n)*?        run: \|\n((?:          .*\n|\n)+)", workflow)
    assert step, "the deploy job's guard step"
    fake = tmp_path / "fake"
    (fake / "bin").mkdir(parents=True)
    (fake / "bin" / "gh").write_text(GH.replace("{python}", sys.executable))
    (fake / "bin" / "gh").chmod(0o755)
    (fake / "scenario.json").write_text(json.dumps({"main": MAIN, "runs": [], "ci_newest": ci_newest, **scenario}))
    env = {**os.environ, "PATH": f"{fake / 'bin'}:{os.environ['PATH']}", "FAKE": str(fake), "GITHUB_REPOSITORY": "o/r",
           "SHA": C3}
    script = "".join(line[10:] + "\n" for line in step.group(1).splitlines())
    return subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script], env=env,
                          capture_output=True, text=True, timeout=30)


def test_a_run_approved_after_a_newer_commit_passed_ci_stops_without_deploying(tmp_path):
    # BHA-663: approving 270710d's run, which 259d302's had missed, would have put the older commit live and kept
    # 259d302's queued behind it for a second approval.
    done = guard(tmp_path, C4)
    assert done.returncode == 1 and f"::error::{C4[:7]} passed CI after {C3[:7]}, so this run deployed nothing" in done.stdout


@pytest.mark.parametrize("ci_newest, scenario", [(C3, {}), (C2, {}), (C4, {"ci_fails": True})])
def test_the_newest_commits_run_deploys_and_a_read_that_fails_doesnt_stop_it(tmp_path, ci_newest, scenario):
    done = guard(tmp_path, ci_newest, **scenario)
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_job_it_reads_is_deploy_ymls_deploy_job():
    # The script finds a run's deploy job by its name: deploy.yml's job `deploy`, with no name of its own.
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text()
    job = re.search(r"^  deploy:\n(.*?)(?=^  \S|\Z)", workflow, re.M | re.S)
    assert job and not re.search(r"^    name:", job.group(1), re.M)
    assert re.search(r"run: deploy/cancel-replaced\.sh \"\$SHA\"", workflow)
