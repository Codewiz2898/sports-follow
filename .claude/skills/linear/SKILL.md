---
name: linear
description: Close Linear issues when a GitHub PR merges, and comment the PR link on them. Use after any merge in Sports Follow, whether you merged it or the user says it merged, and when the user asks to sync or close Linear issues.
---

# Linear after a merge

Linear: team **BHA**, project **Sports Follow**. Use the Linear connector's tools (load them with ToolSearch if deferred) and `gh`.

1. **Get the PR:** `gh pr view <number> --json number,title,url,state,body,headRefName,mergeCommit`. Stop if it isn't merged.
2. **Find its issues:** `BHA-n` in the body, title or branch, or `BUG-0xx` ids (match the issue titled `BUG-0xx · …`). If none are named, ask the user which issues it completes; don't guess.
3. **Close them:** `save_issue` with `state: "Done"`. Leave issues marked `Part of BHA-n` open.
4. **Comment the PR link** on each issue with `save_comment`, unless a comment with that PR URL is already there:
   `Merged in [#19 Battle royale esports from Liquipedia](https://github.com/Codewiz2898/sports-follow/pull/19)`
5. **Tell the user** which issues you closed.

When opening a PR, end its description with `Fixes BHA-n` (or `Part of BHA-n`) lines so step 2 finds them.
