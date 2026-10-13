# Backlog

What's next for Sports Follow, most wanted first. Each item says what exists today, what "done"
looks like, and the open questions.

## Now: lean India-first launch

Chosen 2026-09-30 (research: https://claude.ai/artifact/AKZ9gdcQ1xCGnVr7b16z8G). Licensed live data
for cricket, football and chess first; about $150–350 a month.

- [x] Source switch: `SPORTS_FOLLOW_SOURCES` lists the licensed sources a public build runs; ESPN stays
  development-only.
- [x] Sports without a licensed source get AI-built pages with no live scores or alerts; AI live checks
  are off (`SPORTS_FOLLOW_AGENT_LIVE`).
- [x] Lichess token support; token saved.
- [x] Sportmonks Football adapter: fixtures, live matches with every player's line, goal and card
  moments, season stats, a year of history. Runs whenever a token is saved and comes before ESPN;
  followed footballers move over on their next refresh (ESPN games unlinked, so nothing is polled or
  listed twice). The history lane keeps 1,000 of the 2,500 hourly fixture requests for live games.
- [ ] Sportmonks dashboard: pick the Growth plan's leagues. National teams (Egypt, India…), the
  Champions League, ISL, Saudi Pro League and MLS aren't on it yet, so a player's country has no
  fixtures. The trial ends 2026-10-14; check whether it converts to paid.
- [x] Live polling at scale (football): every armed and live Sportmonks football match is polled by
  one lane (`structured.poll_lane`), one `fixtures/multi` request a round for up to 50 matches. A busy
  matchday costs 240 requests an hour instead of 240 per match, and one worker thread instead of one
  per match. Cricket keeps a poll job per match (6 requests a minute each, of 180); its `livescores`
  endpoint could batch it the same way once checked against a real response.
- [x] Sportmonks Cricket adapter: team fixtures kept only where the player is in the XI (or the
  season's squad before it's out), live scorecards with every player's line and dismissal, the chase
  ("need 42 from 28 balls"), fifty, hundred, out and wicket alerts, form from scorecards, a year of
  history. Built on the free plan (T20 internationals, Big Bash, CSA T20 Challenge).
- [ ] Start the Sportmonks Cricket World trial (€75/month after 14 days): ODIs, Tests, IPL and the
  rest. Then move cricket ahead of ESPN in `adapters/__init__.py` so a development build uses it too.
  Until then Kohli and Rohit (no T20Is since 2024) have no sides on Sportmonks.
- [x] Player ids for football: registry athletes resolve by Sportmonks search, name and birth date.
- [x] Player ids for cricket: Sportmonks search by surname, then birth date (its 1 January
  placeholder dates count as unknown).
- [x] Credits page (`/credits`, from the sidebar, Notifications and each player page): the sources this
  build runs (Lichess with its CC BY-SA 4.0 licence), Wikidata, FIDE ratings, Stockfish, the AI model
  that writes profiles and news, news publishers, "not official records" and "not affiliated"
  notices, and the software with its licences (checked against the installed packages by a test).
  Photo credits join it when item 6 lands.
- [x] Hosting setup (`deploy/`): one server runs the whole app with Docker Compose (Caddy for HTTPS,
  the API serving the web app, the worker with Stockfish, Postgres, Redis, the llm-providers gateway,
  daily database dumps), licensed sources only. Scripts set up the server, send the secrets without
  printing them and copy the registry. Rehearsed end to end on the Mac.
- [ ] Run it: this Mac or a spare laptop behind a Cloudflare Tunnel with a domain while testing, then a
  server (DigitalOcean Bangalore or Lightsail Mumbai, 2 vCPU / 4 GB, $24/month) before public launch.
- [ ] Off-site backups (object storage) and an uptime check on `/api/health`.
- [ ] FIDE's written permission for the rating list.
- [ ] Measure 30-day retention, push opt-in, follows per user and share of users outside India before
  adding basketball or tennis live data.

## Requested

### 1. Previous results: a player's full history (built)

**Built.** Every finished game a source returns is kept as a result from the player's side
(`event_player.result`) and never fetched again. The Results tab pages through the whole history,
newest first ("Show more"), and each game opens to its final score, the player's full line and the
source. The Following page has "Latest results" across every followed player, and the Stats tab
switches between the source's numbers and form over the last 10 or 5 results (per sport: record,
apps, goals and assists; points, rebounds, assists and shooting; runs, average and wickets; sets;
chess score).

