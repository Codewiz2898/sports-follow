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


def run(hook: str, command: str, cwd: Path | str = "/", home: Path | None = None) -> int:
    call = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}
    env = {**os.environ, "HOME": str(home)} if home is not None else None
    return subprocess.run([PYTHON, str(HOOKS / hook)], input=json.dumps(call), capture_output=True, text=True, env=env).returncode


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
    mutation = "mutation { approveDeployments(input: {}) { clientMutationId } }"
    (tmp_path / "approve.graphql").write_text(mutation)
    (tmp_path / "body.json").write_text(json.dumps({"query": mutation}))
    (tmp_path / "viewer.graphql").write_text("query { viewer { login } }")
    (tmp_path / "notes.md").write_text(f"The hook stops `gh api graphql` with {mutation}")
    (tmp_path / "release.sh").write_text("cd /opt/sports-follow\ndeploy/deploy.sh deploy\n")
    run_path = "repos/Codewiz2898/sports-follow/actions/runs/39126691465"
    approve = f"gh api -X POST {run_path}/pending_deployments -f state=approved"
    passes = [
        f"gh api {run_path}/pending_deployments --jq '.[] | .environment.name'",
        f"gh api {run_path}/pending_deployments --jq '.[] | \"env=\\(.environment.name) \\(.current_user_can_approve)\"'",
        f"gh api -X GET {run_path}/pending_deployments",
        "gh api repos/Codewiz2898/sports-follow/environments/production",
        "gh workflow run deploy.yml -f action=deploy",  # starts a run that waits for his approval
        "gh run list --workflow deploy.yml --limit 5",
        "gh run watch 39126691465 --exit-status",
        "gh api repos/Codewiz2898/sports-follow/issues/131/comments -f body='The hook now blocks approveDeployments'",
        "ssh deploy@203.0.113.7 /opt/sports-follow/deploy/deploy.sh status",
        "ssh deploy@203.0.113.7 'cd /opt/sports-follow && docker compose -f deploy/compose.yaml ps'",
        "deploy/deploy.sh",  # prints its usage
        "bash -n deploy/deploy.sh",
        "grep -n 'deploy.sh deploy' deploy/README.md",
        "grep -rn deploy.sh deploy",
        "grep -rln 'deploy.sh' deploy",
        "git diff origin/main -- deploy/deploy.sh deploy",
        "ls -l deploy/deploy.sh deploy",
        "git commit -m \"docs: by hand, ssh deploy@<server> deploy.sh deploy\"",
        "gh pr create --body \"$(cat <<'EOF'\nBy hand: ssh deploy@h deploy/deploy.sh deploy\nEOF\n)\" && bash -n deploy/deploy.sh",
        "sed -n '/^## Deploying/,/^## Alerts/p' deploy/README.md",
        "gh api graphql -F query=@viewer.graphql",
        "gh api repos/Codewiz2898/sports-follow/environments/production | grep -F required_reviewers",
        "gh api repos/Codewiz2898/sports-follow/environments | jq -r '.environments[].name' | cut -f1",
        "grep -n 'api graphql' notes.md",
        "cat notes.md | grep -n 'api graphql'",
        "gh run view 1 && echo 'see pending_deployments for deploy@stann.in'",
        "ssh deploy@203.0.113.7 'grep -n usage /opt/sports-follow/deploy/deploy.sh'",
        "ssh deploy@$(dig +short stann.in | head -1) 'grep -n usage /opt/sports-follow/deploy/deploy.sh'",
        "bash deploy/predeploy.sh",
        "grep -rn ssh README.md deploy",
        "rg -n ssh -g '*.md' deploy",
        "awk '/^do_deploy/,/^}/' deploy/deploy.sh",
        "find deploy -name 'deploy.sh' -newer README.md",
        "xxd deploy/deploy.sh | head",
        "gh api repos/$GITHUB_REPOSITORY/environments/production --jq .protection_rules",
        "ssh deploy@203.0.113.7 'cd /opt/sports-follow && deploy/deploy.sh status && docker compose -f deploy/compose.yaml ps'",
        "gh run list --workflow deploy.yml --status waiting",
        "git log -- deploy/deploy.sh",
        "echo 'rollback is by hand'",
    ]
    stops = [
        approve,
        f'gh api -X POST {run_path}/pending_deployments -F "environment_ids[]=24321060570" -f state=approved',
        f"gh api {run_path}/pending_deployments -f state=rejected -F environment_ids[]=1",
        f"gh api {run_path}/pending_deployments -fstate=approved -Fenvironment_ids[]=1",
        f"gh api --method=post {run_path}/pending_deployments --input approve.json",
        f"echo ok && gh api -XPOST /{run_path}/pending_deployments",
        f"gh api -X POST {run_path}/deployment_protection_rule -f state=approved",
        f"P={run_path}/pending_deployments; gh api -X POST $P -f state=approved",
        f"env -i PATH=$PATH {approve}",
        f"sudo -E {approve}",
        f"timeout 30 {approve}",
        f"echo {run_path}/pending_deployments | xargs gh api -X POST -f state=approved",
        f"python3 -c \"import subprocess; subprocess.run(['gh', 'api', '-X', 'POST', '{run_path}/pending_deployments', '-f', 'state=approved'])\"",
        f"node -e \"require('child_process').execSync('{approve}')\"",
        f"bash -c '{approve}'",
        f"eval '{approve}'",
        f"echo $'don\\'t'; {approve}",  # a quoting the command reader can't follow
        f"curl -s -X POST -H 'Authorization: Bearer x' https://api.github.com/{run_path}/pending_deployments -d '{{}}'",
        "curl -sd '{\"state\":\"approved\"}' https://api.github.com/" + run_path + "/pending_deployments",
        f"gh api graphql -f query='{mutation}'",
        "gh api graphql -F query=@- <<'EOF'\nmutation { rejectDeployments(input: {}) { clientMutationId } }\nEOF",
        "gh api graphql -F query=@approve.graphql",
        "gh api graphql --input body.json",
        "gh api graphql --input=body.json",
        "gh api graphql -F query=@- < approve.graphql",
        "cat approve.graphql | gh api graphql -F query=@-",
        f"echo '{json.dumps({'query': mutation})}' | gh api graphql --input -",
        'gh api graphql -f query="$(cat approve.graphql)"',
        f"Q='{mutation}'; gh api graphql -f query=\"$Q\"",
        f"cat > q.graphql <<'EOF'\n{mutation}\nEOF\ngh api graphql -F query=@q.graphql",
        "curl -s -H \"Authorization: bearer $(gh auth token)\" https://api.github.com/graphql -d " + json.dumps(json.dumps({"query": mutation})),
        "gh api -X PUT repos/Codewiz2898/sports-follow/environments/production -F reviewers[][id]=1",
        "gh api -X DELETE repos/Codewiz2898/sports-follow/environments/production",
        "ssh deploy@203.0.113.7 /opt/sports-follow/deploy/deploy.sh deploy",
        "ssh -o BatchMode=yes deploy@$(dig +short stann.in | head -1) /opt/sports-follow/deploy/deploy.sh rollback",
        "ssh deploy@203.0.113.7 'cd /opt/sports-follow && deploy/deploy.sh deploy 437573e'",
        "ssh deploy@203.0.113.7 \"sh -c 'cd /opt/sports-follow && deploy/deploy.sh deploy'\"",
        "ssh deploy@203.0.113.7 bash -s <<'EOF'\ncd /opt/sports-follow\ndeploy/deploy.sh deploy\nEOF",
        "ssh -i deploy_key deploy@203.0.113.7 deploy",  # the deploy key's forced command runs deploy.sh
        "ssh -i deploy_key deploy@203.0.113.7 rollback",
        "cd /opt/sports-follow && deploy/deploy.sh deploy",
        "bash deploy/deploy.sh deploy",
        'bash -c "deploy/deploy.sh rollback"',
        "bash -o pipefail -c 'deploy/deploy.sh deploy'",
        "bash -c \"sh -c 'deploy/deploy.sh rollback'\"",
        "eval 'deploy/deploy.sh deploy'",
        "echo deploy | xargs deploy/deploy.sh",
        "echo $'don\\'t'; deploy/deploy.sh deploy",
        "echo $'don\\'t'; ssh -i deploy_key deploy@203.0.113.7 deploy",
        f"gh api -H 'Accept: application/json' graphql -f query='{mutation}'",
        f"gh api --paginate graphql -f query='{mutation}'",
        f"gh api /graphql -f query='{mutation}'",
        "ssh deploy@203.0.113.7 'sudo -u deploy /opt/sports-follow/deploy/deploy.sh deploy'",
        "ssh deploy@203.0.113.7 'timeout -s KILL 900 /opt/sports-follow/deploy/deploy.sh deploy'",
        "echo deploy | xargs -I {} deploy/deploy.sh {}",
        "echo 'cd /opt/sports-follow && deploy/deploy.sh deploy' | ssh deploy@203.0.113.7 bash",
        "echo 'cd /opt/sports-follow && deploy/deploy.sh deploy' | ssh deploy@203.0.113.7",
        "echo 'deploy/deploy.sh deploy' | bash",
        ". deploy/deploy.sh deploy",
        "ssh deploy@203.0.113.7 'cd /opt/sports-follow && source deploy/deploy.sh deploy'",
        "python3 -c \"import os; os.system('ssh deploy@203.0.113.7 /opt/sports-follow/deploy/deploy.sh deploy')\"",
        "ssh -i deploy_key deploy@203.0.113.7 'deploy 437573e'",
        "ssh -i deploy_key deploy@203.0.113.7 \"deploy $(git rev-parse origin/main)\"",
        "ssh -i deploy_key deploy@203.0.113.7 -- deploy",
        "bash +n deploy/deploy.sh deploy",
        "ssh deploy@203.0.113.7 'SSH_ORIGINAL_COMMAND=deploy /opt/sports-follow/deploy/deploy.sh'",
        "ssh deploy@203.0.113.7 'SSH_ORIGINAL_COMMAND=rollback /opt/sports-follow/deploy/deploy.sh status'",
        "ssh deploy@203.0.113.7 'env SSH_ORIGINAL_COMMAND=deploy /opt/sports-follow/deploy/deploy.sh'",
        'gh api graphql -F query=@"approve.graphql"',
        f'gh api graphql -F query=@"{tmp_path}/approve.graphql"',
        'gh api graphql --input "body.json"',
        'gh api graphql -F query=@- < "approve.graphql"',
        'gh api graphql -f query="$(cat "approve.graphql")"',
        "R=Codewiz2898/sports-follow; gh api -X PUT repos/$R/environments/production --input env.json",
        'gh api -X PUT "repos/$GITHUB_REPOSITORY/environments/production" -F wait_timer=0',
        "ssh deploy@203.0.113.7 'bash -s' < release.sh",
        "ssh deploy@203.0.113.7 bash < release.sh",
        "cat release.sh | ssh deploy@203.0.113.7",
        "find deploy -name deploy.sh -exec {} deploy \\;",
        "awk 'BEGIN { system(\"deploy/deploy.sh deploy\") }' deploy/deploy.sh",
    ]
    assert [c for c in passes if run("deploy_gate.py", c, tmp_path) != 0] == []
    assert [c for c in stops if run("deploy_gate.py", c, tmp_path) != 2] == []
    # A file named relative to a folder the command cds to is read from there, wherever the session is.
    assert run("deploy_gate.py", f"cd {tmp_path} && gh api graphql -F query=@approve.graphql", "/") == 2
    assert run("deploy_gate.py", f"cd {tmp_path} && gh api graphql -F query=@viewer.graphql", "/") == 0


