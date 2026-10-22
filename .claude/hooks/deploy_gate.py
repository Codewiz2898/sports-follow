"""Stops a session from deploying to production on its own: approving a deploy is Bharath's.

Every deploy waits at GitHub's `production` environment for his approval (deploy/README.md, "Deploying"),
or, in a private repository, where GitHub can't hold it, waits for him to start it. `gh` runs as his
account, so an approval a session sends through the API counts as his, and the review it stands for
never happens (18 Oct 2026). This blocks a Bash call that would get past that gate:

- approving or rejecting a waiting deploy: text naming `pending_deployments` (or
  `deployment_protection_rule`) that also sends something: `approved`, `rejected`, POST, a body from a
  file or stdin. Or GraphQL's approveDeployments / rejectDeployments with `graphql` in the call, inline
  or in a file the call feeds in (`@q.graphql`, `--input`, `< q`, `cat q |`);
- changing an environment (its required reviewers): a POST, PUT, PATCH, DELETE or fields to
  `repos/<owner>/<repo>/environments/...`;
- starting a Deploy run, which in a private repository deploys at once: `gh workflow run` unless it spells
  out another workflow (ci.yml, "Deploy summary"; a number, a variable or xargs could be Deploy), or the
  Deploy workflow's `dispatches` in the API;
- a gh alias for `workflow run` or `run rerun` (`gh alias set`, or any `gh alias import`), which would
  hide them from this check;
- re-running one, which deploys its commit again: `gh run rerun`, or `.../runs/<id>/rerun` or
  `.../jobs/<id>/rerun` in the API, when GitHub says the run is Deploy's, or can't say;
- running `deploy.sh` other than `status`: a command line naming it may only read (grep, git, ls, cat and
  the like); anything that could run it (a shell, ssh, sudo, xargs, `.`, python) stops the line, though
  ssh, `-c` and `eval` scripts are judged the same way. And `ssh … deploy` or `rollback`, which the
  deploy key's forced command hands to deploy.sh.

These read the raw command, so no wrapper, variable or spelling of a flag gets past them; the cost is
that a commit message or comment naming them in the command itself is stopped too (pass the text in a
file). Reading goes through (`gh api .../pending_deployments`, `deploy.sh status`, `gh run list --workflow
deploy.yml`), and so do other workflows (`gh workflow run deploy-summary.yml`, re-running CI).

It stops a session that sets out to approve or deploy the usual ways. It isn't a sandbox: a script
written to get round it would, and CLAUDE.md says not to. Clicks in a browser aren't Bash calls.

A PreToolUse hook on every Bash call in this project: exit 2 blocks the call and stderr goes to Claude.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bash_words import parse  # noqa: E402

TELL = ("Approving or rejecting a production deploy, starting or re-running a Deploy run, changing its environment, "
        "and running deploy.sh deploy or rollback are Bharath's. Give him the Deploy runs' link "
        "(https://github.com/Codewiz2898/sports-follow/actions/workflows/deploy.yml) and what it brings: every "
        "merge since the live commit, and any migration, secrets or server change. He approves or starts it there. "
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
# Starting or re-running a Deploy run. What follows `gh workflow run` or `gh run rerun`, up to the end of that
# command, split into words, quoted ones whole: in a shell line, or in a list a wrapper hands to subprocess.
DISPATCHES = re.compile(r"actions/workflows/([^/\s'\"]+)/dispatches", re.IGNORECASE)
_GH = r"\bgh\b(?:[\s'\",)]+(?:-R|--repo)[\s'\",=]+[^\s'\",]+)?[\s'\",)]+"  # gh, maybe -R owner/repo
WORKFLOW_RUN = re.compile(_GH + r"workflow[\s'\",]+run\b([^\n;&|)]*)", re.IGNORECASE)
RUN_RERUN = re.compile(_GH + r"run[\s'\",]+rerun\b([^\n;&|)]*)", re.IGNORECASE)
ALIAS = re.compile(_GH + r"alias[\s'\",]+(set|import)\b", re.IGNORECASE)  # `gh d` could then hide either
HIDDEN = re.compile(r"\bworkflow\W+run\b|\brun\W+rerun\b", re.IGNORECASE)
WORKFLOW_RUN_VALUES = {"-f", "-F", "--field", "--raw-field", "-r", "--ref", "-R", "--repo"}  # flags taking a value
RERUN_API = re.compile(r"(?:repos/([^\s'\"]+?)/)?actions/(runs|jobs)/([^/\s'\"]+)/rerun", re.IGNORECASE)
WORD = re.compile(r"'[^']*'|\"[^\"]*\"|[^\s'\",\[\]]+")
DEPLOY_WORKFLOW = {"deploy", "deploy.yml", "deploy.yaml", ".github/workflows/deploy.yml", ".github/workflows/deploy.yaml"}

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


def words_after(match: re.Match[str]) -> list[str]:
    """The words after the match, unquoted, without what only separates a list's items (`', '`)."""
    return [w for w in (w.strip("'\"") for w in WORD.findall(match.group(1))) if w.strip(" ,[]")]