Older results come from a rate-limited history lane (`sports_follow/history.py`): at most 20 source
requests a minute system-wide. It fills in missing scorecards newest first, then reads, once, what
the refresh window doesn't reach:

| Sport | History |
|---|---|
| Football | A year before the refresh window, 90 days a page (Sportmonks); this season and last from ESPN in development |
| Basketball | This season and last (team schedules) |
| Tennis | A year of weekly draws |
| Cricket | A year before the refresh window, 90 days a page (Sportmonks); six months of daily feeds from ESPN in development |
| Chess | Two months of broadcasts (Lichess's one request a second makes more slow) |

History also grows by itself: a game stays stored after it drops out of the source's window.

**Next**
- Older chess history, a few Lichess requests a minute.
- Filters on the Results tab (competition, home/away, wins only).
- Season-by-season totals once there's more than a season stored.

### 2. Push notifications for interesting moments (built)

**Built.** Each live poll compares the player's line with the previous one and stores typed moments
once (`sports_follow/moments.py`); the noise rules (`sports_follow/alerts.py`) plan each fan's pushes
and `sports_follow/notify.py` sends them by Web Push to every device, and the app shows them as toasts
when it's open. Verified end to end on an Android emulator and a phone (Chrome, through FCM),
including tap-through to the player.

| Sport | Moments |
|---|---|
| All | Game starts with the player in it; final result, unless the line-ups show they weren't in the squad |
| Football | Line-up: starts or on the bench; goal (an equaliser, a goal that puts their side ahead, "late" from 80', a hat-trick; with minute and assist), assist, red card (key); yellow card, comes on (everything) |
| Cricket | In the XI or named a substitute; 50 / 100 / 150 / 200, 3+ wickets, out with the score (key) |
| Basketball | 30 / 40 / 50 / 60 points, triple-double (key); 20 points, double-double (everything) |
| Tennis | Upset of a higher seed (key); each set won or lost (everything) |
| Chess | Winning, in trouble, a turnaround (key); better, worse, level again (everything); from Stockfish |
| News | Injury, transfer, retirement (key); milestone (everything), from a rebuild's news |

Line-ups come from the team sheet the source publishes before the start (Sportmonks football and
cricket, ESPN football), seen by the armed poll from 30 minutes before. Every moment has a priority
(0–100): a hundred 90, a fifty 70, a late equaliser 95, a goal 85, a duck 65, a yellow card 20.

**The noise rules** (`alerts.py`, built 2026-10-12), app first: a notification is something on the
phone, so the rules are about the tray and the sound.

- Per player: key moments (default) make a sound; everything adds the minor ones silently; results
  only; off. Per device: quiet hours (silent, not dropped).
- One notification per game per fan (tag `e{event}`, and the same Web Push `Topic`, so a phone that was
  offline gets the latest rather than a burst). A new moment replaces it with the story so far
  underneath, across every followed player in the game ("Kohli reaches 100" over "Rohit out for 45").
  Moments from one poll are one push, led by the highest priority. News is one notification per player.
- "Mute this game" on the notification (`PUT /api/me/mutes/{event}`).
- Sound is rationed: a game at most once every 3 minutes, a phone at most 6 times an hour; past
  either the push arrives silently. Priority 90+ always makes a sound. A game's start is silent if the
  fan already heard its line-up.
- A first look at a game already under way is a baseline, so nobody gets "reaches 50" an hour late.
  Live moments aren't sent after 15 minutes (line-ups after an hour); results and news after 12 hours.

**Delivery.** A batch is claimed in the database before sending, so it goes once; every push is a
`notification` row (lead kind, lines, loud, devices, delivered, opened). Pushes to devices go out at
once (16 at a time), not one after another. The tick sends any batch a worker restart left unclaimed.
Tapping a notification records the open (`notify stats` shows pushes, loud and open rate by kind); the
installed app's icon badge counts the tray where the platform has badges.

**In the app.** The Alerts tab opens on Latest: every moment of the fan's players since they followed
them (`GET /api/me/activity`), pushed or not, by day, with what's new since they last looked marked and
counted on the Alerts tab. Settings holds the device switch, quiet hours and each player's level (the
hints say minor moments arrive without sound). A followed player's alerts panel has "Mute this game"
while they're live, and the live score says when its alerts are muted; muted games show no toasts.

**Next**
- **Tune from the open rate.** `notify stats` after a few weeks of real use: a kind fans rarely open
  should get quieter, or its priority lower.
- **Personal context from the stored results:** "a first hundred since March", "a career-high 41",
  "10th goal of the season" (`event_player.result` and `form.py` already hold the history).
- **Corrections:** a goal ruled out by VAR lowers the count, but the goal's notification stands and a
  later real goal reuses its key (`goal1`), so it isn't sent. Track ruled-out goals and update the
  game's notification instead.
- **Line-ups an hour ahead:** arm Sportmonks football at 60 minutes instead of 30 (one lane request a
  round covers every match, so it costs little).
- **iPhone and lock-screen live scores.** Web Push reaches iPhone only from a Home Screen install, and
  Live Activities (lock-screen scores) and Android's ongoing live-score notification need a native
  app. If that matters, a React Native (Expo) app reuses the web app's API client and gets an FCM/APNs
  transport beside `notify.push`; the planning and the log stay as they are.
- An evaluation bar on the live chess board: the evaluation is already in the event state.
- Game-winner in the last minute (basketball), match point (tennis), tournament won (chess).
- Accounts, so a fan's levels and mutes follow them across devices.

### 3. Player search (built)

**Built.** Search as you type from our own player registry: about 320,000 athletes imported weekly from
Wikidata, ranked by how many Wikipedias write about them, with typo tolerance. Sport filters, a picker
for athletes who share a name, "did you mean", a preview (next game, last game, stats) before following,
and a desktop dropdown with keyboard navigation. Following binds the exact athlete by id (a footballer's
ESPN id is found by name and confirmed by birth date). College athletes are hidden. "Research with AI"
is offered last and capped at 3 per fan per day.

**Next**
- **FIDE list** for chess ratings and the 1.9M players Wikidata doesn't have, once FIDE gives written
  permission for commercial use; until then chess falls back to Lichess's FIDE search.
- **Licensed roster feed** to keep current teams right between Wikidata edits (transfers, debuts).
- **Photos**: see item 6.
- A **review queue** for registry-to-source matches the birth date can't settle.
- **Namesakes Wikidata lacks**: a full name the registry knows well doesn't ask ESPN, so an obscure
  namesake only ESPN has (the Malaysian-league footballer Nikola Jokić) no longer shows beside the star.
- Browse by team or league (who plays for the Indiana Fever?).

### 4. Android app (steps 1 and 2 under way)

**Today.** The web app is installable (manifest, icons, service worker): Chrome offers "Install app",
it opens full screen, starts offline with the last update, and offers new versions with a Reload
button. `/.well-known/assetlinks.json` is ready for the Play Store wrapper. Notifications for
moments (item 2) work in the app and the Android wrapper. It still only runs on localhost.

**Done when** a fan can install Sports Follow from the Play Store (or straight from the site), open it
full screen from the home screen, and get notifications for their players.

**Suggested path**, cheapest first:
1. **Installable web app (PWA): built.** Its service worker also delivers the notifications (item 2).
2. **Play Store app:** the PWA wrapped as a Trusted Web Activity (`android/`, built with Bubblewrap's
   library); a test APK runs on a phone over USB. Next: hosting (HTTPS + assetlinks for full screen),
   an upload key, a Play developer account, and a closed-testing release.
3. **Native (Kotlin/Compose) only if needed** for what a web app can't do: home-screen widgets with
   live scores, or Android's ongoing live-score notifications.

**Prerequisite.** The app needs a public HTTPS home (hosting for the API, worker, Postgres and Redis),
which nothing has yet; a PWA, Web Push and a Play Store app all need it.

### 5. Live scores at scale

**Today.** Work grows with the number of games on, not with players or fans: one poll job per live
game (every 8–15 s in play) is shared by every followed player in it and every fan, fetches are
cached by URL, and each fan's app holds one stream for all their players. Sportmonks football is
already one lane for all its matches: one request, one job and one thread a round. But every other
source's poll job holds one of the worker's 24 job slots (and a thread) for up to 10 minutes, mostly
sleeping, so one worker process follows about 20 games at once; on a busy matchday the rest wait in
the queue and go stale.
Each open app also holds its own Redis subscription in the API. Today's load: 12 followed players,
5 fans, a live game or two.

**Done when** a matchday with hundreds of live games involving followed players keeps every score on
its normal cadence, requests to sources grow with leagues and rounds rather than games, and the tick
logs poll lag (how late each game's poll is) and requests per minute, so we see trouble coming.

**Suggested path**, most needed first:
1. **A scheduler loop per worker** instead of one sleeping job per game: one async loop keeps each
   live game's next due time and polls with a concurrency cap, so a process handles hundreds of games;
   games are split across worker processes by event id (the per-event lock already prevents doubles).
2. **League scoreboards first (ESPN):** one scoreboard request covers every game in a league (score,
   clock, status); fetch a match's full summary only when its scoreboard entry changed. About 10×
   fewer requests to an API that isn't official and could block us.
3. **Lichess's live stream** for broadcast rounds (`/api/stream/broadcast/round/{id}.pgn`, checked:
   it streams moves as PGN) instead of polling under the one-request-a-second limit.
4. **One shared Redis subscriber per API process**, fanned out in memory, and more API processes
   behind a load balancer. Needed only past tens of thousands of fans online at once.

The chess engine (item 2) is already one budgeted lane for the whole system; more lanes can be added
by lock index if chess traffic outgrows it.

### 6. Player headshots

**Today.** Players show initials. The registry already stores each athlete's Wikimedia Commons photo
(Wikidata P18) as a file name: 65,205 of 323,298 athletes (20%), and 12,332 of the 16,449 well-known
ones (75%, those with 20+ Wikipedia editions). Nothing is displayed yet.

**Done when** a player's page, search results and the Following list show a photo with its credit
where a properly licensed one exists, and initials otherwise.

**Wikidata / Commons (recommended).**
- Fetch each file's licence, author and attribution text from the Commons API (`extmetadata`) and
  keep only free licences (public domain, CC0, CC BY, CC BY-SA). Show the credit ("Photo: Author,
  CC BY-SA 4.0") on or beside the image, with a credits page listing all of them.
- Serve Commons thumbnails (a sized URL) through our own cache, not hot-linked full files.
- Commons licenses the photograph, not the person: no implied endorsement, no use in ads or
  merchandise. Some files carry a "personality rights" warning; skip or treat those carefully.
- Many are action or event photos rather than headshots, and some are years old. Crop to a face
  square and prefer recent files where a player has several.

**AI-generated headshots: not recommended.** A realistic AI image of a real athlete imitates their
likeness without consent. Indian courts have granted personality-rights injunctions against AI
imitations of celebrities (for example Anil Kapoor, Delhi HC 2023; Arijit Singh, Bombay HC 2024), app
stores restrict deceptive depictions of real people, and a wrong-looking face undermines trust. A
safe alternative is a styled avatar that doesn't depict the person: initials in team colours, a
sport icon, a shirt number.

**Paid options later:** licensed agency headshots (Getty Images, AP, Imago, PA) or a data provider's
image feed (Sportradar and Stats Perform sell them) once revenue justifies it.

### 7. Data in a fresh environment

**Today.** A new copy of the app starts empty, and the only fast way to fill it is from the Mac. The
player registry is a 90-minute Wikidata import (`python -m sports_follow.registry`, football most of
it; naming sports imports only those). `deploy/seed-registry.sh` copies it in seconds, but only from
the Mac's running stack to a server. Nobody is followed, so there are no pages until someone searches
and follows; older results then fill in over hours through the history lane. A Claude Code cloud
session is the emptiest case. It has no Docker daemon, its network policy blocks ESPN, Lichess,
Wikidata, Sportmonks and OpenRouter, and it has none of the tokens. On 12 Oct 2026 the app ran there
(Postgres and Redis started by hand from the image's binaries), but search found nobody and no page
could be built.

**Done when** one command fills any fresh copy (a new laptop, the server, a cloud session, CI) with the
registry and a handful of real players, in minutes and without the Mac. Where sources can't be
reached, the app says so plainly rather than looking broken.

**Suggested path**, most useful first:
1. **A registry snapshot anyone can load.** Publish the `athlete` table as a dump (about 15 MB) after
   each weekly import, and add `python -m sports_follow.registry --load <file or URL>` to load it in
   seconds. The weekly import keeps it current from there. `seed-registry.sh` becomes a wrapper around it.
2. **Starter players.** Add `python -m sports_follow.seed`, which follows a fixed list through the
   normal follow path as a marked demo fan: two or three players a sport, India first (Kohli, Bumrah,
   Salah, Jokić, Alcaraz, Gukesh, Carlsen). Pages come from the real sources this build runs, so a
   licensed-only build seeds only football, cricket and chess. Without a gateway, pages are built from
   the sources, with no profile or news.
3. **Cloud sessions.** Add a SessionStart hook (Claude Code's startup hook) that starts Postgres and
   Redis, runs the migrations, installs the Python and web packages, and loads the snapshot. Document
   the hosts to allow under the environment's Network access: `site.api.espn.com`,
   `site.web.api.espn.com`, `lichess.org`, and `query.wikidata.org` for a full import. Tokens stay off
   cloud sessions, so they run ESPN, Lichess and no AI.
4. **Recorded mode** for when no source is reachable (CI, screenshots, a demo offline): replay the
   recorded responses in `tests/fixtures`, labelled "Recorded data" on every page so it's never taken
   for live. Only if steps 1 to 3 leave a real need: the recordings cover a few calls, and stale scores
   that look live are worse than an empty page.

**Open questions**
- Where the snapshot lives: a release on this (private) repo, or the object storage the off-site
  backups will use.
- Wikidata is CC0, but the snapshot also holds ids found in ESPN and Sportmonks. Sportmonks' terms
  allow storing their data for our own app, not passing it on, so keep the snapshot private or strip
  those ids.
- The demo fan must be left out of the launch metrics (retention, follows per user), or seeding skews them.

### 8. Esports (step 1 built; battle royale built, waiting on a Liquipedia key)

**Built (step 1).** `adapters/pandascore_esports.py` follows a pro's
team in PandaScore's team titles (Valorant, CS2, LoL, Dota 2, Mobile Legends, Rainbow Six, Overwatch,
Rocket League, Call of Duty, Wild Rift, King of Glory): upcoming series with stage and format ("Upper
bracket final · Bo3"), results from the team's side ("Won 2–1", "Lost by forfeit"), a year of history
through the history lane, and the team's series and maps won on the Stats tab. Running series are
polled once a minute, all in one request (`poll_lane`), so map results arrive as each map ends; the
app shows a series board with one chip a map. Moments: the series starting and the result (key),
each map won or lost (everything), all told from the player's team's side. The registry imports the
active pros of `SPORTS_FOLLOW_ESPORTS_GAMES` (Valorant, CS2, LoL and Dota 2 by default) from PandaScore
weekly, by handle and full name, so search finds them under an Esports filter: 33,370 "active"
players for those four titles, 334 requests and about 50 minutes, paced. PandaScore's `active` flag
includes long-retired players (most have no team), so a pro on a team ranks first. Credits name
PandaScore. Background reads keep 300 of the 1,000 requests an hour for live series.

Checked against real responses on 13 Oct 2026 (free plan): series scores (`results`), the winner and
each map's winner come with every match, and a running series shows which map is on and who won the
earlier ones, so map moments work without a paid plan. The remaining count is `x-rate-limit-remaining`.
`/matches` rejects `filter[videogame_id]` (it's `filter[videogame]` there). Birthdays are null.
Real ESL Pro League and VCT results read correctly ("Grand Final · Bo5", "Won 3–0"). The tests use
made-up responses in PandaScore's shapes, as for Sportmonks, rather than committing theirs.
An armed series the schedule moves hours later is now disarmed (any sport), so it isn't polled all
that time or closed unplayed after six days. A pro's sport is their game ("Valorant", "CS2", "BGMI",
"Free Fire"), shown wherever other players show their sport; "esports" is the family the rules, the
search filter and the colour share (`sports_follow/games.py`), and pros followed as "Esports" move to
their game on their next refresh.

**To finish step 1**
- Follow a pro whose team plays soon and watch one series through: the start, map and result alerts.
- Add `pandascore_esports` to `SPORTS_FOLLOW_SOURCES` in `deploy/settings.env` once PandaScore
  confirms the free plan allows a commercial app; until then a public build leaves it off.

**Before step 1.** No esports source. A pro gamer typed into "Research with AI" got an AI-built page
with no scores or alerts, as in any sport without a source. Search barely found them: only 2,844 people
on Wikidata have a Liquipedia id (P10918), 3 of them Indian. Every adapter assumed head-to-head events.

**What fans in India watch** (researched 12 Oct 2026). BGMI by far: KRAFTON India reports 930M+ esports
views in 2025, and the BMPS 2026 final peaked at about 729,000 concurrent viewers. Free Fire is coming
back after its ban; Valorant is the biggest PC title but losing viewers. Globally: LoL, CS2, Dota 2,
Valorant.

| Source | What | Cost | Notes |
|---|---|---|---|
| PandaScore | Schedules, results, teams, players for 13+ titles (LoL, CS2, Dota 2, Valorant, Mobile Legends, R6, Rocket League, CoD…) | Free plan, 1,000 requests an hour; player stats from €400 a game a month; live from €1,000 a game a month (LoL and CS only) | Licensed; stats plans are for non-betting use. No BGMI. |
| GRID | Official CS2 and Dota 2 data from the game servers | Free Open Access is historical only, for pre-revenue projects; live is a commercial deal | Most authoritative, two games |
| Liquipedia API | 70+ titles, including PUBG Mobile (where BGMI events are kept) | Reported $49 or $199 per data type per game a month (unconfirmed) | Community-edited, results after the fact, no live; CC BY-SA credit on every page |
| Betting-odds feeds, HLTV/VLR scrapers | Live scores | Varies | Unlicensed or betting-oriented: the ESPN problem again |

Sources: [PandaScore pricing](https://pandascore.co/pricing), [GRID Open Access](https://grid.gg/open-access/),
[Liquipedia API](https://liquipedia.net/api) and [its terms](https://liquipedia.net/api-terms-of-use),
[BMPS 2026 viewership](https://www.tribuneindia.com/news/business/godlike-esports-rises-from-last-place-to-claim-bmps-2026-title-as-jaipur-bmps-finals-reach-729k-peak-viewers-and-12000-attendance/),
[BGMI 930M views](https://respawn.outlookindia.com/gaming/gaming-news/bgmi-esports-crosses-930m-views-amid-indian-esports-market-growth).

**Done when** a fan can find and follow a Valorant or CS2 pro, see their team's upcoming series and
results ("Won 2–1"), and hear when a series ends, from a licensed source named on the Credits page.

**Suggested path**, cheapest first:
1. **Team esports from PandaScore's free plan: built** (above). Player numbers need the €400 stats
   plan; until then results are the team's.
2. **Live scores** once the launch metrics show demand: €1,000 a game a month for LoL or CS (Valorant
   isn't on the live tier), three to six times the launch's whole data budget.
3. **BGMI and Free Fire: built** on Liquipedia (`adapters/liquipedia.py`, `liquipedia_esports.py`),
   not yet run: it needs a LiquipediaDB key (requested at liquipedia.net/api, saved as
   `~/.config/sports-follow/liquipedia-token`). Battle royale is its own event shape: one lobby (a
   Liquipedia match2: a day of a group, 12 to 24 teams playing several games) is one event, with the
   standings (points, placement, finishes, games won) and each game's winner. Liquipedia stores each
   team's placement and finishes in every game (Lua-Modules: `MatchGroup/Input/Util.lua`), which is
   what the parsers read. The app shows the top of the standings with the player's team marked, one
   chip a game, and results like "3rd of 16 · 87 pts". Alerts, from the player's team's side: the
   lobby starting and a chicken dinner (Booyah in Free Fire) and going top (key); each game's
   placement (everything); where the team finished (key). The line-ups name the four who played, so
   a benched player isn't told. The registry imports the active players of each battle royale wiki
   (`SPORTS_FOLLOW_LIQUIPEDIA_WIKIS`), keyed "lp:<wiki>:<page id>"; PandaScore's and Liquipedia's
   players never move between the two (each covers its own games), and an esports name alone never
   binds a player: their team must match.
   - **The free key's limits:** 60 requests an hour, counted in Redis across the API and the worker,
     never spent past 56; background reads stop at 26. Lobbies are polled every 5 minutes, all of them
     in one request; fixtures every 30 minutes a team (cached). That suits a few teams; more need a
     paid plan.
   - **To check once the key works:** the opponent condition, which asks for each spelling of a team
     page as Liquipedia's MatchTicker does (`([[opponent::Team SouL]] OR [[opponent::Team_SouL]] OR …)`):
     that the API takes the brackets and which spelling the rows hold; how `dateexact` comes (a lobby
     without a time waits for its first result); the date format, whether `match2games[].opponents`
     comes parsed or as a string, and the section and bracket header used for the lobby's name
     ("Semi Finals · Day 3").
   - **For a public build:** a commercial Liquipedia plan (reported $49 or $199 a data type a month;
     their portal says sign-ups are paused), then add `liquipedia_esports` to `SPORTS_FOLLOW_SOURCES`.
     Data is CC BY-SA 3.0: credited on the Credits page and on every score as "Liquipedia".
   - **PUBG PC: built on PUBG's own API** (`adapters/pubg.py`, `pubg_esports.py`; key in
     `~/.config/sports-follow/pubg-token`). A lobby is a session of a tournament's matches played by
     the same teams (a tournament day, or a group of it); standings use the standard PUBG esports
     points (10, 6, 5, 4, 3, 2, 1, 1 and one a finish). Every player has their own line (finishes,
     damage, assists, knocks). Teams are the tags in player names ("TWIS_Lu"); tournaments have no
     names in the API, so they're named from known codes (PWS, PAS, PEC…) or the region. There's no
     schedule: a lobby appears when its first match ends, then is read every 2 minutes. The worker
     indexes recent tournaments every 10 minutes (Redis, shared; matches downloaded once; tournament
     reads at most 9 a minute) and loads their players into search every 6 hours. The tournament
     shard also holds exhibition matches (3 to 20 minutes, players on 40+ finishes): only matches of
     22+ minutes, 8+ teams and at most 25 finishes for anyone count. A tournament listed under two ids
     (`kr-26pws2f`, `kr_26pws2f`) counts once. Checked on 13 Oct 2026: 28 lobbies, 630 players.
     PUBG's terms allow commercial apps (ads, subscriptions, no paywalled data) with the trademark
     notice, on the Credits page.
   - **CODM next:** Call of Duty: Mobile is head-to-head (5v5 series), so it fits the team model on
     Liquipedia's Call of Duty wiki. PandaScore's Call of Duty is the PC and console league (CDL), not
     CODM. Neither Activision nor Garena has a public API.

**Global as well as India** (decided 13 Oct 2026). The registry imports every PandaScore team title
(Mobile Legends, King of Glory, Rainbow Six, Overwatch, Rocket League, Call of Duty and Wild Rift beside
Valorant, CS2, LoL and Dota 2), and the Liquipedia adapter reads PUBG and Apex Legends beside PUBG Mobile
and Free Fire (their wikis store lobbies the same way). 2025 hours watched, worldwide: LoL, CS2, Mobile
Legends, Valorant, Dota 2 (Esports Charts). Activity on PandaScore's free plan, 13 Oct 2026, series in
the last 30 days: CS2 1,567, LoL 309, Dota 2 291, R6 206, Mobile Legends 179, King of Glory 141,
Valorant 76 (off-season), Overwatch 70, Rocket League 47; Call of Duty (between CDL seasons), Wild Rift,
EA FC and PUBG none.

**Live scores, researched 13 Oct 2026.** PandaScore's free plan is live only by the map (which map is on,
each map's winner), and small events can lag an hour; in-game live is €1,000 a game a month (LoL, CS).
Elsewhere:
- **Dota 2:** OpenDota's `/live` gives pro games live with every player's numbers, free (60 a minute,
  3,000 a day, no key); community-run, no data licence. PandaScore would still give the fixtures.
- **LoL:** Riot's own site backend (esports-api.lolesports.com, feed.lolesports.com/livestats) gives full
  live stats, but it's undocumented and Riot licenses official live data only to GRID: development only.
- **Valorant:** the valorantesports.com backend has results, no live; nothing official.
- **CS2:** GRID only (commercial, priced per deal); its free Open Access programme looks discontinued.
- **PUBG PC:** PUBG's own API (a key is saved as `~/.config/sports-follow/pubg-token`): 1,200 official
  tournaments across 14 regions, every player's numbers after each match (not during), 10 requests a
  minute, matches kept 14 days. No BGMI or other mobile events.
- **Fighting games (Tekken, Street Fighter 6):** start.gg's official API, free (80 requests a minute),
  player brackets reported as they're played, commercial use allowed with attribution and no
  gambling or resale; hosts Indian events (TEKKEN Nexus 2025, New Delhi). A 1v1 shape, like tennis.
- **Dead ends:** Supercell's APIs (no pro data; no fees allowed), octane.gg (shut down), EA FC,
  Fortnite and Apex (no esports API; Liquipedia only).

**Open questions**
- Which games first: Valorant and CS2 have the cleanest data, BGMI has the audience. (Both done:
  team titles from PandaScore, battle royale from Liquipedia, picked 13 Oct 2026.)
- PandaScore's free plan: confirm in writing that a commercial, non-betting app may use it. (Its
  results do include series scores and map winners, for every team title: checked 13 Oct 2026.)
- Liquipedia's commercial plans: free access is only for "educational, non-commercial public-interest
  and community feature projects" (liquipedia.net/api); the paid plans' price per data type, and
  whether a battle royale app needs one data type (matches) or several (matches, players).

## Found while building (not yet scheduled)

- **Licensed basketball and tennis.** Football and cricket now come from Sportmonks and chess from
  Lichess; basketball and tennis still use ESPN's unofficial endpoints, so a public build shows them
  as AI pages without live scores until a licensed feed (BALLDONTLIE, API-Tennis) is added.
- **Tests for `structured.identify`** against recorded search responses (checked by hand today:
  full names, surnames, typos, namesakes, college records, legal names).
- **Graceful worker restarts.** Killing the worker mid-build leaves the card "building" until its
  lock expires (15 min); a shutdown hook should release locks and requeue.
- **Tennis doubles** (singles only today) and **chess events Lichess doesn't relay**.
- **Team following** and **compare players** (long-term goals from the original brief).
- **AI budget.** The gateway allows sports-follow $5/day (about 40 new players' first pages);
  decide the budget for real traffic.
