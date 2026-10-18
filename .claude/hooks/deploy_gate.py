"""Stops a session from deploying to production on its own: approving a deploy is Bharath's.

Every deploy waits at GitHub's `production` environment for his approval (deploy/README.md, "Deploying").
`gh` runs as his account, so an approval a session sends through the API counts as his, and the review
it stands for never happens (18 Oct 2026). This blocks a Bash call that would get past that gate:

- approving or rejecting a waiting deploy: text naming `pending_deployments` (or
  `deployment_protection_rule`) that also sends something: `approved`, `rejected`, POST, a body from a
  file or stdin. Or GraphQL's approveDeployments / rejectDeployments with `graphql` in the call, inline
  or in a file the call feeds in (`@q.graphql`, `--input`, `< q`, `cat q |`);
- changing an environment (its required reviewers): a POST, PUT, PATCH, DELETE or fields to
  `repos/<owner>/<repo>/environments/...`;
- running `deploy.sh` other than `status`: a command line naming it may only read (grep, git, ls, cat and
  the like); anything that could run it (a shell, ssh, sudo, xargs, `.`, python) stops the line, though
  ssh, `-c` and `eval` scripts are judged the same way. And `ssh … deploy` or `rollback`, which the
  deploy key's forced command hands to deploy.sh.

These read the raw command, so no wrapper, variable or spelling of a flag gets past them; the cost is
that a commit message or comment naming them in the command itself is stopped too (pass the text in a
file). Reading goes through (`gh api .../pending_deployments`, `deploy.sh status`), and so does starting a
Deploy run (`gh workflow run deploy.yml`), which waits for his approval like any other.

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
        "(If the command only mentions these in text, such as a commit message, pass the text in a file, or run "
        "the other commands in it on their own.)")

GATE = re.compile(r"pending_deployments|deployment_protection_rule")
SENDS = re.compile(r"\b(approved|rejected)\b|(\b|-X)post\b|--input|(=|\s)@", re.IGNORECASE)
MUTATIONS = re.compile(r"\b(approve|reject)Deployments\b", re.IGNORECASE)
GRAPHQL = re.compile(r"\bgraphql\b", re.IGNORECASE)
CALLS_API = re.compile(r"\bgh\b.*\bapi\b|\bcurl\b|\bwget\b|\bhttp\b|requests|urllib|fetch\(", re.IGNORECASE | re.DOTALL)
FED = re.compile(r"(?:(?:=|\s)@|--input[=\s]+|<\s*|\bcat\s+)['\"]?([^\s'\"|;&()<>]+)")  # files a call feeds in
ENVIRONMENT = re.compile(r"repos/\S+/environments\b")  # repos/o/r/, repos/$GITHUB_REPOSITORY/
CHANGES = re.compile(r"(\b|-X)(post|put|patch|delete)\b|(^|\s)(-[fF]\S*|--field|--raw-field|--input)", re.IGNORECASE)
NAMES_DEPLOY = re.compile(r"(?<![\w-])deploy\.sh\b(?![\s'\"]+(status|help|-h|--help)\b)")
FORCED_TEXT = re.compile(r"\b(ssh|mosh)\b[^\n;&|]*\s['\"]?(deploy|rollback)(?=['\"\s]|$)")  # when unreadable

# Commands that only read or tidy files: a line naming deploy.sh may run these and nothing else.
READ_ONLY = {"grep", "egrep", "fgrep", "rg", "ag", "ls", "cat", "bat", "less", "more", "head", "tail", "wc", "sed",
             "sort", "uniq", "cut", "tr", "diff", "cmp", "file", "stat", "git", "gh", "echo", "printf", "jq", "yq",
             "cd", "test", "[", "true", "false", ":", "basename", "dirname", "realpath", "readlink", "shellcheck",
             "chmod", "cp", "mv", "mkdir", "touch", "column", "nl", "shasum", "md5", "dig", "xxd", "od", "hexdump",
             "tree"}
RUNS = re.compile(r"\bsystem\b|-exec|-execdir|-ok\b|-okdir|-delete")  # what makes awk or find run or remove things
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
REMOTES = {"ssh", "mosh"}
READS = {"status", "help", "-h", "--help"}
SSH_OPTION_ARGS = set("BbcDEeFIiJLlmOoPpQRSWw")  # ssh's options that take a value: -o X, -i key, -p 22


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
            check = check or (w.startswith("-") and not w.startswith("--") and "n" in w[1:])
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


def could_run_deploy(command: str, depth: int = 0) -> bool:
    """For a line naming deploy.sh: whether anything in it could run it. Read-only commands can't; a shell
    or ssh with no script of its own runs its stdin; anything else might."""
    if depth > 4:
        return True
    for simple in parse(command).commands:
        words = simple.words
        name = os.path.basename(words[0]) if words else ""
        if not words or name in READ_ONLY or (name in ("awk", "find") and not RUNS.search(" ".join(words))):
            continue
        if name == "deploy.sh" and (len(words) == 1 or words[1] in READS):
            continue  # its usage, or status
        if name in SHELLS:
            script, args, check = shell_parts(words)
            if check:
                continue
            if script is not None:
                if could_run_deploy(script, depth + 1):
                    return True
                continue
            if args and os.path.basename(args[0]) == "deploy.sh" and args[1:2] and args[1] in READS:
                continue
            return True  # a script file, or its stdin
        if name in REMOTES:
            remote = ssh_remote(words)
            if not remote or could_run_deploy(" ".join(remote), depth + 1):
                return True
            continue
        if name == "eval":
            if could_run_deploy(" ".join(words[1:]), depth + 1):
                return True
            continue
        return True
    return False


def forced_deploy(command: str, depth: int = 0) -> bool:
    """`ssh … deploy` or `rollback`: with the deploy key, its forced command hands these to deploy.sh."""
    if depth > 4:
        return False
    for simple in parse(command).commands:
        words = simple.words
        if not words or os.path.basename(words[0]) in READ_ONLY:
            continue  # `grep -rn ssh README.md deploy` only reads
        at = next((i for i, w in enumerate(words) if os.path.basename(w) in REMOTES), None)
        if at is not None:
            remote = " ".join(ssh_remote(words[at:])).split()
            remote = remote[1:] if remote[:1] == ["--"] else remote
            if remote[:1] in (["deploy"], ["rollback"]) or forced_deploy(" ".join(remote), depth + 1):
                return True
        if words and os.path.basename(words[0]) in SHELLS:
            script = shell_parts(words)[0]
            if script and forced_deploy(script, depth + 1):
                return True
        if words and words[0] == "eval" and forced_deploy(" ".join(words[1:]), depth + 1):
            return True
    return False


def changes_environment(command: str) -> bool:
    """A write to a repository's environments: judged on the command naming the path, so piping what it
    reads through `grep -F` or `cut -f1` doesn't count."""
    if not ENVIRONMENT.search(command):
        return False
    try:
        texts = [" ".join(s.words) for s in parse(command).commands if ENVIRONMENT.search(" ".join(s.words))]
    except ValueError:
        texts = []
    return any(CHANGES.search(t) for t in texts or [command])  # the path in a variable: the whole line


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