def test_productions_reviewers_and_the_deploy_keys_command_stay_bharaths_however_asked(tmp_path):
    # BUG-221: GraphQL's environment mutations (its reviewers cleared, or it deleted) and ssh's RemoteCommand option
    # went past the gate; with no reviewer left, a Deploy run started by hand would have gone live unseen.
    clear = 'mutation { updateEnvironment(input: {environmentId: "EN_x", reviewers: []}) { environment { name } } }'
    (tmp_path / "clear.graphql").write_text(clear)
    (tmp_path / "reviewers.graphql").write_text('query { repository(owner: "o", name: "r") { environment(name: "production") { protectionRules(first: 5) { nodes { type } } } } }')
    (tmp_path / "ssh_config").write_text("Host stann\n  HostName 203.0.113.7\n  User deploy\n  RemoteCommand deploy\n")
    (tmp_path / "ssh_config_plain").write_text("Host stann\n  HostName 203.0.113.7\n  User deploy\n")
    passes = [
        "gh api graphql -F query=@reviewers.graphql",
        'gh api graphql -f query=\'query { repository(owner: "o", name: "r") { environments(first: 5) { nodes { name } } } }\'',
        "ssh -o BatchMode=yes deploy@203.0.113.7 /opt/sports-follow/deploy/deploy.sh status",
        "ssh -o RemoteCommand=uptime deploy@203.0.113.7",
        "ssh -F ssh_config_plain deploy@stann 'cd /opt/sports-follow && deploy/deploy.sh status'",
        "gh api repositories/1/environments/production --jq .protection_rules",
        "ssh deploy@203.0.113.7 -i deploy_key /opt/sports-follow/deploy/deploy.sh status",
    ]
    stops = [
        f"gh api graphql -f query='{clear}'",
        "gh api graphql -f query='mutation { deleteEnvironment(input: {id: \"EN_x\"}) { clientMutationId } }'",
        "gh api graphql -f query='mutation { createEnvironment(input: {repositoryId: \"R_x\", name: \"production\"}) { environment { id } } }'",
        "gh api graphql -F query=@clear.graphql",
        "ssh -i ~/.ssh/k -o RemoteCommand='deploy abc1234' deploy@16.178.106.36",
        "ssh -oRemoteCommand=rollback deploy@203.0.113.7",
        "ssh -o 'RemoteCommand deploy' deploy@203.0.113.7",
        "ssh -o remotecommand=deploy -i deploy_key deploy@203.0.113.7",
        "ssh -o RemoteCommand='/opt/sports-follow/deploy/deploy.sh deploy' deploy@203.0.113.7",
        "ssh -F ssh_config deploy@stann",
        "ssh -Fssh_config deploy@stann",
        "ssh deploy@203.0.113.7 -i deploy_key deploy",  # OpenSSH reads options after the host too
        "ssh deploy@203.0.113.7 -o RemoteCommand=deploy",
        "gh api -X PUT repositories/1/environments/production -F wait_timer=0",
        "gh api --method DELETE repositories/1/environments/production",
    ]
    assert [c for c in passes if run("deploy_gate.py", c, tmp_path) != 0] == []
    assert [c for c in stops if run("deploy_gate.py", c, tmp_path) != 2] == []


