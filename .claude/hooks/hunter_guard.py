"""Keeps a bug hunter (.claude/agents/bug-hunter.md) from changing git or GitHub: no commits, checkouts,
stashes, branches or pushes in any worktree, no PRs or comments. Other sessions work in those worktrees
and the stash stack is shared. Its repros (curl, docker, a server of its own) are its own business; the
agent's rules cover those. A PreToolUse hook in its frontmatter: exit 2 blocks the call, stderr goes to it.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bash_words import simple_commands  # noqa: E402
from reviewer_readonly import gh_refusal, git_refusal  # noqa: E402


def main() -> int:
    try:
        command = json.load(sys.stdin).get("tool_input", {}).get("command") or ""
        simples = simple_commands(command)
        reasons = [git_refusal(s.words[1:]) if s.name == "git" else gh_refusal(s.words[1:]) if s.name == "gh" else None
                   for s in simples]
    except ValueError:
        print("This command couldn't be read (an open quote?). Simplify it.", file=sys.stderr)
        return 2
    except Exception as exc:  # a guard that crashes would let everything through
        print(f"The hunter's git guard failed ({exc!r}), so the command was refused. Simplify it.", file=sys.stderr)
        return 2
    for reason in reasons:
        if reason:
            print(f"Blocked: {reason}. A hunter reads git and GitHub but never changes them; report what "
                  "should change instead.", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
