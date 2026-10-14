"""Stops `gh pr create` until the description has a "Reviewed:" line (CLAUDE.md, "Before a pull request").

A PreToolUse hook on every Bash call in this project: exit 2 blocks the call and stderr goes to Claude.
It lets everything else through, and lets a command through when it can't be read rather than stall
every session.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bash_words import SUBSTITUTED, parse  # noqa: E402

ASK = ("Before opening a pull request, have the reviewer agent (.claude/agents/reviewer.md) review the branch, "
       "then add a 'Reviewed:' line to the description, next to the 'Fixes BHA-n' lines, saying what it found "
       "and what you fixed. See CLAUDE.md, \"Before a pull request\".")


def description(words: list[str]) -> tuple[str | None, str | None]:
    """The --body text and the --body-file path of a `gh pr create`, either of which may be missing."""
    body = path = None
    for i, w in enumerate(words):
        following = words[i + 1] if i + 1 < len(words) else ""
        if w in ("--body", "-b"):
            body = following
        elif w.startswith("--body="):
            body = w.split("=", 1)[1]
        elif w in ("--body-file", "-F"):
            path = following
        elif w.startswith("--body-file="):
            path = w.split("=", 1)[1]
        elif w.startswith("-F") and len(w) > 2:
            path = w[2:]
    return body, path


def main() -> int:
    try:
        call = json.load(sys.stdin)
        command = call.get("tool_input", {}).get("command") or ""
        if "gh" not in command:
            return 0
        parsed = parse(command)
    except Exception:
        return 0
    cwd = call.get("cwd") or os.getcwd()
    for simple in parsed.commands:
        words = simple.words
        if simple.name == "cd" and len(words) > 1:
            cwd = os.path.join(cwd, os.path.expanduser(words[1]))
            continue
        if simple.name != "gh" or words[1:3] not in (["pr", "create"], ["pr", "new"]):
            continue
        if "--help" in words or "-h" in words:
            continue
        body, path = description(words)
        if path == "-" or (body and SUBSTITUTED in body):
            # From stdin, or --body "$(cat <<'EOF' ... EOF)": the text is in a heredoc, if anywhere.
            text = "\n".join(parsed.heredocs)
            if not parsed.heredocs and "Reviewed:" not in command:
                print("The description comes from a command this hook can't read. Pass it with --body-file "
                      "<path>, or in a heredoc, with its 'Reviewed:' line.", file=sys.stderr)
                return 2
        elif path:
            path = os.path.join(cwd, os.path.expandvars(os.path.expanduser(path)))
            try:
                with open(path, encoding="utf-8") as f:
                    text = f.read()
            except OSError:
                print(f"Couldn't read the description file {path}. Pass its absolute path, or the description "
                      "itself in a heredoc with --body-file -.", file=sys.stderr)
                return 2
        else:
            text = body or ""
        if "Reviewed:" not in text:
            print(ASK, file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