def files_fed(command: str, cwd: str) -> list[str]:
    """The text of each file a call feeds in: query=@q.graphql, -d @body, --input body.json, < q, cat q |."""
    texts: list[str] = []
    for name in dict.fromkeys(FED.findall(command)):
        if name == "-":
            continue
        for folder in directories(command, cwd):
            path = os.path.join(folder, os.path.expanduser(name))
            if os.path.isfile(path):
                try:
                    with open(path, encoding="utf-8", errors="replace") as f:
                        texts.append(f.read(1 << 20))
                except OSError:
                    pass
                break
    return texts


def blocked(command: str, cwd: str) -> bool:
    if GATE.search(command) and SENDS.search(command):
        return True
    if GRAPHQL.search(command) and (MUTATIONS.search(command) or (
            CALLS_API.search(command) and any(MUTATIONS.search(text) for text in files_fed(command, cwd)))):
        return True
    if changes_environment(command):
        return True
    if "SSH_ORIGINAL_COMMAND" in command and "deploy.sh" in command:
        return True  # deploy.sh takes its arguments from it: `SSH_ORIGINAL_COMMAND=deploy deploy.sh status`
    try:
        if forced_deploy(command) or (NAMES_DEPLOY.search(command) and could_run_deploy(command)):
            return True
        names = {os.path.basename(s.words[0]) for s in parse(command).commands if s.words}
    except ValueError:  # a line the reader can't follow: stopped if it names deploy.sh at all
        return bool(NAMES_DEPLOY.search(command) or FORCED_TEXT.search(command))
    # A script sent to a shell or to ssh on stdin (`ssh host 'bash -s' < release.sh`) is read for deploy.sh too.
    return bool(names & (SHELLS | REMOTES)) and any(NAMES_DEPLOY.search(text) for text in files_fed(command, cwd))


def main() -> int:
    try:
        call = json.load(sys.stdin)
        command = call.get("tool_input", {}).get("command") or ""
    except Exception:
        return 0
    lowered = command.lower()
    if not ("deploy" in lowered or "environments" in lowered or "graphql" in lowered or "rollback" in lowered):
        return 0
    try:
        stop = blocked(command, call.get("cwd") or os.getcwd())
    except Exception:  # anything else unexpected: stopped only if it plainly names a deploy
        stop = bool((GATE.search(command) and SENDS.search(command)) or MUTATIONS.search(command)
                    or NAMES_DEPLOY.search(command) or FORCED_TEXT.search(command))
    if stop:
        print(TELL, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
