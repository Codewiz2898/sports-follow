"""Stops a session from deploying to production on its own: approving a deploy is Bharath's.

Every deploy waits at GitHub's `production` environment for his approval (deploy/README.md, "Deploying").
`gh` runs as his account, so an approval a session sends through the API counts as his, and the review
it stands for never happens (18 Oct 2026). This blocks a Bash call that would get past that gate:

- approving or rejecting a waiting deploy: text naming `pending_deployments` (or
  `deployment_protection_rule`) that also sends something: `approved`, `rejected`, POST, a body from a
  file or stdin. Or GraphQL's approveDeployments / rejectDeployments with `graphql` in the call, inline
  or in a file the call feeds in (`@q.graphql`, `--input`, `< q`, `cat q |`);
- changing an environment (its required reviewers): a POST, PUT, PATCH, DELETE or fields to a path with
  `/environments` in it, whatever names the repository (`repos/<owner>/<repo>/`, `repositories/<id>/`, a
  variable or a `$(…)`), or GraphQL's createEnvironment, updateEnvironment, deleteEnvironment and the
  like, as above. With GraphQL, any file the line names is read for them (`jq . q.json | gh api graphql
  --input -`);
- running `deploy.sh` other than `status`: a command line naming it may only read (grep, git, ls, cat and
  the like); anything that could run it (a shell, ssh, sudo, xargs, `.`, python) stops the line, though
  ssh, `-c` and `eval` scripts are judged the same way. And `ssh … deploy` or `rollback`, which the
  deploy key's forced command hands to deploy.sh: after the host, as ssh's RemoteCommand option
  (`-o RemoteCommand=deploy`, its flags bundled or not: `-vo`, `-qF file`), or as a RemoteCommand in a
  config file the call names (`-F file`) or, naming none, in `~/.ssh/config` or the system's, or a file
  one of them includes; or with the command from stdin (`xargs ssh`), or ssh run by `env -S`;
- a deploy by hand over ssh: what an ssh call runs on the other side (after its host, as its
  RemoteCommand, or a script fed to it on stdin) checks out another commit (git checkout, reset, pull…),
  builds, tags or pulls an image (`docker compose build`, `up --build`, `docker tag`), or runs the
  migrations (`run … migrate`, `alembic upgrade`). Reading and restarting what's there go through
  (`logs`, `ps`, `exec … psql`, `up -d --force-recreate api`);
- a gh alias that would do any of this: made (`gh alias set`, `gh alias import`) or run (`gh <alias>`, read
  from gh's config).

These read the raw command, so no wrapper, variable or spelling of a flag gets past them; the cost is
that a commit message or comment naming them in the command itself is stopped too (pass the text in a
file). Reading goes through (`gh api .../pending_deployments`, `deploy.sh status`), and so does starting a
Deploy run (`gh workflow run deploy.yml`), which waits for his approval like any other.

It stops a session that sets out to approve or deploy the usual ways. It isn't a sandbox: a script
written to get round it would, and CLAUDE.md says not to. Clicks in a browser aren't Bash calls.

A PreToolUse hook on every Bash call in this project: exit 2 blocks the call and stderr goes to Claude.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bash_words import parse  # noqa: E402

TELL = ("Approving or rejecting a production deploy, changing its environment, and deploying by hand (deploy.sh deploy "
        "or rollback, or a checkout, build, tag or migration over ssh) are Bharath's. Give him the Deploy run's link "
        "(https://github.com/Codewiz2898/sports-follow/actions/workflows/deploy.yml) and what it brings: every "
        "merge since the live commit, and any migration, secrets or server change. He approves it there. "
        "(If the command only mentions these in text, such as a commit message, pass the text in a file, or run "
        "the other commands in it on their own.)")

GATE = re.compile(r"pending_deployments|deployment_protection_rule")
SENDS = re.compile(r"\b(approved|rejected)\b|(\b|-X)post\b|--input|(=|\s)@", re.IGNORECASE)
# A waiting deploy approved or rejected, or an environment changed: its reviewers cleared, or it deleted (BUG-221).
MUTATIONS = re.compile(r"\b((approve|reject)Deployments|(create|update|delete|pin|reorder)Environment)\b", re.IGNORECASE)
GRAPHQL = re.compile(r"\bgraphql\b", re.IGNORECASE)
CALLS_API = re.compile(r"\bgh\b.*\bapi\b|\bcurl\b|\bwget\b|\bhttp\b|requests|urllib|fetch\(", re.IGNORECASE | re.DOTALL)
FED = re.compile(r"(?:(?:=|\s)@|--input[=\s]+|<\s*|\bcat\s+)['\"]?([^\s'\"|;&()<>]+)")  # files a call feeds in
# repos/o/r/, repos/$GITHUB_REPOSITORY/, repositories/<id>/, repos/$(gh repo view …)/: whatever comes before it (BUG-221).
ENVIRONMENT = re.compile(r"/environments\b")
CHANGES = re.compile(r"(\b|-X)(post|put|patch|delete)\b|(^|\s)(-[fF]\S*|--field|--raw-field|--input)", re.IGNORECASE)
NAMES_DEPLOY = re.compile(r"(?<![\w-])deploy\.sh\b(?![\s'\"]+(status|help|-h|--help)\b)")
FORCED_TEXT = re.compile(r"\b(ssh|mosh)\b[^\n;&|]*\s['\"]?(deploy|rollback)(?=['\"\s]|$)"  # when unreadable
                         r"|\bRemoteCommand\s*[=\s]\s*['\"]?(deploy|rollback)\b", re.IGNORECASE)
# ssh's RemoteCommand, as an -o option's value ("RemoteCommand=deploy", "RemoteCommand deploy") or a config file's line.
REMOTE_COMMAND = re.compile(r"^\s*RemoteCommand\s*(?:=\s*|\s+)(.*?)\s*$", re.IGNORECASE | re.MULTILINE)
INCLUDE = re.compile(r"^\s*Include\s*(?:=\s*|\s+)(.*?)\s*$", re.IGNORECASE | re.MULTILINE)  # a config file's Include line
# What makes a deploy by hand on the server: the checkout moved to another commit, an image built, tagged or pulled, the
# migrations run. Each a command's own subcommand, after its options (`git -C dir checkout`, `docker compose -f f build`),
# so a word in what `docker compose exec` runs (`python -c "…import…"`) doesn't count.
_OPTIONS = r"(?:\s+-[-\w]*(?:[=\s]+[^-\s;&|][^\s;&|]*)?)*\s+"
_DOCKER = r"\bdocker(?:-compose|\s+compose)?" + _OPTIONS
HAND_DEPLOY = re.compile(
    r"\bgit" + _OPTIONS + r"(checkout|switch|reset|pull|merge|rebase|restore|cherry-pick|revert|am|apply|stash|read-tree|update-ref)\b"
    + "|" + _DOCKER + r"(build|buildx|tag|load|import|commit|pull|image\s+(build|tag|load|import|pull))\b"
    + "|" + _DOCKER + r"up\b[^;&|\n]*\s--build\b"
    + "|" + _DOCKER + r"run\b[^;&|\n]*\smigrate\b"
    + r"|\balembic\b[^;&|\n]*\s(upgrade|downgrade|stamp)\b")
REMOTE_WORD = re.compile(r"\b(ssh|mosh)\b")
FEEDS_STDIN = {"xargs", "parallel"}  # they make a command of the words they read: `echo deploy | xargs ssh box`

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


def ssh_parts(words: list[str]) -> tuple[list[tuple[str, str]], list[str]]:
    """An ssh call's options that take a value, as (letter, value): -o X or -oX, -i key, -F file; and the words
    it runs on the other side. OpenSSH reads options after the host too (`ssh host -i key deploy`), up to the
    first other word or `--`, and flags bundled as getopt does: in `-qi key` or `-vo X` the first letter that
    takes a value takes the rest of the word, or else the next word (BUG-221)."""
    options: list[tuple[str, str]] = []
    i, host = 1, False
    while i < len(words):
        w = words[i]
        if w == "--":
            i += 1 if host else 2  # the host may come after it
            break
        if w.startswith("-") and len(w) > 1:
            i += 1
            for at, letter in enumerate(w[1:], 2):
                if letter in SSH_OPTION_ARGS:
                    value = w[at:]
                    if not value:
                        value, i = (words[i] if i < len(words) else ""), i + 1
                    options.append((letter, value))
                    break
            continue
        if host:
            break
        host, i = True, i + 1
    return options, words[i:]


def ssh_remote(words: list[str]) -> list[str]:
    """What an ssh call runs on the other side: the words after its options and host, or else the command its
    RemoteCommand option gives (`-o RemoteCommand=deploy`)."""
    options, remote = ssh_parts(words)
    if remote:
        return remote
    given = [m.group(1) for letter, value in options if letter == "o" for m in [REMOTE_COMMAND.match(value)] if m]
    return [given[0].strip("'\"")] if given else []


def ssh_configs(words: list[str], cwd: str | None) -> list[str]:
    """The text of each config file an ssh call reads: those it names (-F file), read from where the call runs, or
    else the user's own (~/.ssh/config) and the system's, which OpenSSH reads when none is named; and every file
    one of them includes (`Include ~/.orbstack/ssh/config`, `Include config.d/*`), as OpenSSH finds it: a
    relative one in ~/.ssh, or /etc/ssh for the system's (BUG-221)."""
    texts: list[str] = []
    seen: set[str] = set()

    def read(path: str, base: str, depth: int) -> None:
        if depth > 8 or path in seen or not os.path.isfile(path):
            return
        seen.add(path)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read(1 << 20)
        except OSError:
            return
        texts.append(text)
        for line in INCLUDE.findall(text):
            try:
                names = shlex.split(line)
            except ValueError:
                names = line.split()
            for name in names:
                name = os.path.expandvars(os.path.expanduser(name))
                for found in sorted(glob.glob(os.path.join(base, name))):
                    read(found, base, depth + 1)

    user = os.path.expanduser("~/.ssh")
    named = [value for letter, value in ssh_parts(words)[0] if letter == "F"]
    for name in named:
        read(os.path.join(cwd or os.getcwd(), os.path.expanduser(name)), user, 0)
    if not named:
        read(os.path.join(user, "config"), user, 0)
        read("/etc/ssh/ssh_config", "/etc/ssh", 0)
    return texts


