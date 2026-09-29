# Backlog

What's next for Sports Follow, most wanted first. Each item says what exists today, what "done"
looks like, and the open questions.

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
| Football, basketball | This season and last (team schedules for club and country) |
| Tennis | A year of weekly draws |
| Cricket | Six months of daily feeds |
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
- **Photos**: Commons file names are imported; fetch each one's licence and author, show with credit,
  initials as the fallback.
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

## Found while building (not yet scheduled)

- **Licensed data before a public launch.** ESPN's JSON endpoints are unofficial and could change
  or be blocked; the adapter interface takes a licensed feed as another adapter. Lichess is an
  official API, but rate-limited (reads are one a second).
- **Tests for `structured.identify`** against recorded search responses (checked by hand today:
  full names, surnames, typos, namesakes, college records, legal names).
- **Graceful worker restarts.** Killing the worker mid-build leaves the card "building" until its
  lock expires (15 min); a shutdown hook should release locks and requeue.
- **Tennis doubles** (singles only today) and **chess events Lichess doesn't relay**.
- **Team following** and **compare players** (long-term goals from the original brief).
- **AI budget.** The gateway allows sports-follow $5/day (about 40 new players' first pages);
  decide the budget for real traffic.
