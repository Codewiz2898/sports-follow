---
name: bug-bounty
description: How Sports Follow is tested bug-bounty style - hunter agents by area, one ranked bug board, every bug listed before any is fixed, then fixes by severity tier with a PR each, then a second round. Use when asked for a bug hunt, a bug bounty, a sweep, a test round, or a regression round after fixes.
---

# Bug bounty for Sports Follow

Bharath wants a complete, ranked picture before anything is fixed, and fixes that land in batches he can
review and merge. So a sweep has two halves: **hunt and list everything**, then **fix tier by tier**, then
a second round to catch regressions and what the first missed.

## 1. Set up

- **A stack of your own.** Hunters poke at a running app, so give them one that isn't the main checkout
  (other sessions work there): follow the `isolated-stack` skill, at the commit under test.
- **The board** is `BUGBOARD.md` at the main checkout's root. It's in `.gitignore` and must stay out of
  git: the repo is public, and the board can describe open security holes. A new sweep starts a new
  board; keep the last one as `BUGBOARD-<date>.md` beside it. Its header names the branch, commit and
  stack (ports, database), then the legends below, then the **Hunters** table (hunter, area, status),
  the **Board** table (ID, Sev, Conf, Area, Title, Hunter, Status, sorted by severity), and **Details**,
  one section per bug.
- If Bharath wants a live page too, keep a private Artifact with a database beside the file (collections
  `bugs/BUG-nnn` and `meta/board`) and update it with ArtifactData as reports come in. Ask him for the
  last sweep's link before making a new one.

## 2. Hunt

Spawn one `bug-hunter` agent per area, all at once, in the background (`.claude/agents/bug-hunter.md`
holds their rules and report format). Give each its area, the commit, what changed since the last sweep,
the stack's worktree (they read the code there, so file:line matches the commit) and the stack's exact
environment (`isolated-stack`, step 5): a server a hunter starts for a repro must use all of it, or it
falls back to the main stack's Redis, keys and research agent. The areas last time, to adjust to what the code has now:

| Hunter | Area |
|---|---|
| H1 | Search and the player registry |
| H2 | Following, player cards, merges, accounts |
| H3 | Live events, moments, alerts, push |
| H4 | Adapters, source parsers and source budgets |
| H5 | Security and API abuse (sign-in, cookies, rate limits, fetch_url) |
| H6 | Worker, scheduler, chess engine |
| H7 | Web app code (React, PWA, service worker) |
| Lead | You: the web app in the browser at phone size (375 px), every screen and flow |

Hunters report; they never fix. As reports arrive, put them on the board:

- **Number** in order of discovery, continuing from the highest `BUG-nnn` on the last board or in Linear.
- **Dedupe**: the same root cause found twice is one bug; its Hunter column lists everyone who found it,
  and their repros go in its details.
- **Triage honestly**: re-read the code for anything Suspected; demote what's oversold, promote what's
  undersold. Severity is about fans and cost, not how clever the find was.

**Severity:** Critical = data loss, a real security hole, or the app unusable · High = a core feature
broken or wrong for many fans, or unbounded cost · Medium = broken in edge cases, or wrong data
sometimes · Low = cosmetic or rare · Info = hardening, not a defect today.
**Confidence:** Confirmed = reproduced, with the repro on the board · Suspected = from reading the code.

When every hunter is done, mirror the board to Linear (follow the `linear` skill): one issue per bug,
titled `BUG-nnn · <title>`, labelled `Bug` and one Area label (Alerts, Worker, Sources, Search & registry,
Follows & cards, Web app, API & security, Tests), under the sweep's epic. Then **stop and show Bharath the
board**, counts by severity first. Fix nothing until he says which tiers.

## 3. Fix, tier by tier

One tier at a time, as he asks: Critical and High, then Medium, then Low and Info. Each tier is one
branch (`fix/bug-bounty-<tier>`) and one pull request.

- **Make the fix agents' worktrees yourself**, on the tier branch's commit. (Agent worktree isolation once
  started from `main` instead of the branch.)
- **Group bugs so agents own disjoint functions**: two agents editing one function is a merge conflict
  and often a wrong fix.
- **Every bug gets a regression test that fails on the old code.** Check it does: run it before the fix.
- **Review every diff yourself**, then merge the agents' work into one integration worktree and run
  everything there, with the main checkout's venv by absolute path (`<venv>`; once,
  `<venv>/bin/pip install -r requirements-dev.txt`):
  - `SPORTS_FOLLOW_TEST_DATABASE_URL=postgresql+psycopg://sports:sports@127.0.0.1:5433/sports_follow_test <venv>/bin/python -m pytest -rs`.
    Without the variable the Postgres tests skip, so a regression test there proves nothing. They empty
    every table: point it only at `sports_follow_test` (migrated to head), never at a real database or
    the stack's copy.
  - `<venv>/bin/ruff check .` and `<venv>/bin/basedpyright --pythonpath <venv>/bin/python`.
  - In `web/`: `npm install`, `npx tsc -p tsconfig.app.json`, `npm run build`.
  - Boot the merged API on a migrated copy of the database (`isolated-stack`).
- **Then the `reviewer` agent** on the tier branch (CLAUDE.md, "Before a pull request").
- Commit and open the PR only when Bharath asks; merge only when he asks. The PR body has a `Fixes BHA-n`
  line per bug and the `Reviewed:` line. Mark the issues In Review.
- **The repo is public and production is live.** PR titles, bodies and commit messages say what the fix
  does, never how to exploit what it fixes: repros stay in Linear and on the board. Tell Bharath to deploy
  the Critical and High tier as soon as it merges.
- After the merge: the `linear` skill (Done, and a "Merged in" comment on each), and each bug's Status on
  the board becomes `Fixed (F<n>, PR #<n>)`, F<n> being the fix group.

## 4. Round two

Once every tier he asked for is merged: a fresh `isolated-stack` at the new main, then hunters again,
briefed to check last round's fixes still hold (try to break each fix) and to hunt what the first round
missed. Same board rules, new IDs; a fix that didn't hold reopens its bug rather than getting a new one.

## Don'ts

- Don't touch the main checkout while another session works there; the stack and the board are yours.
- Don't let a sweep spend money: the isolated stack runs with the research agent and paid sources off,
  and hunters don't loop against a source (`isolated-stack` says why: its spend is invisible to the main
  stack's counts).
- Don't leave the stack running after the round: tear it down (`isolated-stack`, "When the round ends").