def ssh_at(words: list[str]) -> int | None:
    """Where ssh is in a simple command's words: first (`ssh host …`), or run by another (`sudo ssh`, `xargs ssh`)."""
    return next((i for i, w in enumerate(words) if os.path.basename(w) in REMOTES), None)


def inner_lines(words: list[str]) -> list[str]:
    """Command lines a simple command runs that aren't its own words: a shell's -c script, eval's, and `env -S`'s
    (`env -S 'ssh box deploy'`, whose words bash_words leaves after taking `env` off). A line it runs on its stdin
    isn't here."""
    lines: list[str] = []
    name = os.path.basename(words[0]) if words else ""
    if name in SHELLS:
        script = shell_parts(words)[0]
        if script:
            lines.append(script)
    if name == "eval":
        lines.append(" ".join(words[1:]))
    # env's own options: after `env` (/usr/bin/env), or first, where bash_words took a plain `env` off.
    start = 1 if name == "env" else 0 if words[:1] and words[0].startswith("-") else len(words)
    for i in range(start, len(words)):
        w = words[i]
        if not w.startswith("-"):
            break
        if w in ("-S", "--split-string"):
            lines.append(" ".join(words[i + 1:]))
        elif w.startswith("--split-string="):
            lines.append(" ".join([w.split("=", 1)[1], *words[i + 1:]]))
        elif w.startswith("-S"):
            lines.append(" ".join([w[2:], *words[i + 1:]]))
        else:
            continue
        break
    return lines


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


