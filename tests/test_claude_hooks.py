"""The hooks in .claude/hooks: what the reviewer agent may run, and when `gh pr create` waits for a review.

Each hook runs as Claude Code runs it, a script reading the tool call as JSON: 0 lets it through, 2 blocks it.
On a Mac, `HOOK_PYTHON=/usr/bin/python3` runs them on the Python Claude Code uses.
"""

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).parent.parent / ".claude" / "hooks"
PYTHON = os.environ.get("HOOK_PYTHON", sys.executable)


def run(hook: str, command: str, cwd: Path | str = "/") -> int:
    call = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}
    return subprocess.run([PYTHON, str(HOOKS / hook)], input=json.dumps(call), capture_output=True, text=True).returncode


@pytest.mark.parametrize("path", sorted(HOOKS.glob("*.py")), ids=lambda p: p.name)
def test_the_hooks_run_on_the_macs_python(path):
    # Claude Code runs them with the system python3, 3.9 on macOS; a hook that can't start lets everything through.
    ast.parse(path.read_text(), feature_version=(3, 9))


@pytest.mark.parametrize("command", [
    "git fetch -q origin && git diff origin/main...HEAD",
    'git -C "/a b" log --format="%h > %s" -5',
    "git stash list",
    "git stash show -p stash@{0}",
    "git reflog HEAD",
    "gh pr view 40 --json body && gh pr diff 40",
    "gh api repos/o/r/pulls/40/files",
    "gh api graphql -f query='query { viewer { login } }'",
    "grep -rn alembic sports_follow 2>/dev/null | wc -l",
    "grep -n '>' sports_follow/server.py",
    "grep -rn '&&' . ; cut -d '|' -f1 f; tr ';' '\\n' < f",
    "awk '$3 > 100' f",
    ".venv/bin/alembic heads",
    "python -m sports_follow.players list",
    "git branch --show-current",
    "git branch -a --contains abc123",
    "git tag --list",
    "cd /x && .venv/bin/python -m pytest -q tests/test_budget.py 2>&1 | tail -5",
    "ruff check . >/dev/null; echo $?",
    "sed -n 1,20p f.py",
    "find . -name '*.py'",
    "command -v jq",
    "echo $((1 + 2))",
    "diff <(git show origin/main:README.md) <(git show HEAD:README.md)",
    "cat <<'EOF'\na > b\nEOF",
    "git log -1  # the newest\ngit status",
    "if git diff --quiet origin/main...HEAD -- migrations; then echo none; else echo some; fi",
    'for f in $(git diff --name-only origin/main...HEAD); do wc -l "$f"; done',
    "git diff --name-only origin/main...HEAD | while read f; do echo $f; done",
])
def test_the_reviewer_may_read(command):
    assert run("reviewer_readonly.py", command) == 0


@pytest.mark.parametrize("command", [
    "git stash",
    'git -C "/x y" stash',
    "git -c k=v commit -m x",
    'echo "$(git stash)"',
    'echo "$(git stash $(echo x))"',
    "echo `git stash`",
    "cat <(git checkout main)",
    "cat <<EOF\n$(git stash)\nEOF",
    "git status\n# tidy up\ngit checkout -- .",
    "git log -1  # last\ngit stash",
    "echo x#y; git stash",
    'grep -rn "<<EOF" scripts\ngit stash',
    "X=1 git commit -am wip",
    "git checkout main",
    "git branch newname",
    "git branch -f main origin/main",
    "git tag v1",
    "git worktree remove /x",
    "git fetch origin main:main",
    "git diff --output=patch.diff",
    "gh pr checkout 52",
    "gh pr create -t x",
    "gh api --method=POST repos/o/r/issues",
    "gh api -XPOST repos/o/r/issues",
    "gh api repos/o/r/issues -f title=t",
    "rm -rf build",
    "find . -delete",
    'bash -c "git stash"',
    "sed -E -i '' s/a/b/ f",
    "echo hi > notes.md",
    "pytest 2>1",
    "uv pip install foo",
    "alembic upgrade head",
    "python -m alembic downgrade -1",
    ".venv/bin/python -m sports_follow.players remove amster",
    "python -m sports_follow.registry",
    "curl https://example.com",
    "ruff check --fix-only .",
    "basedpyright --writebaseline",
    'git commit -m "an open quote',
])
def test_the_reviewer_may_not_write(command):
    assert run("reviewer_readonly.py", command) == 2


