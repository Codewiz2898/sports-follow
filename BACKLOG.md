# Backlog

What's next for Sports Follow, most wanted first. Each item says what exists today, what "done"
looks like, and the open questions.

## Requested

### 1. Previous results: a player's full history

**Today.** The Results tab shows the player's last 5 finished games from their structured source
(ESPN or Lichess), with the result from their side and their own line ("139* (88)", "38 PTS · 16
REB", "Won the match 6–2"). Sports without an adapter show the agent's list.

**Done when**
- The Results tab lists the whole current season (and the last one) with "load more", newest first,
  each row opening the game: final score, the player's full line, and a link to the source.
- The Following page has a "Latest results" section across every followed player.
- Stats can be filtered by the results shown (last 5 / last 10 / season).

**Notes.** The data is mostly already fetched: football and basketball team schedules carry the
whole season; tennis and chess need the look-back widened (6 weeks of draws, 60 days of
broadcasts today); cricket reads 30 days of day feeds and would need more (cached for good once a
day is over). Finished games are already stored as events with a final state and player line, so
this is a paged read over `event` + `player_line`, not new scraping.

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
| Chess | Game starts, result |
| News | Injury, transfer, retirement (key); milestone (everything), from a rebuild's news |

Per player: key moments (default), everything, results only, off. Per device: quiet hours (silent,
not dropped). A first look at a game already under way is a baseline, so nobody gets "reaches 50" an
hour late. Live moments expire after 15 minutes if the phone is offline; results and news after 12 hours.

**Next**
- **iPhone and lock-screen live scores.** Web Push reaches iPhone only from a Home Screen install, and
  Live Activities (lock-screen scores) need a native app. If iPhone matters at launch, a React Native
  (Expo) app reuses the web app's TypeScript and sends through the same `notify.deliver` step (FCM/APNs
  beside Web Push).
- **Chess advantage** (engine eval past about ±2): Lichess's cloud eval covers popular positions; the
  rest needs Stockfish in the worker.
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
