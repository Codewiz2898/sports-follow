"""Keeps the reviewer agent's Bash to reading: commands it may run, and only their read-only forms.

Another session's worktree may hold uncommitted work, and the stash stack is shared by every worktree,
so a reviewer must never commit, check out, stash, delete, install or write. Anything not known to be
read-only is refused, with the reason. A guard against slips, not a sandbox: python can still write.
A PreToolUse hook in .claude/agents/reviewer.md: exit 2 blocks the call and stderr goes to the agent.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bash_words import Simple, simple_commands  # noqa: E402

READERS = {
    "cat", "head", "tail", "wc", "grep", "egrep", "fgrep", "rg", "ls", "sort", "uniq", "cut", "tr", "diff",
    "cmp", "comm", "jq", "file", "stat", "du", "pwd", "echo", "printf", "true", "false", "test", "[",
    "basename", "dirname", "realpath", "readlink", "which", "type", "date", "column", "nl", "tac", "rev",
    "fold", "paste", "join", "xxd", "od", "hexdump", "strings", "tree", "cd", "zcat", "gzcat", "seq",
    "expr", "printenv", "uname", "sleep", "shasum", "sha256sum", "md5", "base64", "awk", "pytest", "read",
    "[[", "command",
}
GIT_READS = {
    "log", "diff", "show", "status", "blame", "grep", "ls-files", "ls-tree", "rev-parse", "rev-list",
    "merge-base", "describe", "shortlog", "cat-file", "for-each-ref", "name-rev", "range-diff",
    "whatchanged", "check-ignore", "check-attr", "count-objects", "version", "help", "var", "show-ref",
    "show-branch", "cherry", "difftool", "diff-tree", "diff-index", "diff-files", "ls-remote",
}
# The project's own modules a reviewer may run: the rest change the database or call live sources.
PROJECT_READS = {"sports_follow.usage": None, "sports_follow.players": {"list"}}
GIT_OPTIONS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
# Options of `git branch` and `git tag` whose value is the next word; any other word names a branch or tag to make.
REF_VALUE_OPTIONS = {"--contains", "--no-contains", "--merged", "--no-merged", "--points-at", "--sort",
                     "--format", "--color", "--column"}
GH_READS = {
    "pr": {"view", "diff", "list", "checks", "status"},
    "issue": {"view", "list", "status"},
    "run": {"view", "list", "watch"},
    "release": {"view", "list"},
    "repo": {"view"},
    "workflow": {"view", "list"},
    "search": None,  # every form reads
    "auth": {"status"},
    "browse": None,
}
ALEMBIC_READS = {"heads", "history", "current", "show", "branches", "check"}


def refusal(simple: Simple) -> str | None:
    """Why this simple command may change something, or None when it only reads."""
    words, name = simple.words, simple.name
    if simple.writes:
        return f"it writes to {simple.writes[0]}"
    if not words:
        return None
    args = words[1:]
    if name == "git":
        return git_refusal(args)
    if name == "gh":
        return gh_refusal(args)
    if name.startswith("python"):
        module = args[args.index("-m") + 1] if "-m" in args[:-1] else ""
        after = args[args.index("-m") + 2:] if module else []
        if module in ("pip", "venv", "ensurepip"):
            return "it installs packages"
        if module == "alembic":
            return refusal(Simple(["alembic", *after]))
        if module.startswith("sports_follow"):
            allowed = PROJECT_READS.get(module, set())
            if allowed is None or after[:1] and after[0] in allowed:
                return None
            return f"`python -m {module}` changes the database or calls live sources"
        return None
    if name == "ruff":
        if any(a in ("--fix", "--fix-only", "--unsafe-fixes", "--add-noqa", "clean") for a in args):
            return "it changes files"
        if "format" in args and not ({"--check", "--diff"} & set(args)):
            return "it reformats files"
        return None
    if name == "basedpyright":
        return "it rewrites the type baseline" if "--writebaseline" in args else None
    if name == "alembic":
        sub = next((a for a in _positional(args, {"-c", "--config", "-n", "--name", "-x"})), "")
        return None if sub in ALEMBIC_READS else f"`alembic {sub}` changes the database"
    if name == "sed":
        if any(a.startswith("--in-place") or (a.startswith("-") and not a.startswith("--") and ("i" in a or "I" in a))
               for a in args):
            return "`sed -i` edits files in place"
        return None
    if name == "find":
        bad = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"}
        return "it can delete or run commands on what it finds" if bad & set(args) else None
    if name in ("gzip", "gunzip"):
        flags = "".join(a.lstrip("-") for a in args if a.startswith("-") and not a.startswith("--"))
        keeps = {"--stdout", "--list", "--test"} & set(args) or set("clt") & set(flags)
        return None if keeps else "it replaces the file it reads"
    if name == "tar":
        first = args[0].lstrip("-") if args else ""
        lists = "--list" in args or ("t" in first and not set("xcru") & set(first))
        return None if lists else "only `tar -t` (list) reads"
    if name == "unzip":
        return None if {"-l", "-p", "-t", "-v", "-Z"} & set(args) else "it writes the files it extracts"
    if name in READERS:
        return None
    return f"`{name}` isn't on the reviewer's list of read-only commands"


def _positional(args: list[str], with_value: set[str]) -> list[str]:
    """The arguments that aren't options or option values."""
    out, skip = [], False
    for a in args:
        if skip:
            skip = False
        elif a in with_value:
            skip = True
        elif not a.startswith("-"):
            out.append(a)
    return out


