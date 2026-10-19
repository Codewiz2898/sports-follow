# Store listing

What to paste into Play Console, **Grow users → Store presence → Main store listing**. The name is
`sports_follow/app.json`'s; the rest is the site's copy (`web/index.html`'s description, the pages' words),
in its voice: short, plain sentences, no claims the app doesn't keep.

## App details

| Field | Value | Limit |
|---|---|---|
| App name | `stann` | 30 |
| Short description | `Follow any athlete, any sport: live scores, results and alerts for your players.` | 80 (this is exactly 80) |
| Full description | below | 4,000 |
| App category | Application → **Sports** | |
| Tags | Live scores, Sports news, Football, Cricket, Chess, Esports (pick up to 5 of those Play offers) | 5 |
| Contact email | `bharath@stann.in` | |
| Website | `https://stann.in` | |
| Privacy policy (App content) | `https://stann.in/privacy` | |
| Account deletion (App content) | `https://stann.in/account` (sign in, then Delete account) | |

Google shows the name and short description as typed: keep "stann" lower case, as fans see it everywhere.

## Full description

```
stann follows the athletes you care about, in any sport: football, cricket, tennis, basketball, chess and esports. Pick a player and their games come to you.

Your players first
Today opens on your players' live games, then their results and what's coming up. Follow a team and its games come too. Follow one match for its moments alone, until it ends.

Alerts that know the difference
One alert a game, replaced as its moments come: a goal, a wicket, a hundred, a game turning. Sound only for the big ones, and never in your quiet hours. Per player, you choose how much you want to hear.

Live scores on your lock screen
While a game you follow is on, its score stays on your lock screen and updates as it goes. Stop it with one tap.

Their own numbers while they play
Not just the score: your player's line in the game as it happens, and their recent form.

Share a moment
Send a player or a moment to WhatsApp with a card that shows the score.

Sign in if you like
Everything works without an account. Sign in with an email code and your players follow you to every device.

No ads. No selling your data. Read the policy at stann.in/privacy.
```

That text is 1,144 characters. Play keeps line breaks; it ignores Markdown, so the headings are plain lines.

Before pasting, check the sports named in the first line against what production has turned on (the admin
panel's sports, BHA-207 for PUBG): the listing must not promise a sport that's off. Esports titles are left
unnamed on purpose, as which ones are on changes with the sources.

## What Play asks about the app (App content)

Answers for the declarations under **Policy → App content**. The data safety form has its own file,
`data-safety.md`.

| Declaration | Answer | Why |
|---|---|---|
| Privacy policy | `https://stann.in/privacy` | Covers the app since the PR that added this folder. |
| Ads | No, the app has no ads | None in the code. |
| App access | All functionality is available without special access | Signing in is optional; following and alerts work anonymously. |
| Content rating | Fill the IARC questionnaire as **Utility, Productivity, Communication or Other**: no violence, sexual content, language, controlled substances, gambling; no user-generated content shown to others; no chat; no location sharing; no purchases | Rated Everyone / 3+. |
| Target audience | **18 and over** only | Not designed for children; picking a younger group adds the Families programme's questions. |
| News app | No | News lines about a player are a small part; the app's purpose is scores and alerts. |
| COVID-19 contact tracing | No | |
| Data safety | `data-safety.md` | |
| Government app | No | |
| Financial features | No financial features | |
| Health | No health features | |
| Account deletion | Yes, in the app (Account → Delete account) and at `https://stann.in/account`; deleting the account deletes all its data (daily backups kept 14 days) | `web/src/pages/AccountPage.tsx`, `sports_follow/accounts.py`. |
| Advertising ID | No, the app doesn't use it | No ads SDK; Firebase Messaging doesn't add the `AD_ID` permission (checked in the generated manifest). |
