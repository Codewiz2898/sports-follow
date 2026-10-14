---
name: linear
description: How to handle Linear for Sports Follow. Close issues when a GitHub PR merges and comment the PR link on them, and mark issues that need the user with the "Needs you" label. Use after any merge (whether you merged it or the user says it merged), whenever you create or update a Linear issue, and when the user asks to sync or close Linear issues.
---

# Linear for Sports Follow

Linear: team **BHA**, project **Sports Follow**. Use the Linear connector's tools (load them with ToolSearch if deferred) and `gh`.

## After a merge

1. **Get the PR:** `gh pr view <number> --json number,title,url,state,body,headRefName,mergeCommit`. Stop if it isn't merged.
2. **Find its issues:** `BHA-n` in the body, title or branch, or `BUG-0xx` ids (match the issue titled `BUG-0xx · …`). If none are named, ask the user which issues it completes; don't guess.
3. **Close them:** `save_issue` with `state: "Done"`. Leave issues marked `Part of BHA-n` open.
4. **Comment the PR link** on each issue with `save_comment`, unless a comment with that PR URL is already there:
   `Merged in [#19 Battle royale esports from Liquipedia](https://github.com/Codewiz2898/sports-follow/pull/19)`
5. **Tell the user** which issues you closed.

When opening a PR, end its description with `Fixes BHA-n` (or `Part of BHA-n`) lines so step 2 finds them.

## The "Needs you" label

Add the `Needs you` label to an issue when its next step is one only the user can take: paying for something, signing up for an account, holding a key or password, getting a licence or permission, making a decision, or testing on their own device. Check this whenever you create or update an issue, and remove the label once that step is done.