def forced_deploy(command: str, depth: int = 0, cwd: str | None = None) -> bool:
    """`ssh … deploy` or `rollback`: with the deploy key, its forced command hands these to deploy.sh. Given
    after the host, as the RemoteCommand option, or as the RemoteCommand of a config file the call names."""
    if depth > 4:
        return False
    for simple in parse(command).commands:
        words = simple.words
        if not words or os.path.basename(words[0]) in READ_ONLY:
            continue  # `grep -rn ssh README.md deploy` only reads
        at = ssh_at(words)
        if at is not None:
            remote = " ".join(ssh_remote(words[at:])).split()
            remote = remote[1:] if remote[:1] == ["--"] else remote
            if remote[:1] in (["deploy"], ["rollback"]) or forced_deploy(" ".join(remote), depth + 1, cwd):
                return True
            if not remote and any(os.path.basename(w) in FEEDS_STDIN for w in words[:at]):
                return True  # its command comes from what xargs reads: `echo deploy | xargs ssh box`
            for text in ssh_configs(words[at:], cwd):
                for m in REMOTE_COMMAND.finditer(text):
                    if m.group(1).strip("'\"").split()[:1] in (["deploy"], ["rollback"]) or NAMES_DEPLOY.search(m.group(1)):
                        return True
        if any(forced_deploy(line, depth + 1, cwd) for line in inner_lines(words)):
            return True
    return False


