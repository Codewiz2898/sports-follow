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

### 2. Push notifications for interesting moments

**Today.** Nothing is pushed. Live changes reach an open page over SSE within about 10 s, and
`follow.alert_rules` exists in the schema but is unused.

**Done when** a fan gets a notification, even with the app closed, for moments like:

| Sport | Moments |
|---|---|
| All | Game about to start (player in the lineup/squad), final result |
| Football | Player scores or assists, red card, comes on |
| Cricket | Player reaches 50 / 100, takes 3+ wickets, is out (with score) |
| Basketball | 20/30/40 points, double-double, triple-double, game-winner in the last minute |
| Tennis | Wins/loses a set, match point won, upset (beats a higher seed) |
| Chess | Game starts, clear advantage (engine eval past about ±2), win/draw/loss, tournament won |
| News | Transfer, injury, retirement (from the research agent's news) |

**Design sketch.** Detection runs in `structured.poll_once`: compare the previous and new snapshot
(score, the player's line, moments) and emit typed moments with a stable id, so each is sent once.
A moment is stored, then fanned out to every follower whose rules want it. Delivery by Web Push
(VAPID keys, a service worker, `pywebpush`); SSE shows the same moment as an in-app toast. Per
player, fans pick "everything / key moments / results only / off", plus quiet hours.

**Open questions**
- Channel: Web Push works on desktop and Android browsers, and on iPhone only once the app is added
  to the Home Screen (iOS 16.4+). Is that enough, or is email/Telegram/WhatsApp wanted too?
- Chess advantage needs an evaluation: Lichess's cloud eval (`/api/cloud-eval`) covers popular
  positions for free; anything else needs a Stockfish process in the worker.
- Fans are anonymous (a cookie) today, so a subscription belongs to one browser until accounts
  exist.

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

### 4. Android app

**Today.** The web app works in Android's browser (the layout is built for phones, with a bottom
nav), but it isn't installable, can't notify, and only runs on localhost.

**Done when** a fan can install Sports Follow from the Play Store (or straight from the site), open it
full screen from the home screen, and get notifications for their players.

**Suggested path**, cheapest first:
1. **Installable web app (PWA):** a manifest, icons and a service worker. Android offers "Install
   app", and the same service worker is what Web Push (item 2) needs, so the two go together.
2. **Play Store listing:** wrap the PWA as a Trusted Web Activity (Bubblewrap); one codebase, updates
   ship with the website.
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
