"""Stops a session from deploying to production on its own: approving a deploy is Bharath's.

Every deploy waits at GitHub's `production` environment for his approval (deploy/README.md, "Deploying").
`gh` runs as his account, so an approval a session sends through the API counts as his, and the review
it stands for never happens (18 Oct 2026). This blocks a Bash call that would get past that gate:

- approving or rejecting a waiting deploy: text naming `pending_deployments` (or
  `deployment_protection_rule`) that also sends something: `approved`, `rejected`, POST, a body from a
  file or stdin. Or GraphQL's approveDeployments / rejectDeployments sent to the GraphQL API, inline or
  from a file the call reads;
- changing an environment (its required reviewers): a POST, PUT, PATCH, DELETE or fields to
  `repos/<owner>/<repo>/environments/...`;
- running `deploy.sh deploy` or `rollback`: here, through ssh, `bash -c`, `eval` or `xargs`, or the deploy
  key's forced command (`ssh -i deploy_key deploy@host deploy`).

The first two read the raw command, so no wrapper, variable or spelling of a flag gets past them; the
cost is that a commit message or comment naming them in the command itself is stopped too (pass the text
in a file). Reading goes through (`gh api .../pending_deployments`, `deploy.sh status`), and so does
starting a Deploy run (`gh workflow run deploy.yml`), which waits for his approval like any other.

It stops a session that sets out to approve or deploy the usual ways. It isn't a sandbox: a script
written to get round it would, and CLAUDE.md says not to. Clicks in a browser aren't Bash calls.

A PreToolUse hook on every Bash call in this project: exit 2 blocks the call and stderr goes to Claude.
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bash_words import parse  # noqa: E402

TELL = ("Approving or rejecting a production deploy, changing its environment, and running deploy.sh deploy or "
        "rollback are Bharath's. Give him the Deploy run's link "
        "(https://github.com/Codewiz2898/sports-follow/actions/workflows/deploy.yml) and what it brings: every "
        "merge since the live commit, and any migration, secrets or server change. He approves it there. "
        "(If the command only mentions these in text, such as a commit message, pass the text in a file.)")

GATE = re.compile(r"pending_deployments|deployment_protection_rule")
SENDS = re.compile(r"\b(approved|rejected)\b|(\b|-X)post\b|--input|@", re.IGNORECASE)
MUTATIONS = re.compile(r"\b(approve|reject)Deployments\b", re.IGNORECASE)
GRAPHQL_CALL = re.compile(r"\bapi\s+graphql\b|api\.github\.com/graphql", re.IGNORECASE)
ENVIRONMENT = re.compile(r"repos/[^/\s'\"]+/[^/\s'\"]+/environments\b")
CHANGES = re.compile(r"(\b|-X)(post|put|patch|delete)\b|(^|\s)(-[fF]\S*|--field|--raw-field|--input)", re.IGNORECASE)
DEPLOY_TEXT = re.compile(r"deploy\.sh\W+(deploy|rollback)\b")  # when a command can't be read

SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
REMOTES = {"ssh", "mosh"}
WRAPPERS = {"env", "sudo", "doas", "timeout", "nice", "ionice", "nohup", "xargs", "caffeinate", "command", "exec",
            "time", "stdbuf"}
READS = {"status", "help", "-h", "--help"}
SSH_OPTION_ARGS = set("BbcDEeFIiJLlmOoPpQRSWw")  # ssh's options that take a value: -o X, -i key, -p 22


def unwrap(words: list[str]) -> tuple[list[str], bool]:
    """The command a line runs, past `env -i X=1`, `sudo -E`, `timeout 30`, `xargs -n1` and the like, and
    whether xargs feeds it arguments of its own."""
    fed = False
    while words:
        name = os.path.basename(words[0])
        if name in WRAPPERS:
            fed = fed or name == "xargs"
            words = words[1:]
            if name == "timeout" and words and re.fullmatch(r"[\d.]+[smhd]?", words[0]):
                words = words[1:]
        elif words[0].startswith("-") or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0]) or words[0].isdigit():
            words = words[1:]  # a wrapper's option, a setting, nice's priority
        else:
            break
    return words, fed


def deploys(script: list[str], fed: bool) -> bool:
    """Whether `deploy.sh <args>` deploys or rolls back (with xargs, whatever it's given may)."""
    first = script[1] if len(script) > 1 else None
    return first in ("deploy", "rollback") or (fed and first not in READS)


def shell_parts(words: list[str]) -> tuple[str | None, list[str], bool]:
    """A shell's -c script, the script file and its arguments, and whether it only checks syntax (-n)."""
    check, skip = False, False
    for i, w in enumerate(words[1:], 1):
        if skip:  # the value of -o pipefail, -O extglob
            skip = False
            continue
        if w in ("-o", "+o", "-O", "+O"):
            skip = True
            continue
        if w == "-c" or (w.startswith("-") and not w.startswith("--") and "c" in w[1:]):
            return (words[i + 1] if i + 1 < len(words) else ""), [], check
        if w.startswith("-") or w.startswith("+"):
            check = check or (not w.startswith("--") and "n" in w[1:])
            continue
        return None, words[i:], check
    return None, [], check


def ssh_remote(words: list[str]) -> list[str]:
    """What an ssh call runs on the other side: the words after its options and host."""
    i = 1
    while i < len(words):
        w = words[i]
        if w.startswith("-"):
            i += 2 if len(w) == 2 and w[1] in SSH_OPTION_ARGS else 1
            continue
        return words[i + 1:]
    return []


def runs_deploy(command: str, depth: int = 0) -> bool:
    """Whether a command line runs deploy.sh deploy or rollback, here or in a script it hands on."""
    if depth > 4:
        return bool(DEPLOY_TEXT.search(command))
    try:
        parsed = parse(command)
    except ValueError:
        return bool(DEPLOY_TEXT.search(command))
    for simple in parsed.commands:
        words, fed = unwrap(simple.words)
        if not words:
            continue
        name = os.path.basename(words[0])
        if name == "deploy.sh" and deploys(words, fed):
            return True
        if name in SHELLS:
            script, args, check = shell_parts(words)
            if check:
                continue
            if script is not None and runs_deploy(script, depth + 1):
                return True
            if args and os.path.basename(args[0]) == "deploy.sh" and deploys(args, fed):
                return True
            if script is None and not args and any(runs_deploy(body, depth + 1) for body in parsed.heredocs):
                return True  # `bash -s <<'EOF'`: the body is the script
        if name in REMOTES:
            remote = ssh_remote(words)
            if remote[:1] in (["deploy"], ["rollback"]):
                return True  # the deploy key's forced command runs deploy.sh with these
            if remote and runs_deploy(" ".join(remote), depth + 1):
                return True
            if any(runs_deploy(body, depth + 1) for body in parsed.heredocs):
                return True  # `ssh host bash -s <<'EOF'`
        if name == "eval" and runs_deploy(" ".join(words[1:]), depth + 1):
            return True
    return False


def directories(command: str, cwd: str) -> list[str]:
    """Where relative paths in a command may point: its own folder, and any it cds to."""
    found = [cwd]
    try:
        for simple in parse(command).commands:
            if simple.name == "cd" and len(simple.words) > 1:
                found.append(os.path.join(found[-1], os.path.expanduser(simple.words[1])))
    except ValueError:
        pass
    return found


def files_read(command: str, cwd: str) -> list[str]:
    """The text of each file a command names (query=@q.graphql, --input body.json, < q, cat q |)."""
    texts: list[str] = []
    seen: set[str] = set()
    for token in re.split(r"[\s'\"=@<>|;&()`$]+", command):
        if not token or len(token) > 1024 or token.startswith("-"):
            continue
        for folder in directories(command, cwd):
            path = os.path.normpath(os.path.join(folder, os.path.expanduser(token)))
            if path in seen or not os.path.isfile(path):
                continue
            seen.add(path)
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    texts.append(f.read(1 << 20))
            except OSError:
                continue
            if len(seen) >= 30:
                return texts
    return texts


def blocked(command: str, cwd: str) -> bool:
    if GATE.search(command) and SENDS.search(command):
        return True
    if GRAPHQL_CALL.search(command) and (MUTATIONS.search(command)
                                         or any(MUTATIONS.search(text) for text in files_read(command, cwd))):
        return True
    if ENVIRONMENT.search(command) and CHANGES.search(command):
        return True
    return runs_deploy(command)


def main() -> int:
    try:
        call = json.load(sys.stdin)
        command = call.get("tool_input", {}).get("command") or ""
    except Exception:
        return 0
    lowered = command.lower()
    if not ("deploy" in lowered or "environments" in lowered or "graphql" in lowered):
        return 0
    try:
        stop = blocked(command, call.get("cwd") or os.getcwd())
    except Exception:  # unreadable: stop it only if it plainly names a deploy
        stop = bool((GATE.search(command) and SENDS.search(command)) or MUTATIONS.search(command)
                    or DEPLOY_TEXT.search(command))
    if stop:
        print(TELL, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
