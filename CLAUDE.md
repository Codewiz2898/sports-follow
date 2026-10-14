# Sports Follow

Work is tracked in Linear (team BHA, project Sports Follow). Whenever you merge or open a pull request, or create or update a Linear issue, follow the `linear` skill (`.claude/skills/linear/SKILL.md`): a merged PR closes its issues with a comment linking the PR, and issues waiting on Bharath carry the `Needs you` label.

## Code organization

Sports, players and data sources are things we add, remove and switch often, so each of these must stay a small change in one place:

- **Add or remove a sport.** Everything about a sport (names and aliases, colour, moment and form rules, alert wording, scoreboard) lives with that sport, plus one entry in the list of sports. No `if sport == "cricket"` branches in shared code: shared code asks the sport.
- **Enable or disable a sport.** A setting, not a code change. A disabled sport isn't searched, imported, polled or offered in the app.
- **Add or remove a player.** A command or a row, never code. Removing a player takes everything that hangs off them with it, and a weekly import doesn't bring them back.
- **Add or remove a data source for a sport.** One adapter module that describes itself (its sports, token, credit, id format), plus one entry in that sport's list of sources, in order of preference. Nothing else names the source.

The web app takes the list of sports from the API rather than keeping its own. Much of today's code predates these rules: don't add to the scatter, and move what you touch toward them.