def git_refusal(args: list[str]) -> str | None:
    """Why a git command (its arguments) may change the repository, or None when it only reads."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in GIT_OPTIONS_WITH_VALUE else 1
    if i >= len(args):
        return None
    verb, rest = args[i], args[i + 1:]
    if any(a.startswith("--output") for a in rest):
        return "`--output` writes a file"
    if verb in GIT_READS:
        return None
    if verb == "fetch":
        return "a refspec with `:` moves a local branch" if any(":" in a for a in rest) else None
    if verb == "stash":
        return None if rest[:1] in (["list"], ["show"]) else "`git stash` changes the shared stash and the worktree"
    if verb == "worktree":
        return None if rest[:1] == ["list"] else "only `git worktree list` reads"
    if verb == "remote":
        return None if not rest or rest[0] in ("-v", "--verbose", "show", "get-url") else "it changes the remotes"
    if verb == "reflog":
        return "it changes the reflog" if rest[:1] in (["expire"], ["delete"]) else None
    if verb == "config":
        reads = {"--get", "--get-all", "--get-regexp", "--list", "-l", "--get-urlmatch"}
        return None if reads & set(rest) else "it changes the git config"
    if verb in ("branch", "tag"):
        listing = "--list" in rest or "-l" in rest
        for j, a in enumerate(rest):
            if a.startswith("--"):
                if a.split("=")[0] not in REF_VALUE_OPTIONS | {"--list", "--all", "--remotes", "--verbose", "--show-current",
                                                               "--no-color", "--abbrev", "--no-abbrev", "--ignore-case", "--omit-empty"}:
                    return f"`git {verb} {a}` changes refs"
            elif a.startswith("-"):
                writes = set("dDmMcCftu") if verb == "branch" else set("dasfuFmMe")
                if writes & set(a[1:]):
                    return f"`git {verb} {a}` changes refs"
            elif not listing and (j == 0 or rest[j - 1] not in REF_VALUE_OPTIONS):
                return f"`git {verb} {a}` makes a {verb}"
        return None
    return f"`git {verb}` changes the repository"


def gh_refusal(args: list[str]) -> str | None:
    """Why a gh command (its arguments) may change GitHub, or None when it only reads."""
    group = args[0] if args else ""
    if group == "api":
        method = next((args[j + 1] for j, a in enumerate(args[:-1]) if a in ("-X", "--method")), "GET")
        method = next((a.split("=", 1)[1] for a in args if a.startswith("--method=")), method)
        method = next((a[2:] for a in args if a.startswith("-X") and len(a) > 2), method)
        if args[1:2] == ["graphql"]:  # a query sends fields too; only a mutation writes
            return "a GraphQL mutation writes" if any("mutation" in a for a in args) else None
        fields = {"-f", "-F", "--field", "--raw-field", "--input"} & {a.split("=")[0] for a in args}
        fields |= {a[:2] for a in args if a[:2] in ("-f", "-F") and len(a) > 2}
        return None if method.upper() == "GET" and not fields else "it writes through the GitHub API"
    if group not in GH_READS:
        return f"`gh {group}` isn't a read"
    allowed = GH_READS[group]
    if allowed is None or (len(args) > 1 and args[1] in allowed) or "--help" in args:
        return None
    return f"`gh {' '.join(args[:2])}` changes GitHub"


def main() -> int:
    try:
        command = json.load(sys.stdin).get("tool_input", {}).get("command") or ""
        reasons = [r for r in map(refusal, simple_commands(command)) if r]
    except ValueError:
        print("The reviewer only reads, and this command couldn't be read (an open quote?). Simplify it.", file=sys.stderr)
        return 2
    except Exception as exc:  # a guard that crashes would let everything through
        print(f"The reviewer's read-only guard failed ({exc!r}), so the command was refused. Simplify it.", file=sys.stderr)
        return 2
    for reason in reasons:
        if reason:
            print(f"Blocked: {reason}. The reviewer only reads: use git log/diff/show, gh pr view/diff, Read, Grep or "
                  "Glob, and report what should change instead of changing it.", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
