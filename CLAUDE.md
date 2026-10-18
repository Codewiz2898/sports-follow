# Sports Follow

Work is tracked in Linear (team BHA, project Sports Follow). Whenever you merge or open a pull request, or create or update a Linear issue, follow the `linear` skill (`.claude/skills/linear/SKILL.md`): a merged PR closes its issues with a comment linking the PR, and issues waiting on Bharath carry the `Needs you` label.

To find out why something went wrong, locally or on the production server (a fan didn't get a push, a card is stuck, a live score is stale, an alert email), follow the `debug` skill (`.claude/skills/debug/SKILL.md`). It also says what's safe to run on production and what needs Bharath first.

## Code organization

Sports, players and data sources are things we add, remove and switch often, so each of these must stay a small change in one place:

- **Add or remove a sport.** Everything about a sport (names and aliases, colour, moment and form rules, alert wording, scoreboard) lives with that sport, plus one entry in the list of sports. No `if sport == "cricket"` branches in shared code: shared code asks the sport.
- **Enable or disable a sport.** A setting, not a code change. A disabled sport isn't searched, imported, polled or offered in the app.
- **Add or remove a player.** A command or a row, never code. Removing a player takes everything that hangs off them with it, and a weekly import doesn't bring them back.
- **Add or remove a data source for a sport.** One adapter module that describes itself (its sports, token, credit, id format), plus one entry in that sport's list of sources, in order of preference. Nothing else names the source.

The web app and the Android app (`mobile/`) take the list of sports from the API rather than keeping their own. `tests/test_sources.py`, `tests/test_sports.py` and `tests/test_web_names.py` hold the code to these rules; the README's "Adding or removing" notes say where each change goes.

## Before a pull request

Run the checks in the README's "Run it" (tests, `ruff check`, `basedpyright`, the web build). Then have the `reviewer` agent (`.claude/agents/reviewer.md`) review the branch: name the branch or worktree, and paste the Linear issue's title and description (it can't read Linear). Fix what it marks Blocking, and for anything you leave, say why in the pull request. Add a `Reviewed:` line next to the `Fixes BHA-n` lines, such as `Reviewed: reviewer agent, 2 fixed, 1 left (see above)`. A hook stops `gh pr create` without one.

## Deploys

Approving a production deploy is Bharath's, even after he says "deploy now": start the Deploy run if needed, then give him its link and what it brings (every merge since the live commit, any migration, secrets or server change), and he approves it on GitHub. `gh` runs as his account, so an approval sent from a session would count as his. A hook (`.claude/hooks/deploy_gate.py`) blocks approving or rejecting a waiting deploy, changing the `production` environment, and `deploy.sh deploy` or `rollback`. It isn't a sandbox: rebuilding or recreating containers on the server by hand (`docker compose … up`) also needs his yes first (the `debug` skill), and the hook doesn't catch that.