def hand_deploy(command: str, cwd: str, depth: int = 0) -> bool:
    """A deploy by hand over ssh, past deploy.sh (BUG-221): what an ssh call runs on the other side (after its host,
    as its RemoteCommand, or a script it reads on stdin: a heredoc, a file fed in, or the line piped to it) moves the
    server's checkout, builds, tags or pulls an image, or runs the migrations (HAND_DEPLOY)."""
    if depth > 4:
        return False
    parsed = parse(command)
    for simple in parsed.commands:
        words = simple.words
        if not words or os.path.basename(words[0]) in READ_ONLY:
            continue
        at = ssh_at(words)
        if at is not None:
            remote = ssh_remote(words[at:])
            texts = [" ".join(remote), *parsed.heredocs, *files_fed(command, cwd)]
            texts += [m.group(1) for text in ssh_configs(words[at:], cwd) for m in REMOTE_COMMAND.finditer(text)]
            first = " ".join(remote).split()[:1]
            if not first or os.path.basename(first[0]) in SHELLS:
                texts.append(command)  # a shell there reads its stdin: whatever the line pipes to it
            if any(HAND_DEPLOY.search(text) for text in texts):
                return True
        if any(hand_deploy(line, cwd, depth + 1) for line in inner_lines(words)):
            return True
    return False


def gh_aliases(text: str) -> dict[str, str]:
    """name: expansion, from gh's config (its `aliases:` block) or a file for `gh alias import` (at its top): each
    `name: expansion` line, the expansion's YAML quotes taken off."""
    found: dict[str, str] = {}
    for m in re.finditer(r"^\s*([\w.-]+):[ \t]*(\S.*?)\s*$", text, re.MULTILINE):
        value = m.group(2)
        if len(value) > 1 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1].replace("''", "'") if value[0] == "'" else value[1:-1]
        found[m.group(1)] = value
    return found


def gh_config() -> str:
    """gh's config file, where the aliases it was given are kept."""
    folder = os.environ.get("GH_CONFIG_DIR") or os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "gh")
    try:
        with open(os.path.join(folder, "config.yml"), encoding="utf-8", errors="replace") as f:
            return f.read(1 << 20)
    except OSError:
        return ""


def expansion_blocked(expansion: str, args: list[str], cwd: str, depth: int) -> bool:
    """Whether what a gh alias expands to would be stopped: a shell command (`!…`), or a gh command."""
    line = " ".join([expansion[1:] if expansion.startswith("!") else f"gh {expansion}", *(shlex.quote(a) for a in args)])
    return blocked(line, cwd, depth + 1)


