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
- [ ] Live polling at scale: one request per live match every 15 s is 240 an hour; past about six
  matches at once, poll `livescores/inplay` (or `fixtures/multi`) once for all of them.
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
- [ ] Hosting (HTTPS) and a public build; FIDE's written permission for the rating list.
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
once (`sports_follow/moments.py`); `sports_follow/notify.py` sends them by Web Push to every device of
every follower whose level wants them, and the app shows them as toasts when it's open. Verified end
to end on an Android emulator and a phone (Chrome, through FCM), including tap-through to the player.

| Sport | Moments |
|---|---|
| All | Game starts with the player in it; final result, unless the line-ups show they weren't in the squad |
| Football | Goal, assist, red card (key); yellow card, comes on (everything) |
| Cricket | 50 / 100 / 150 / 200, 3+ wickets, out with the score (key) |
| Basketball | 30 / 40 / 50 / 60 points, triple-double (key); 20 points, double-double (everything) |
| Tennis | Upset of a higher seed (key); each set won or lost (everything) |
| Chess | Winning, in trouble, a turnaround (key); better, worse, level again (everything); from Stockfish |
| News | Injury, transfer, retirement (key); milestone (everything), from a rebuild's news |

Per player: key moments (default), everything, results only, off. Per device: quiet hours (silent,
not dropped). A first look at a game already under way is a baseline, so nobody gets "reaches 50" an
hour late. Live moments expire after 15 minutes if the phone is offline; results and news after 12 hours.

**Next**
- **iPhone and lock-screen live scores.** Web Push reaches iPhone only from a Home Screen install, and
  Live Activities (lock-screen scores) need a native app. If iPhone matters at launch, a React Native
  (Expo) app reuses the web app's TypeScript and sends through the same `notify.deliver` step (FCM/APNs
  beside Web Push).
- An evaluation bar on the live chess board: the evaluation is already in the event state.
- Game-winner in the last minute (basketball), match point (tennis), tournament won (chess).
- A digest instead of a burst when several moments land in one poll.
- Accounts, so a fan's levels follow them across devices.

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
cached by URL, and each fan's app holds one stream for all their players. But each poll job holds
one of the worker's 24 job slots (and a thread) for up to 10 minutes, mostly sleeping, so one worker
process follows about 20 games at once; on a busy matchday the rest wait in the queue and go stale.
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