def test_productions_reviewers_and_a_deploy_stay_bharaths_by_the_ways_round_two_found(tmp_path):
    """BUG-221, round two: an environment's path built by a substitution, ssh's options bundled (-qi key, -vo X, -qF
    file), a config file another includes, a deploy by hand over ssh (a checkout, a build or a tag, the migrations), ssh
    run by `env -S` or xargs, a mutation that reaches `graphql --input -` through jq, and a gh alias that sends one."""
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "config").write_text("Host *\n  ServerAliveInterval 30\nInclude ~/.orbstack/ssh/config extra/*.conf\n")
    (home / ".orbstack" / "ssh").mkdir(parents=True)
    (home / ".orbstack" / "ssh" / "config").write_text("Host orb\n  HostName 127.0.0.1\n")
    (home / ".ssh" / "extra").mkdir()
    (home / ".ssh" / "extra" / "box.conf").write_text("Host box\n  HostName 203.0.113.7\n  User deploy\n")
    mutation = "mutation { approveDeployments(input: {}) { clientMutationId } }"
    (tmp_path / "body.json").write_text(json.dumps({"query": mutation}))
    (tmp_path / "viewer.json").write_text(json.dumps({"query": "query { viewer { login } }"}))
    (tmp_path / "cfg").write_text("Host deploy-box\n  HostName 203.0.113.7\n  User deploy\n  RemoteCommand rollback\n")
    (tmp_path / "plain_cfg").write_text("Host box\n  HostName 203.0.113.7\n  User deploy\n")
    (tmp_path / "release.sh").write_text("cd /opt/sports-follow\ngit checkout --detach origin/main\ndocker compose -f deploy/compose.yaml up -d --build\n")
    (tmp_path / "look.sh").write_text("cd /opt/sports-follow\ngit log -1 --format=%h\ndocker compose -f deploy/compose.yaml ps\n")
    (tmp_path / "aliases.yml").write_text("aliases:\n  ok: api graphql --input body.json\n")
    (tmp_path / "views.yml").write_text("aliases:\n  me: api graphql --input viewer.json\n")
    box = "deploy@16.178.106.36"
    own_repo = "$(gh repo view --json nameWithOwner -q .nameWithOwner)"
    passes = [
        f'gh api "repos/{own_repo}/environments/production" --jq .protection_rules',
        "ssh -qi ~/.ssh/id_ed25519 box uptime",
        "ssh -vo BatchMode=yes deploy@203.0.113.7 /opt/sports-follow/deploy/deploy.sh status",
        "ssh -qF plain_cfg box 'cd /opt/sports-follow && deploy/deploy.sh status'",
        "ssh orb uptime",
        f"ssh {box} 'cd /opt/sports-follow && docker compose -f deploy/compose.yaml up -d --force-recreate api worker'",
        f"ssh -o BatchMode=yes {box} 'sudo test -s /etc/sports-follow/files/resend-key && echo present'",
        f"ssh {box} \"cd /opt/sports-follow && docker compose -f deploy/compose.yaml exec -T postgres psql -U sports sports_follow\" <<'SQL'\nselect 1;\nSQL",
        f"ssh {box} 'cd /opt/sports-follow && docker compose -f deploy/compose.yaml logs --tail 30 api worker'",
        f"ssh {box} 'cd /opt/sports-follow && git log -1 --oneline && docker compose -f deploy/compose.yaml ps'",
        f"ssh {box} 'bash -s' < look.sh",
        "git checkout -b fix/x && git log -1",
        "docker compose build api && docker compose up -d",
        "jq -c . viewer.json | gh api graphql --input -",
        "gh alias import views.yml",
        "gh alias set co 'pr checkout'",
        "gh co 52",
    ]
    stops = [
        f'gh api -X PUT "repos/{own_repo}/environments/production" --input env.json',
        f'gh api --method DELETE "repos/{own_repo}/environments/production"',
        'gh api -X PUT "repos/${OWNER}/${REPO}"/environments/production -F wait_timer=0',
        f"ssh -qi ~/.ssh/id_ed25519 {box} deploy abc1234",
        f"ssh -4p 22 {box} deploy abc1234",
        f"ssh -tt -p 22 {box} rollback",
        f"ssh -vo RemoteCommand=rollback {box}",
        f"ssh -To 'RemoteCommand deploy abc1234' {box}",
        "ssh -qF cfg deploy-box",
        f"ssh {box} 'cd /opt/sports-follow && git fetch origin main && git checkout --detach origin/main && "
        "docker compose -f deploy/compose.yaml build && docker compose -f deploy/compose.yaml run --rm migrate && docker compose -f deploy/compose.yaml up -d'",
        f"ssh {box} 'cd /opt/sports-follow && docker tag sports-follow:abc1234 sports-follow:latest && docker compose -f deploy/compose.yaml up -d --no-build api worker'",
        f"ssh {box} 'cd /opt/sports-follow && git reset --hard origin/main && docker compose -f deploy/compose.yaml up -d --build'",
        f"ssh {box} 'cd /opt/sports-follow && docker compose -f deploy/compose.yaml run --rm migrate'",
        f"ssh {box} 'bash -s' < release.sh",
        f"cat release.sh | ssh {box}",
        f"ssh {box} bash -s <<'EOF'\ncd /opt/sports-follow\ngit pull --ff-only\ndocker image tag sports-follow:abc1234 sports-follow:latest\nEOF",
        f"env -S 'ssh {box} deploy abc1234'",
        f"echo deploy abc1234 | xargs ssh {box}",
        "jq -c . body.json | gh api graphql --input -",
        'gh api graphql -f query="$(jq -r .query body.json)"',
        "jq -c . body.json | curl -s -H \"Authorization: bearer $(gh auth token)\" -d @- https://api.github.com/graphql",
        "gh alias import aliases.yml",
        "gh alias set ok 'api graphql --input body.json'",
        f"gh alias set --shell ok 'ssh {box} deploy'",
    ]
    assert [c for c in passes if run("deploy_gate.py", c, tmp_path, home=home) != 0] == []
    assert [c for c in stops if run("deploy_gate.py", c, tmp_path, home=home) != 2] == []
    # A config file that another includes is read too, as OpenSSH does: relative to ~/.ssh, globs and all.
    (home / ".ssh" / "extra" / "box.conf").write_text("Host box\n  HostName 203.0.113.7\n  User deploy\n  RemoteCommand deploy\n")
    assert run("deploy_gate.py", "ssh box", tmp_path, home=home) == 2
    (home / ".orbstack" / "ssh" / "config").write_text("Host orb\n  RemoteCommand rollback\n")
    (home / ".ssh" / "extra" / "box.conf").unlink()
    assert run("deploy_gate.py", "ssh orb", tmp_path, home=home) == 2
    (tmp_path / "outer_cfg").write_text(f"Include {tmp_path}/cfg\n")
    assert run("deploy_gate.py", f"ssh -F {tmp_path}/outer_cfg deploy-box", tmp_path, home=home) == 2
    # An alias already made runs what it was made with: judged as that.
    (home / ".config" / "gh").mkdir(parents=True)
    (home / ".config" / "gh" / "config.yml").write_text(f"version: 1\naliases:\n    co: pr checkout\n    ok: api graphql --input {tmp_path}/body.json\n"
                                                        f"    up: '!ssh {box} deploy \"$1\"'\ngit_protocol: https\n")
    assert run("deploy_gate.py", "gh co 52", tmp_path, home=home) == 0
    assert run("deploy_gate.py", "gh ok", tmp_path, home=home) == 2
    assert run("deploy_gate.py", "gh up abc1234", tmp_path, home=home) == 2


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


def test_a_deploy_set_as_the_remote_command_of_the_users_own_ssh_config_is_stopped_too(tmp_path):
    """BUG-221's review: OpenSSH reads ~/.ssh/config when no -F is named, so a RemoteCommand there (written by another
    tool than this hook sees) would run deploy.sh from a plain `ssh stann`."""
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "config").write_text("Host stann\n  HostName 203.0.113.7\n  User deploy\n  RemoteCommand deploy\n")
    assert run("deploy_gate.py", "ssh stann", tmp_path, home=home) == 2
    (home / ".ssh" / "config").write_text("Host stann\n  HostName 203.0.113.7\n  User deploy\n")
    assert run("deploy_gate.py", "ssh stann 'cd /opt/sports-follow && deploy/deploy.sh status'", tmp_path, home=home) == 0