def gh_says(path: str, jq: str, cwd: str) -> str:
    """One answer from GitHub's API through gh, as the session's account; raises when there's none."""
    out = subprocess.run(["gh", "api", path, "--jq", jq], cwd=cwd if os.path.isdir(cwd) else None,
                         capture_output=True, text=True, timeout=20, check=True).stdout.strip()
    if not out:
        raise ValueError(path)
    return out


def deploys_again(kind: str, number: str, repo: str | None, cwd: str) -> bool:
    """Whether re-running this run (or this job's run) re-runs a deploy: True for Deploy's, and when GitHub
    can't say. `{owner}/{repo}` is gh's: the repository of the folder it runs in."""
    base = f"repos/{repo if repo and '$' not in repo else '{owner}/{repo}'}/actions"
    try:
        if kind == "jobs":
            number = gh_says(f"{base}/jobs/{number}", ".run_id", cwd)
        path = gh_says(f"{base}/runs/{number}", ".path", cwd)
    except Exception:
        return True
    return os.path.basename(path.split("@")[0]).lower() in ("deploy.yml", "deploy.yaml")


def other_workflow(name: str) -> bool:
    """A workflow named by its file, and not Deploy's: ci.yml, deploy-summary.yml. A number or a variable could
    be Deploy."""
    return bool(re.fullmatch(r"[\w.-]+\.ya?ml", name)) and name.lower() not in DEPLOY_WORKFLOW


def runs_deploy_workflow(words: list[str]) -> bool:
    """For `gh workflow run`'s words: whether the workflow it names could be Deploy. It must be spelled out, as
    a file other than deploy.yml or a name other than Deploy; a number, a variable or none (xargs) could be."""
    if {"-h", "--help"} & set(words):
        return False
    i = 0
    while i < len(words):
        w = words[i]
        if w.startswith("-"):
            i += 2 if w in WORKFLOW_RUN_VALUES else 1
            continue
        if any(c in w for c in "$`(") or w.isdigit():
            return True
        name = os.path.basename(w).lower()
        return name in DEPLOY_WORKFLOW or (not other_workflow(name) and name.endswith((".yml", ".yaml")))
    return True


def starts_deploy(command: str, cwd: str) -> bool:
    """`gh workflow run` for the Deploy workflow, its dispatches in the API, or re-running one of its runs.
    A run or workflow the line doesn't spell out (a variable, xargs) counts as Deploy's."""
    if any(not other_workflow(name) for name in DISPATCHES.findall(command)):
        return True
    if ALIAS.search(command) and (HIDDEN.search(command) or "import" in command.lower()):
        return True  # an alias file's text isn't read: name the alias's command in the line
    if any(runs_deploy_workflow(words_after(match)) for match in WORKFLOW_RUN.finditer(command)):
        return True
    folder = directories(command, cwd)[-1]
    for match in RUN_RERUN.finditer(command):
        words = words_after(match)
        if {"-h", "--help"} & set(words):
            continue
        if any(w.startswith("$") or "`" in w for w in words) or not any(w.isdigit() for w in words):
            return True
        repo = next((words[i + 1] for i, w in enumerate(words[:-1]) if w in ("-R", "--repo")), None)
        repo = repo or next((w.split("=", 1)[1] for w in words if w.startswith("--repo=")), None)
        jobs = {words[i + 1] for i, w in enumerate(words[:-1]) if w in ("-j", "--job")}
        for w in words:
            if w.isdigit() and deploys_again("jobs" if w in jobs else "runs", w, repo, folder):
                return True
    for repo, kind, number in RERUN_API.findall(command):
        if not number.isdigit() or deploys_again(kind.lower(), number, repo or None, folder):
            return True
    return False


def blocked(command: str, cwd: str) -> bool:
    if GATE.search(command) and SENDS.search(command):
        return True
    if starts_deploy(command, cwd):
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
    if not any(word in lowered for word in ("deploy", "environments", "graphql", "rollback", "workflow", "rerun", "alias")):
        return 0
    try:
        stop = blocked(command, call.get("cwd") or os.getcwd())
    except Exception:  # anything else unexpected: stopped only if it plainly names a deploy
        stop = bool((GATE.search(command) and SENDS.search(command)) or MUTATIONS.search(command)
                    or NAMES_DEPLOY.search(command) or FORCED_TEXT.search(command)
                    or any(not other_workflow(name) for name in DISPATCHES.findall(command))
                    or WORKFLOW_RUN.search(command) or RUN_RERUN.search(command))
    if stop:
        print(TELL, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