def test_a_pull_request_waits_for_a_review(tmp_path):
    (tmp_path / "reviewed.md").write_text("What it does.\n\nFixes BHA-1\nReviewed: reviewer agent, 1 fixed\n")
    (tmp_path / "not reviewed.md").write_text("What it does.\n\nFixes BHA-1\n")
    passes = [
        "git status",
        'grep -n "gh pr create" CLAUDE.md',
        'git commit -m "open it with gh pr create later"',
        "gh pr create --help",
        'gh pr create --title x --body "x\n\nReviewed: reviewer agent, nothing found"',
        "gh pr create --body-file - <<'EOF'\nx\nReviewed: reviewer agent, 2 fixed\nEOF",
        "gh pr create --title t --body \"$(cat <<'EOF'\nx (1) done\nReviewed: reviewer agent, 2 fixed\nEOF\n)\"",
        f"gh pr create --body-file={tmp_path}/reviewed.md",
        f'cd "{tmp_path}" && gh pr create -F reviewed.md',
    ]
    stops = [
        'gh pr create --title x --body "x"',
        'gh pr new --title x --body "x"',
        "gh  pr create --fill",
        "# open the PR\ngh pr create --title x --body x",
        "gh pr create --title t --body \"$(cat <<'EOF'\nx\nEOF\n)\"",
        'gh pr create --body "$(cat body.md)"',
        f'gh pr create --body-file "{tmp_path}/not reviewed.md"',
        "gh pr create --body-file missing.md",
    ]
    assert [c for c in passes if run("pr_reviewed.py", c, tmp_path) != 0] == []
    assert [c for c in stops if run("pr_reviewed.py", c, tmp_path) != 2] == []


def test_approving_a_production_deploy_is_bharaths(tmp_path):
    # 18 Oct: a session approved a waiting deploy with `gh api`, which runs as Bharath, so it counted as his review.
    (tmp_path / "approve.graphql").write_text("mutation { approveDeployments(input: {}) { clientMutationId } }")
    (tmp_path / "viewer.graphql").write_text("query { viewer { login } }")
    run_path = "repos/Codewiz2898/sports-follow/actions/runs/39126691465"
    passes = [
        f"gh api {run_path}/pending_deployments --jq '.[] | .environment.name'",
        f"gh api -X GET {run_path}/pending_deployments",
        "gh api repos/Codewiz2898/sports-follow/environments/production",
        "gh workflow run deploy.yml -f action=deploy",  # starts a run that waits for his approval
        "gh run list --workflow deploy.yml --limit 5",
        "gh run watch 39126691465 --exit-status",
        "ssh deploy@203.0.113.7 /opt/sports-follow/deploy/deploy.sh status",
        "ssh deploy@203.0.113.7 'cd /opt/sports-follow && docker compose -f deploy/compose.yaml ps'",
        "grep -n 'deploy.sh deploy' deploy/README.md",
        "git commit -m \"docs: by hand, ssh deploy@<server> deploy.sh deploy\"",
        "sed -n '/^## Deploying/,/^## Alerts/p' deploy/README.md",
        f"cd {tmp_path} && gh api graphql -F query=@viewer.graphql",
    ]
    stops = [
        f'gh api -X POST {run_path}/pending_deployments -F "environment_ids[]=24321060570" -f state=approved',
        f"gh api {run_path}/pending_deployments -f state=rejected -F environment_ids[]=1",
        f"gh api --method=POST {run_path}/pending_deployments --input approve.json",
        f"echo ok && gh api -XPOST /{run_path}/pending_deployments",
        f"gh api -X POST {run_path}/deployment_protection_rule -f state=approved",
        "gh api graphql -f query='mutation { approveDeployments(input: {workflowRunId: \"x\"}) { clientMutationId } }'",
        "gh api graphql -F query=@- <<'EOF'\nmutation { rejectDeployments(input: {}) { clientMutationId } }\nEOF",
        f"cd {tmp_path} && gh api graphql -F query=@approve.graphql",
        "gh api -X PUT repos/Codewiz2898/sports-follow/environments/production -F reviewers[][id]=1",
        "gh api -X DELETE repos/Codewiz2898/sports-follow/environments/production",
        f"curl -s -X POST -H 'Authorization: Bearer x' https://api.github.com/{run_path}/pending_deployments -d '{{}}'",
        "ssh deploy@203.0.113.7 /opt/sports-follow/deploy/deploy.sh deploy",
        "ssh -o BatchMode=yes deploy@$(dig +short stann.in | head -1) /opt/sports-follow/deploy/deploy.sh rollback",
        "ssh deploy@203.0.113.7 'cd /opt/sports-follow && deploy/deploy.sh deploy 437573e'",
        "ssh deploy@203.0.113.7 bash -s <<'EOF'\ncd /opt/sports-follow\ndeploy/deploy.sh deploy\nEOF",
        "cd /opt/sports-follow && deploy/deploy.sh deploy",
        'bash -c "deploy/deploy.sh rollback"',
    ]
    assert [c for c in passes if run("deploy_gate.py", c, tmp_path) != 0] == []
    assert [c for c in stops if run("deploy_gate.py", c, tmp_path) != 2] == []


@pytest.mark.parametrize("command, blocked", [
    ("curl -s localhost:18421/api/health", False),
    ("docker exec sports-follow-postgres psql -U sports -d sports_follow_r2 -c 'select 1'", False),
    ("cd /w && env REDIS_URL=redis://127.0.0.1:6381/0 /v/bin/uvicorn sports_follow.server:app --port 18431 &", False),
    ("git -C /w log --oneline -5 && gh pr view 40", False),
    ("git stash", True),
    ('git -C "/w x" checkout main', True),
    ("curl -s x; git commit -am wip", True),
    ("gh pr comment 40 --body hi", True),
])
def test_a_hunter_runs_its_repros_but_never_changes_git(command, blocked):
    assert run("hunter_guard.py", command) == (2 if blocked else 0)