def gh_alias(command: str, cwd: str, depth: int) -> bool:
    """A gh alias that would approve, change an environment or deploy (BUG-221): one being made (`gh alias set`, or
    `gh alias import` of a file or stdin), or one gh's config already has, being run (`gh ok`)."""
    parsed = parse(command)
    for simple in parsed.commands:
        words = simple.words
        if not words or os.path.basename(words[0]) != "gh" or len(words) < 2:
            continue
        if words[1] == "alias" and words[2:3] == ["set"]:
            given = [w for w in words[3:] if not w.startswith("-")]
            shell = any(w in ("-s", "--shell") for w in words[3:])
            if len(given) >= 2 and expansion_blocked(("!" if shell else "") + given[1], [], cwd, depth):
                return True
        elif words[1] == "alias" and words[2:3] == ["import"]:
            names = [w for w in words[3:] if not w.startswith("-")]
            texts = files_fed(command, cwd, every=True) + (parsed.heredocs if names in ([], ["-"]) else [])
            if any(expansion_blocked(e, [], cwd, depth) for text in texts for e in gh_aliases(text).values()):
                return True
        else:
            block = re.search(r"^aliases:[ \t]*\n((?:[ \t]+\S.*(?:\n|$)|[ \t]*\n)*)", gh_config(), re.MULTILINE)
            expansion = gh_aliases(block.group(1)).get(words[1]) if block else None
            if expansion and expansion_blocked(expansion, words[2:], cwd, depth):
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


def files_fed(command: str, cwd: str, every: bool = False) -> list[str]:
    """The text of each file a call feeds in: query=@q.graphql, -d @body, --input body.json, < q, cat q |. With every,
    each file any word of the line names too: `jq . q.json | gh api graphql --input -` reads it on its way in."""
    names = list(FED.findall(command))
    if every:
        try:
            names += [w for s in parse(command).commands for w in s.words[1:] if not w.startswith("-")]
        except ValueError:
            pass
    texts: list[str] = []
    for name in dict.fromkeys(names):
        if name == "-" or not name:
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


def blocked(command: str, cwd: str, depth: int = 0) -> bool:
    if depth > 4:
        return False
    if GATE.search(command) and SENDS.search(command):
        return True
    if GRAPHQL.search(command) and (MUTATIONS.search(command) or (
            CALLS_API.search(command) and any(MUTATIONS.search(text) for text in files_fed(command, cwd, every=True)))):
        return True
    if changes_environment(command):
        return True
    if "SSH_ORIGINAL_COMMAND" in command and "deploy.sh" in command:
        return True  # deploy.sh takes its arguments from it: `SSH_ORIGINAL_COMMAND=deploy deploy.sh status`
    try:
        if forced_deploy(command, cwd=cwd) or (NAMES_DEPLOY.search(command) and could_run_deploy(command)):
            return True
        if hand_deploy(command, cwd) or gh_alias(command, cwd, depth):
            return True
        names = {os.path.basename(s.words[0]) for s in parse(command).commands if s.words}
    except ValueError:  # a line the reader can't follow: stopped if it names deploy.sh at all, or deploys by hand over ssh
        return bool(NAMES_DEPLOY.search(command) or FORCED_TEXT.search(command)
                    or (REMOTE_WORD.search(command) and HAND_DEPLOY.search(command)))
    # A script sent to a shell or to ssh on stdin (`ssh host 'bash -s' < release.sh`) is read for deploy.sh too.
    return bool(names & (SHELLS | REMOTES)) and any(NAMES_DEPLOY.search(text) for text in files_fed(command, cwd))


def main() -> int:
    try:
        call = json.load(sys.stdin)
        command = call.get("tool_input", {}).get("command") or ""
    except Exception:
        return 0
    lowered = command.lower()
    # An ssh call too: its config file (~/.ssh/config) may say what it runs (RemoteCommand), naming nothing here. And a
    # gh call, which may run an alias.
    if not ("deploy" in lowered or "environments" in lowered or "graphql" in lowered or "rollback" in lowered
            or re.search(r"\b(ssh|mosh|gh)\b", lowered)):
        return 0
    try:
        stop = blocked(command, call.get("cwd") or os.getcwd())
    except Exception:  # anything else unexpected: stopped only if it plainly names a deploy
        stop = bool((GATE.search(command) and SENDS.search(command)) or MUTATIONS.search(command)
                    or NAMES_DEPLOY.search(command) or FORCED_TEXT.search(command)
                    or (REMOTE_WORD.search(command) and HAND_DEPLOY.search(command)))
    if stop:
        print(TELL, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
