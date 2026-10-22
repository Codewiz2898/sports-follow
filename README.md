# Sports Follow

Follow any athlete in any sport. Each followed player gets one shared page: live score and the
player's own numbers while they play, upcoming games, news, recent results and season stats. The
Today screen puts all of your players together: on a phone a row of them along the top (the live
score, or when they next play), then their live games, a link to the other games on anywhere (the Live
page: those games by sport, outlined, with players to follow from each), a row of every result since
yesterday and a row of every game in the next two weeks, each scrolling sideways and ending in the big
games of the competitions the sources cover that none of your players are in, outlined too, the sports
taking turns so one sport's busy week doesn't fill the row, and the big games coming up soonest first (a
fan who follows nobody sees only those, "Big results", "Big games coming up", and "Live now" to pick
players from). The Following page has a tile for each player in their sport's colour. On a desktop the page runs
the width of the window; from 1400px a column on its right, beside every page, lists the scores on now and the games
starting soon (`web/src/components/Side.tsx`), and a match page's "Call it" stands at its top, beside the game.

## How it's built

This is milestones 1 and 2 of the [backend design](docs/backend-design.html): the persistent core,
plus structured adapters for cricket and football.

```
React app (web/) ──/api──▶ FastAPI (sports_follow/server.py) ──▶ Postgres (registry, cards, history)
      ▲                         │  enqueue jobs                   Redis (hot cards, per-player channels)
      └──── SSE: card updates ──┘         ▼
                              arq worker (sports_follow/worker.py)
                                ├─ build_card: Wikidata + Wikipedia → profile; research agent only for a page neither they nor a source covers
                                ├─ reread_profile, monthly per player: Wikidata + Wikipedia → profile (wiki.py)
                                ├─ refresh_structured: adapter → fixtures, results, stats → card
                                ├─ poll_event: one job per live game, shared by every player in it
                                ├─ poll_lane: every live Sportmonks football or cricket match, one request a round
                                ├─ refresh_live: agent live check, for sports with no adapter
                                ├─ import_registry, weekly: Wikidata ──▶ athlete table (the player registry)
                                ├─ prune, daily: finished games' timelines, old moments and pushes, idle hot cards
                                ├─ fetch_photos, every 15 min: followed and starter players' photos from Wikimedia Commons, for their profiles
                                ├─ read_sidelined, twice a day: who's out and who's back, league-wide, for the Wire (sidelined.py)
                                ├─ refresh_campaigns, daily: each campaign page's teams' rosters from their sources (campaigns.py)
                                └─ tick, every minute, beside the jobs (no slot): arm events, queue polls, refreshes and rebuilds
                                         │                                  │
                              llm-providers gateway ──▶ LLM         adapters/ ──▶ ESPN JSON (football, cricket, basketball, tennis)
                                                                              ├─▶ Lichess API (chess)
                                                                              ├─▶ PandaScore API (esports: team titles)
                                                                              ├─▶ LiquipediaDB API (esports: battle royale)
                                                                              └─▶ PUBG API (esports: PUBG)

                              arq curator (sports_follow/curator.py), a queue of its own
                                ├─ poll_channels, every 30 min: each sport's trusted channels, and the searches due for
                                │    followed players' big moments (searches.py, capped a day) ──▶ feed_candidates,
                                │    then the judge reads what's new and rank.py scores it (no source, the model only)
                                ├─ sweep, daily: a search for each followed player with nothing to watch this week,
                                │    each player's six months weekly, a few players' most watched ever (sweeps.py, capped) ──▶ feed_candidates
                                └─ check_videos, daily: stored videos not read for a week, dropped once gone
                                         │
                                       adapters/ ──▶ YouTube feeds and Data API v3 (the feed's videos)
```

- **Work is keyed by player, never by fan.** Following adds one row. The first fan to follow a player
  triggers the build; every later fan gets the shared card at once.
- **A player registry from Wikidata.** `sports_follow/registry.py` imports every living athlete Wikidata
  (public domain) knows in the five sports into the `athlete` table each week (Monday 03:30 UTC, by the
  worker): names and aliases, birth date, country, current teams, chess titles, Commons photo file, the
  number of Wikipedias that write about them (the popularity signal), and their ids elsewhere:
  ESPNcricinfo, FIDE, ESPN for NBA and tennis players and some footballers, Transfermarkt, Soccerdonna,
  FBref, NBA/WNBA, ATP/WTA. College players aren't in it. On 29 Sep 2026: football ~260,000, chess
  24,247, cricket 19,745, tennis 10,781, basketball 9,612. First import:
  `.venv/bin/python -m sports_follow.registry` (about 90 minutes, one query at a time as the Wikidata
  query service asks; football is most of it); `--new` reads only people not imported yet. Esports
  pros come from their sources instead (Wikidata knows only a few thousand): the active players of
  `SPORTS_FOLLOW_ESPORTS_GAMES` from PandaScore and of `SPORTS_FOLLOW_LIQUIPEDIA_WIKIS` from
  Liquipedia, by handle and real name, with each source's key, each under their game's sport ("cs2",
  "bgmi": an Indian player on the PUBG Mobile wiki plays BGMI), each source read once for all its games
  that run (`python -m sports_follow.registry esports` for every game, or `… registry valorant cs2`; part of
  the weekly run). After each import,
  `sports_follow/roster_wikidata.py` finds each pro's Wikidata person among the few thousand Wikidata
  counts as esports people (a Liquipedia, HLTV or Esports Earnings id, or professional gamer), for their
  photo: by the source's id where Wikidata keeps it (Liquipedia's pages), by handle together with the
  real name or birth date and the game or a team, or by real name and birth date; never by a handle alone,
  and never where two people fit one pro or one person two pros. It records the Wikidata id in the pro's
  `ids` and the photo file in `image`, which the weekly roster leaves alone. On 16 Oct 2026, 542 of
  41,870 pros matched, 238 of them with a photo (TenZ, ZywOo, Faker, s1mple…).
  `python -m sports_follow.roster_wikidata --dry-run --show 20` shows what it would match, and
  `players unmatch` takes a wrong one back (below). A run by hand
  takes the weekly run's lock, so it refuses to start while that one is going; a sport (or esports
  source) that fails keeps last week's rows and the rest still run.
- **Player photos from Wikimedia Commons, on the profile only.** A player's page, and the preview of an
  athlete before following, show their Commons photo; the home page, lists and search don't (the home page
  is getting each sport's own line art instead). `sports_follow/photos.py`: every 15 minutes the worker asks
  the Commons API about the registry's photo file of each followed and starter athlete it hasn't got, 50 to
  a request, and keeps those under a licence fans may see them under (public domain, CC0, CC BY, CC BY-SA,
  GODL-India; not GFDL, Commons' generic "Attribution" or "copyrighted free use"). Their 960-pixel
  thumbnails (about 290 KB; the face's square shows about pixel for pixel on a phone), author, licence and
  file page go in the `photo` table, and the app gets them from our own origin (`/api/photos/<digest>.jpg`,
  cached a year, never hot-linked) with the card and the previews. Each file is asked about again every 90
  days (a new upload, licence or author, or a file Commons deleted, which then stops showing), one that
  failed the next day, and one kept at another width at once (`photos.WIDTH` changed: they're fetched
  again, 100 a run, and show at their old size meanwhile); a file fetched over 30 days ago that nobody
  follows or is offered now is forgotten. A photo goes into followed players' cards, and leaves any card
  that shows it once its athlete no longer has it, followed or not (anyone may open a page). The app shows
  each photo in its own colours, cropped around the face and fading into the band, and changes nothing
  else; its credit says the crop, as CC BY and CC BY-SA ask: "Photo: Bryan Berlin, CC BY-SA 4.0, Wikimedia
  Commons, cropped", under the photo, linking to the file's page. The Credits page lists them all and says
  how they're cropped and faded. Players without a photo show none. Never a
  data provider's photos (their terms don't license them), and never generated likenesses.
  The job also finds the face in each new thumbnail, once (`sports_follow/faces.py`: OpenCV's YuNet
  detector, a 230 KB MIT model in `sports_follow/data/`, a few milliseconds a photo, loaded by the worker
  only), and keeps its box on the photo row. The app gets the head-and-shoulders square around it
  (`crop`) with the photo, and the profile shows that square; a photo with no face found is shown from
  near its top.
  `python -m sports_follow.photos` runs the job once.
- **Profiles from Wikidata and Wikipedia, no model.** A player with a Wikidata person (their registry
  athlete; an esports pro's, once roster_wikidata.py has matched them) gets the facts on their page from
  Wikidata (`sports_follow/wiki.py`): date of birth, the country they compete for, current teams (one club
  and one national side, as the registry lists them), their role (`Sport.profile_role`: position played;
  chess's is the title) and the awards their sport names (`Sport.honours`: "Ballon d'Or ×8", counted by
  year). The About paragraph is the summary of their English Wikipedia article (the REST summary
  endpoint), shown with a link to the article and "CC BY-SA 4.0", as the licence asks; the Credits page
  names Wikipedia. Three requests a player (the person with only the statements read, the names of the
  items it names, the summary), one at a time, kept in the source cache under `wikimedia`; a page's first
  build reads them and the scheduler reads them again a month later (`reread_profile`, 5 a minute at most;
  an hour's wait when Wikimedia doesn't answer). A live source's facts win over Wikidata's (a bound
  player's name and teams are their source's), and Wikidata's and Wikipedia's over the research agent's
  (`profile_from` on the card says which gave what). The research agent writes only the background of a
  player with a live source and no English article (`pipeline.needs_agent`), at most once a month
  (`BACKGROUND_EVERY`), when a fan opens the page and the player is followed and their sport runs; never
  on a clock. A player with an article and a live source never sends it. A player no live source has gets no page at all
  unless `SPORTS_FOLLOW_RESEARCH=1`, when the agent writes their whole page as before (games and news too,
  again after `SPORTS_FOLLOW_CARD_MAX_AGE`); so pages build and rebuild without the gateway for everything
  this covers. For a player with a live source the agent is asked for the background alone
  (`follow_player(background_only=True)`), and nothing else it says is kept: no news items and no news
  alerts, whose news is the publishers' (`news.py`). `python -m sports_follow.wiki Q213854 cricket` prints one player's profile.
- **Search answers from the registry.** `GET /api/search` (`sports_follow/search.py`) reads Postgres only:
  players on Sports Follow (those more signed-in fans follow first, and "N following" counts them, an inbox's
  plus-address accounts as one: a cookie's follow only breaks a tie, so a script's can't steer it; teams the same), then registry athletes by popularity, with a trigram index for typos ("did you
  mean"). ESPN's search and Lichess's FIDE search are asked only when the registry has fewer than three
  matches. Two athletes with the typed name get a picker with birth years.
- **Following binds the exact athlete.** A registry athlete is looked up in their live source by the id
  Wikidata has, or, for most footballers (Wikidata has their Transfermarkt id, rarely ESPN's), by name
  accepted only if ESPN's birth date matches; the id found is written back, so it's by id from then on.
  The page is live in about a second and its profile follows from Wikidata and Wikipedia. An athlete no
  live source has can't be followed, unless `SPORTS_FOLLOW_RESEARCH=1`: then the agent builds their page
  (counted as an AI research).
  `GET /api/athletes/{system}/{id}` and `/api/athletes/wikidata/{qid}` preview before following (a preview is
  kept 10 minutes, by the athlete, whatever `league` the link names). A fan follows at most 100 players
  (`server.PLAYERS_MAX`). Previews and follows that ask a source for an athlete not on the app yet (a whole
  team's follow on a campaign page asks once for each such player) are limited to 30 a minute a fan and 120 a
  client address (`SPORTS_FOLLOW_SOURCE_LOOKUPS_PER_MINUTE`, `SPORTS_FOLLOW_SOURCE_LOOKUPS_PER_ADDRESS`), past
  which they're a 429; a player already here costs nothing. Follows of every kind (a player, a team, a game; a
  campaign page's whole team in one tap is one) are limited to 150 an hour a fan, and 300 an IPv6 /64, 1,200 its
  /48 and 1,200 an IPv4 address (a phone network's may be shared by thousands of fans) (`server.FOLLOWS_PER_FAN`,
  `FOLLOWS_PER_ADDRESS`, `FOLLOWS_PER_IPV6_48`, `FOLLOWS_PER_IPV4`), past which they're a 429; one refused isn't counted.
- **AI research is capped** (only with `SPORTS_FOLLOW_RESEARCH=1`). A name no source knows goes to the research agent, 3 per fan per UTC day
  (`search.RESEARCH_PER_DAY`, counted in Redis), 10 per client address per day and 10 an hour across all
  fans (`SPORTS_FOLLOW_RESEARCH_PER_ADDRESS`, `SPORTS_FOLLOW_RESEARCH_PER_HOUR`). Following a player
  already here, or a search result with a live source, doesn't count. The gateway's per-app budget is
  the hard ceiling behind it. The backgrounds the agent writes for players a live source has and Wikipedia
  doesn't are capped too, whoever made the player: 6 an hour across all players
  (`SPORTS_FOLLOW_BACKGROUNDS_PER_HOUR`); past it a page is whole from its source without one, and a fan
  opening it in a later hour sends the agent.
- **Names converge.** "Kohli" and "Virat Kohli" end up on the same player: every typed name becomes an
  alias, and when a build discovers the athlete already exists, the rows are merged. A player bound to a
  source is never renamed or merged by an agent report, and keeps the source's sections when a typed
  name is merged into them; a player picked in search is never merged into another by name. So a
  namesake's report can't take them over.
- **Structured sources where they exist, the agent everywhere else.** After the agent says who a
  player is, `structured.bind` looks them up in their sport's adapter (`sports_follow/adapters/`) and
  records the identity and bindings. From then on the source owns the fixtures, results, stats and live
  score, the publishers their news (`news.py`), and the agent only writes a background Wikipedia has no
  article for. A player followed before their sport had an adapter is
  bound by the next scheduled refresh. A bound player is read again from their source every six hours in a
  refresh (`structured.reread`), so one who changes teams moves to the new team's games, and their old team's
  still to come are no longer theirs; a fan picking them again in search moves them at once.

  | Sport | Source | Fixtures | Live | Stats |
  |---|---|---|---|---|
  | Football | ESPN | club + national team schedules | score, scorers, cards, player line | season totals |
  | Cricket | ESPN (ESPNcricinfo ids) | day-by-day calendar, filtered by squad | innings, player's batting/bowling | recent form from scorecards |
  | Basketball | ESPN (NBA, WNBA; NCAA if researched) | team schedule, every phase | score, quarters, box-score line | season averages |
  | Tennis | ESPN (ATP, WTA singles) | draws, current + 6 weeks back | set scores, serve | ranking, points, win–loss |
  | Chess | Lichess broadcasts (FIDE ids) | the player's broadcast rounds | board, clocks, match score | FIDE ratings, recent score |
  | Esports | PandaScore (team titles: Valorant, CS2, LoL, Dota 2…) | the team's series | series score, map by map (no in-game data); Dota 2 also inside the map, from OpenDota, and on its match page the map being played's gold line so far, towers and barracks and ten heroes, and each finished map's gold by minute, how it went, every player's items and the draft | team's series and maps won; a Dota player's kills, deaths, assists, GPM and heroes |
  | Esports, PUBG | PUBG's official API (PC tournaments) | the player's lobbies, found as each match ends (no schedule) | standings and every player's finishes, damage, assists and knocks, after each match | own and team form over 14 days |
  | Esports, battle royale | Liquipedia (BGMI and PUBG Mobile, Free Fire, Apex Legends) | the team's lobbies | standings and each game's winner, every 5 min (as editors record them) | placings, games won, finishes |

  Every other sport still gets everything from the agent.
- **Live polling is armed by the schedule.** Fixtures are stored as events keyed by the source's id.
  30 minutes before one starts it is armed, or earlier for a sport whose team sheets come out sooner
  (`Sport.team_sheet_before`: football's 75 minutes), so "starts" and "on the bench" are told when the
  line-ups are published; one `poll_event` job then polls it (every minute before
  kick-off, every 8–15 s in play), appends event states and player lines, and pushes every followed
  player's card. The job hands over every 10 minutes; the tick restarts it while the game is on.
  Sportmonks football matches share one `poll_lane` job instead: each round fetches every armed and
  live match in one request (`fixtures/multi`, 50 matches a request), so a busy matchday costs 240
  requests an hour, not 240 per match, and one worker thread, not one per match. Sportmonks cricket
  matches have a lane of their own: one `livescores` request a round brings every match in play (360
  an hour, however many are on), and a match it leaves out (before the toss, just finished) is read on
  its own at `poll_event`'s pace: at once when livescores drops it, every round while it's in play and
  changing, otherwise once a minute.
- **One stream per fan.** The app holds one SSE connection for all of a fan's players; a player's page
  listens on it too, and opens that player's own stream only for someone the fan doesn't follow. An API process
  holds so many streams at once (`server.STREAMS_PER_PROCESS`): so many a fan's own (`STREAMS_PER_FAN`; a player's
  stream isn't counted there), a client address, and an IPv6 /48's /64s together (an IPv4 /24 is only logged at that
  ceiling: a phone network's NAT puts many fans behind a few); a player's stream with neither a fan's cookie nor a
  session takes one of the few kept for those (`STREAMS_WITHOUT_FAN`), so the rest stay fans'.
- **A page for every game.** Every game on Today opens its match page, `/match/<source>/<game id>`
  (`sports_follow/match.py`, `GET /api/match/...`), live, over or to come: the fan's live boards, results
  and games to come, the big results and big games coming up, and Live now's games; so does a game on a
  player's Matches tab ("The match"). Its board large in a block of its sport's colour, and over a finished
  game's board who won, big, in its sport's words (`Sport.verdict`); a game to come has its countdown
  instead. Then what its sport shows under it (a football timeline, a cricket scorecard, an esports series by
  map or a lobby's standings; a chess game's moves sit beside its board instead), the fan's players in it with their lines and
  results, the moments found for the players followed in it, and both sides' players to follow with a tap.
  A game's address is its source and the id a fixture of it has there, as its event's key holds them, so
  it's the same whether or not anyone follows a player in it, and once it's over; the server gives every
  game it lists its address (`match`) where it has a page, and the app never makes one up. Where a fan
  follows someone in it, its board is their live card's, as fresh as the polls. Any other game's board, and
  every game's line-ups and players' lines, come from a read of its source (an adapter's `match`) by the
  worker: at most once a minute a game (a game still hours off, or called off, every half hour) and only
  while someone has its page open, for a game a source has named (on Live now, among the big results and
  games coming up, or a followed player's, their results included), at most six a minute of a source's
  allowance. A read that finds the game over is read once more half an hour on, when the source has filled
  in what it hadn't at the whistle (and every half hour while the source says it's partial: a chess round
  still on, moves or a Dota map OpenDota hasn't parsed yet); that one is kept a week and not asked for again
  till then, so a crowd opening yesterday's final costs its source two reads. One that finds it not there
  is the last for hours, unless it was made before the game's start or in the 15 minutes after, and that
  start has passed (a chess round's boards aren't on its broadcast till its games begin: the game is then
  read as any is), or, while the game is on, for half an hour, as a source can list a game late. A read can say more than a poll: a Dota 2 map's gold by minute, moments, players
  and draft, and the live map's gold line, buildings and heroes, come only with the page's own read (each poll's state is stored and sent to every card, so it
  stays small), as the state's `page_detail`, and show under whichever board is newest. The page shows Live now's copy
  until the first read comes, and looks again while it's in view. A sport turned off has no match pages.
- **Watch live** (`sports_follow/watch.py`, the match page's Watch block). A source that lists a game's
  broadcasts puts them on its fixtures (`Fixture.streams`: each one's page, its language, whether the
  organiser runs it and whether it's their main one; PandaScore's `streams_list` for esports series), and
  the game's read keeps them. The page's `streams` name each one's platform from its address and come in
  order: the organiser's main broadcasts, then those in English, then the rest. While the game is on, or
  within half an hour of its start, the web app plays a Twitch or Kick channel inside the page once it's
  tapped (`player.twitch.tv`, told the page's host; `player.kick.com`), as it does a YouTube video
  (youtube-nocookie.com), with "Open on Twitch" beside it; any other platform is a "Watch on Douyu" link.
  The player is the column's width, 16:9, a phone's too, and its own full-screen button makes it bigger. On a
  phone that's smaller than Twitch's stated minimum for an embedded player (400×300): Bharath chose on 21 Oct
  to have it play in the page anyway (BHA-387). A phone never autoplays it, so there the fan taps the player's
  own play too. A player a fan has started stays while the page is open, though the game is called over before
  the broadcast, which runs behind, has shown the end. The Android app plays the same three in the page too
  (`mobile/src/watch.tsx`), at the content's width, with "Open on Twitch" and Expand under it: Expand turns it
  sideways over the whole screen, and Collapse (or Back) puts it back in the page (BHA-358: nothing turns on its
  own); the rest are links. An app isn't a site Twitch can be told, so it shows the
  site's own page of the player alone, `/watch/<player>/<id>` (`watch.page`; a player the app doesn't have, or an
  id of another shape than its player plays, is a 404), in a WebView that keeps to that page: a link out of the
  player opens in the platform's app or the browser. That page brings its own Content-Security-Policy, stricter
  than the site's (no script of its own, its one style by its hash, only its player's frame), which the Caddyfile
  leaves it. A platform is one entry in `watch.PLATFORMS`, with its player (what it plays, its address) as a
  `watch.Player`; the web app makes the same address itself (`components/Watch.tsx`'s `playerUrl`), and the
  Caddyfile's `frame-src` names each player's host, which `tests/test_watch.py` keeps in step.
- **Following a game by itself** (`sports_follow/matches.py`, the match page's Follow): its key moments
  from both sides reach the fan until it ends, whoever they follow in it. A follow is a row
  (`match_follow`: the fan, the game's event, their level, and when it lapsed); following a game nobody
  follows a player in makes its event, polled by its source's `locator` (an adapter's, below). From then on
  the poller treats it as a followed player's game: armed by its sport's lead, polled at its source's cadence
  in the live lane, through the same lanes and allowances, and left alone once it's over. Each poll finds
  the game's own moments as its sport sees a whole game (`Sport.game_moments`: football's goals and cards
  whoever's, every named cricketer's wickets and fifties, a chess board turning, an esports series' maps or a
  lobby's matches, every result), titled with the game ("India v Australia: Kohli out for 82"), and says
  whom each is about, so a fan who follows a player in it too hears each moment once, as their player's,
  and one the other side already said in an earlier push isn't said again (the same play, by the source's id
  for it). A game here as two events (a chess board is a fixture of each player) is one follow, found at
  either address; it settles on the event of a player the fan follows (the tick), so its moments come once
  and its board shows once.
  They go by the fan's level for the game (key moments by default; the bell beside Follow, or Alerts'
  Settings), on the game's one notification, muted with it, and are in the Alerts list. Today shows the
  games followed this way that none of the fan's players is in, live with their own games and to come in
  Coming up (`GET /api/me/matches`, and the fan stream's game channels). When the game ends the follow
  lapses within the minute (the tick), after the result is sent; a game that never starts (to come, or armed
  with its source still saying so) lapses a day past its start. A fan follows at most 20 games at once; a
  made-up address makes no follow and no poll, and a game no running source can poll by itself (one only a
  followed player's fixtures bring) says No live alerts rather than promise them. A game nobody follows a
  player in, from a source polled a request a game (Sportmonks cricket, Lichess), waits while that source is
  down to its live reserve, so followed players' games keep it: the page and Today say its alerts are paused,
  and the late-games metric doesn't count it. `PUT`/`DELETE /api/match/<source>/<game id>/follow`,
  `DELETE /api/me/matches/<event id>`.
- **Clips** (`sports_follow/clips.py`, `clipstore.py`, BHA-364 under BHA-363): short videos cut on Slam's Mac app
  and published to Slam, for a game's feed (`GET /api/clips?match=<its match page's address>`), its
  competition's (`?competition=`), a player's (`?player=`) or everyone's, newest first (`?before=` pages back).
  Only a publishing key's holder publishes, for now admins (`python -m sports_follow.clips key "<name>"`, or
  `POST /api/admin/clip-keys`; the key is shown once). The app sends `Authorization: Bearer slam_pk_…` to
  `POST /api/clips` with the match page's address, the moment it read, length, size, its poster's size and source,
  and gets two upload URLs signed for 15 minutes, each for its file's type and size. It puts the video and its poster frame straight into the bucket (Cloudflare R2;
  the server never carries a video), then `POST /api/clips/<id>/done`: both files are checked and the clip goes
  live, served from the CDN domain. A clip recorded from a browser window (a broadcast, its organiser's production)
  is refused while Slam itself posts clips (`clips.RIGHTS`, BHA-369). Fans report a clip once each
  (`POST /api/clips/<id>/report`), which flags it for the admin panel (`/api/admin/clips?status=reported`) to hide
  or take down; reports never hide a clip by themselves, as an anonymous fan is just a request without a cookie.
  A hidden clip's files are moved off their public address, to a name made with the bucket's secret (the CDN domain
  serves every name), and back when it's put back; one taken down has them deleted, wherever they are. Without the bucket's settings, publishing says it isn't set up and the feeds are empty.
- **Predictions with friends** (`sports_follow/predictions.py`, BHA-317): before a game, or a map of a series, a
  fan calls it in a tap; the app settles it from the board its poller reads anyway, and friends compare in small
  leagues joined by an invite link. Bragging rights only: no entry fee, no prize of any value (India's online gaming
  law, 2025), no odds. What's asked is each sport's (`Sport.asks`, below): a chess game's result (either player, or
  a draw), who takes each map of a team esports series, who wins a football match (home, a draw or away, from its
  final score: extra time counts, a shootout doesn't) and who wins a limited-overs cricket match (a tie a Super Over
  doesn't settle, no result or a washout is voided); a game called off is voided (the board's `cancelled`),
  one postponed waits for its new date (no picks, no reminder and no closing till then). Other sports ask nothing
  yet. A question (`prediction_question`) belongs to the game, whichever fixture shows it (a chess board is one of each player's: `Sport.board` gives the
  id they share), and is made from its board the first time it's seen before the start: a poll of a followed
  game, a look at its match page (which lists its questions), or the worker's read of a fan's game, or one of
  Today's big games, coming up within a day (`prepare_questions`, every ten minutes, at most once every two hours a
  game till its board asks: a big game's card says "Call it" to a fan who follows nobody). A game with no start
  time is asked nothing till it has one (nothing would close it as it began); one asked before that takes a pick
  only on a board a few minutes fresh that says the game's still to come. It
  closes as its game or map starts (`Sport.closed`; a first question's start time closes it too, and the server
  refuses a pick after that) and settles from the board (`Sport.answer`): a right pick scores 100 less the share of
  fans who picked the same, at least 10; a wrong one 0; a map the series didn't need is voided, as is a question
  still unanswered three days past its start (a postponed game's wait a month for their new date). A game is
  polled as a followed game is while a question of it waits: one a fan picked any question of, or one with a
  later map, which only a poll can close. It's armed by its start, which a followed player's or team's refresh
  moves, or for a game nobody follows anyone in, a read of it: one a pick waits on is read every two hours in the
  day before it, and a big game also when the league's read says its start moved, so a game put back or brought
  forward closes at its new start. A game under way takes a
  later map's pick only on a board a few minutes fresh, so a map already played can't be called. Picks need no account (they're kept under the fan id and move into the account on sign-in); making or
  joining a league needs one (`prediction_league`, `prediction_member`: a six-letter invite code, a name the
  friends see, never the email). Fans see how everyone split once they've picked; friends' picks show in the
  league once the question's closed; a league's table is its members' points on what settled since it was made.
  The split shows to a fan once they've picked and at least 20 have, and to everyone once the question's closed.
  While it's open it's shares of the split as it stood when last refreshed (at 20 picks, then each 10 more, a tenth
  more past a hundred: `Question.split`), with that count, never the exact one: else its moves would tell a fan a
  friend's pick. And it's of fans in no league, whose picks join it as it closes: a member who made the rest of a
  batch with fans of their own can't read a friend's off it. Only picks from an account, or from a fan whose cookie
  came with the pick, count in the split and in what a right pick scores (`Pick.counted`): a request without a
  cookie is a new fan, whose pick is theirs and scored but moves nobody else's numbers. Picks on questions still
  open move into an account on sign-in, those already under way stay the device's (no calling a game three ways and
  signing in on the right browser). A game
  whose source is off asks nothing, and picks are limited per address (an IPv6 client's /64, its /48's /64s together
  having a ceiling of their own, as every per-address limit does). In the app: "Call it" on a match page (beside the
  game on a wide screen, in the side column) and on a campaign page (its players' games); on Today and in the side
  column, a game's card says "Call it" until the fan has, then their pick, then "You called it · +38" or what they said
  (`GET /api/me/predictions`, `web/src/components/Calls.tsx`); `/me/leagues`, `/league/<code>` and the invite `/j/<code>`, whose sign-in comes back
  to it. A question's own link (`/match/<game>?q=<id>`, its card's Share) previews the question, and its split once
  it's closed (`share.of_question`).
  The admin panel's Users tab counts leagues (and those of three or more), invites shared (once a day a member) and
  opened (by fans who came with their own cookie), and fans who picked on two days or more (IST). Pushes
  (`sports_follow/predictions_push.py`, the worker every minute): "Make your pick" two hours before a campaign's game (`campaigns.player_ids`: its players' game within three hours of one on its
  schedule, not their other events) to the fans who follow someone in it, its team or the game
  and haven't picked, and how each pick went once its question settles, with the fan's best league place. They go as
  moments' pushes do (Web Push, FCM), logged as notification rows (kinds "pick" and "called"), and as each fan set
  their alerts: none from a muted game or about players all set to "off", no reminder at "results" only, silent in
  quiet hours. A game's pushes share a tag, so a series' maps replace each other, only the first in 20 minutes loud;
  moments' noise rules don't count them (`notify.CALLS`), so a game's own alerts sound as they did.
- **Following a team** (`sports_follow/teams.py`, BHA-77): Arsenal, the Lakers, India's cricket side or T1,
  not only its players, in the sports whose teams can be followed (`Sport.follow_teams`: football, cricket,
  basketball, the esports team titles; not tennis or chess). The team registry (`team`) is one row per
  source's team, by the source's id for it, imported weekly (and on a fresh server's first start) from each
  running source's list (`teams()`, below), from the sport's first source with one (every one, for esports,
  whose sources sit side by side); search finds its teams by name or code beside players (`teams` in
  `GET /api/search`), from the registry alone. A follow is a row (`team_follow`: the fan, the team, their
  level). A followed team's games are read from its source as often as a followed player's
  (`team_fixtures()`, the tick) and linked to it (`event_team`), and each is then followed as a game followed
  by itself: armed by its sport's lead, polled in the live lane by the same allowances, its own moments from
  both sides reaching the team's fans by their level, and its result told from the team's side
  (`Sport.side_final`: "Arsenal won 2–1 v Chelsea", "Chelsea lost 1–2 v Arsenal") in place of the game's own,
  once even for a fan who follows both sides; a fan who follows a player in it hears each moment once, and
  one who mutes a game of it hears nothing more of that game. The refresh keeps each result from the team's
  side and its page (`GET /api/teams/<id>`): its game on now, its next games, its last five results and form,
  and its squad (the source's `team()`, read daily), each player followable as a search result is. A page
  whose games are stale asks for them at most every ten minutes, so a team nobody follows is shown too. A fan
  follows at most 30 teams; one whose source isn't running can't be followed. `PUT`/`DELETE
  /api/teams/<id>/follow`, `PUT /api/teams/<id>/alerts`, and `teams` in `GET /api/me/following`.
- **Your sports** (`sports_follow/fansports.py`, BHA-653): the sports a fan said they watch, in their order, a row
  each (`fan_sport`), kept as follows are: an anonymous fan's under their cookie's id, moved into the account on
  sign-in (the account's first, then the device's others) and deleted with it. The welcome's first step saves them
  and Following's Edit changes them (`GET`/`PUT /api/me/sports`, `{"sports": [keys]}`); a change reaches the fan's
  other devices as a follow does (`sports` in the fan stream's list). Only the sports `/api/sports` says are on
  count: a key of another is left out, and a sport turned off in the admin panel stays in the fan's list out of
  sight, and is back when it's on again (after any the fan saved meanwhile). Dropping a sport unfollows nobody in it.
  In the web app (`web/src/yourSports.ts`, `components/YourSports.tsx`): the welcome's tiles are every sport that's
  on, each esports game its own, and a picked sport with no starters offers its games on now and a search for its
  players; Following's row and Edit sheet (with none saved, the sports of who the fan follows ticked), also reached
  from Today's game search; on Today, each of the fan's sports with something on it gets a chip (`filter.ts
  theirGames`), "Players to follow" keeps offering their sports they follow nobody in past the first three follows,
  and the fan's sports come first in Live now, the big rails, the Live page and search's "Try" names. The Android app
  does the same (`mobile/src/yourSports.tsx`, with the web's `yourSports.ts`), reading the fan's sports with its other
  lists every 30 seconds and on coming back to the front.
- **Installable (PWA), the first step to Android.** A manifest, icons (`web/scripts/make_icons.py`) and a
  service worker (`web/public/sw.js`, production builds only) let Chrome on Android and desktop offer
  "Install app"; the app then opens full screen from the home screen. The app shell opens offline and
  shows the last update of your players; live scores and streams always go to the network. A new
  version waits for the fan to tap Reload, so a game in progress isn't cut off. The Android app
  (`mobile/`) opens stann.in links once `/.well-known/assetlinks.json` publishes its package and
  certificates (`SPORTS_FOLLOW_ANDROID_PACKAGE`, `SPORTS_FOLLOW_ANDROID_CERT_SHA256`; "Android app", below).
- **Links that preview.** A player's page shared in WhatsApp or X shows a card, not a bare address: the
  server writes each page's Open Graph and Twitter card tags into it (`sports_follow/share.py`), since the
  crawlers that read them run no JavaScript. A player's page says who they are and what they're doing
  now, live, last or next; a game of theirs (`/player/<id>?g=<event id>`, the link a This week poster
  shares) says its big moment in the sport's own words, as This week does (`week.game`); a campaign page
  (`/<slug>`, below) says its event, its next game and who to follow; a league's invite (`/j/<code>`) says join
  it, who asked and how many are in it, never its code; a team's page says who and its game on now, last or
  next; a clip's says its caption, moment and game; a game's (`/match/<source>/<game>`) its score, who won or
  when it starts; every other page says what the app is. A preview is what anyone with the link may see, read
  from what's stored: it counts no invite as opened and asks no source.
  Each has a 1200 × 630 picture drawn by the server with Pillow (`/api/players/<id>/share.png`, `?g=` for a
  game; `/api/campaigns/<slug>/share.png`; `/api/leagues/<code>/share.png`; `/api/teams/<id>/share.png`;
  `/api/clips/<id>/share.png`; `/api/match/<source>/<game>/share.png`; `/api/share.png` for the app): a block of the sport's
  colour, the name and the moment's word in Anton, the lines in Manrope, the app's name in the logo's
  Fredoka (the fonts are in `sports_follow/data/fonts`, OFL). No photos, no likenesses, no league or
  team marks. Its address carries a digest of what it shows, so a crawler that caches pictures by
  address gets a new one once the score moves. Each API process keeps the 256 it drew last and draws new
  ones one at a time, so asking for many can't take the threads the database calls use. The player page
  and each This week poster have a Share button, on the web and in the Android app: the phone's share
  sheet (on the web where the browser has one, else the link copied). It shares a link on the site, never
  the app's own, so it previews for anyone; `web/src/share.ts` says which link and title, for both. Every
  link carries `s=share`. Where a fan came from is kept with them (`fan_source`): the tags on the first link
  they open (`s=`, `utm_source`, `utm_medium`, `utm_campaign`) and its page, told by the web app as it starts
  (`web/src/visit.ts`) and by the Android app when a link opens it (`mobile/src/visit.ts`), to `POST
  /api/me/source`, which keeps it for a fan whose cookie came with it, a hundred a day from one address; moved
  to their account when they sign in and deleted with it. Nothing reads it yet: the launch numbers (BHA-239) will.
- **A Feed on every player's page** (`sports_follow/feed.py`, `web/src/components/Feed.tsx`, epic BHA-296): what
  fans, creators and the player share about them, judged before a fan sees it. Three layers: L0 fetches
  candidates from the sources into `feed_candidates`, L1 (the judge) keeps the ones really about the player with
  a one-line reason, and L2, at serve time in the API, takes out what this fan hid and groups the rest around
  the player's moments, then This week, This month (8 to 30 days old) and Earlier, the famous ones apart. `GET /api/players/<id>/feed` reads
  rows only, never a model or a source, and is empty rather than slow. `feed.judged` reads the rows the curator
  scored (below) as videos beside the card's news, and weighs recency only here: each one's score is faded to
  `FADE_FLOOR + (1 - FADE_FLOOR) x 0.5^(age / HALF_LIFE)` (0.45, three days; `feed.fade`), so this week's good
  ones lead and a strong one from three months ago still beats a weak one from yesterday (an evergreen one keeps
  its own), a page holds one video from each channel and two around each moment, and older ones still
  being watched are "the ones everyone's seen". With nothing scored, or if the rows can't be read, a feed is
  the card's news, so the Feed tab (which replaced News) is never emptier than News was, and "From the fans"
  on Overview is absent (as is `GET /api/me/feed`, a followed player's top video or post, never news, which
  only Android builds from before its Feed tab read). The "…" on a card says why
  it's there and takes the fan's word (`POST /api/players/<id>/feed/<item>/feedback`: not interested, not about
  the player, a report), kept per fan in `feed_feedback`, which hides it from them at once (under every player
  holding the video, but "not about the player" under that one alone), moves to their account when they sign in,
  and goes with the player or the account; 300 an hour from an address (an IPv6 /64, its /48's /64s together
  1,200), or from a signed-in fan's account. Every card has a picture where there is
  one (`picture`): a video's is YouTube's thumbnail (`VideoSource.picture`, hqdefault, which every video has)
  behind a play button, and the player, from youtube-nocookie.com, loads only once it's tapped; a story's is
  the image its publisher's feed attaches (`publisher_rss.image`: media:content, media:thumbnail, an image
  enclosure, else the description's lead image), kept as its https address and loaded from the publisher as
  they serve it, lazily; without one the headline is drawn on the sport's colour, and a post is words only. The
  Caddyfile's Content-Security-Policy allows images over https from anywhere (publishers' images live on their
  own image hosts, not their feeds'), video over https from anywhere (clips play from their bucket's public
  address) and frames from youtube-nocookie.com, and from player.twitch.tv and
  player.kick.com for a match page's Watch live (a `/watch/` page has its own).
- **The Feed tab: every followed player's at once** (`feed.page`, `web/src/pages/FeedPage.tsx`, BHA-344): one feed
  in the bottom bar and the sidebar, at `/me/feed` (a two-word address, which the Android app's links leave in the
  browser), from `GET /api/me/feed/items?cursor=&player=&limit=&tz=`. Each player's items as
  their own Feed tab has them, under the day each was published where the fan is (Today, Yesterday, This week,
  This month, Earlier), the ranking deciding within a day and the players taking turns, so no two in a row are
  one player's while another has something to show; a story two players hold is one card, and hiding it from
  either hides it. A page is 20; its `cursor` keeps the order as it was read (an item the fan hides meanwhile
  keeps its place, unserved, and rows that come in later wait for the next first page) and the last 20 served, so
  an item gone from every fan's Feed meanwhile skips nothing: each player's items carry on after their last one
  served, whatever moved up into its place. A rescore in between (views growing) can still move an item across a
  page's end. Each item
  carries its player (id, name, sport, photo with its framing) for the card's head; `players` are the circles
  across the top (a tap filters with `player=`), each with when their newest came in, which rings them in their
  sport's colour until the fan taps them; `newest` puts a dot on the tab until the fan opens it (only items of
  Today and Yesterday count, so an old video a sweep finds now isn't "new"; both kept on
  the device, `web/src/feedSeen.ts`). Today's "From your players" is its top two, in the edge-to-edge cards
  below, and "All N in your Feed".
- **Clips on the web** (BHA-367, `web/src/components/Clips.tsx`, `web/src/pages/ClipsPage.tsx`): clips published
  from the Mac app (the clips API, `GET /api/clips`) in a game's feed (a Clips section on its match page), a
  player's (on their page), and a competition's tournament feed (`/competition/<name>/clips`, older ones a page at a time;
  two words or more, so the Android app, which opens one-word addresses, leaves it to the browser);
  a shared clip opens on its own page (`/clip/<id>`). Each card plays the clip where it is (it loads when played),
  with its moment as a block of its sport's colour, its game and competition, Share and Report (a report flags it
  for the admin panel). A section with no clips isn't shown, nor is one the API can't answer.
- **For you and Following, edge to edge or full screen** (BHA-349, `web/src/components/FeedLayouts.tsx`): the Feed
  tab opens on For you (`?tab=for_you`), which mixes in videos of players the fan doesn't follow, 3 of every 5 items
  the fan's own and 2 others' (`feed.others`: of the players with something served, the fan's sports first, then
  anyone with a big moment in the last day, then the most followed on the app (by signed-in fans, then by anyone); 50 at most, the pool's among them,
  below), each entry saying `following`, so its card offers Follow, which works in place. Others' videos of the
  last two months (`feed.OTHERS_WINDOW`, BHA-382) are one list, best first by their score faded for age whatever
  their day, so a strong official video of three weeks ago comes before a weak one of today's; older ones follow.
  The fan's own players' stay in their days. A fan who follows nobody gets For you all from
  them. Following (`?tab=following`, the API's default) is the fan's players alone, with the circles; when they run
  out it says so and offers For you. How the apps draw it is a setting: `layout` on every page, and
  `feed_layout` in `/api/config`, `edge` (full-width posts: photo ringed in the sport's colour, name and under it the
  channel, Follow, "…", the 16:9 picture with the video's length, title, "views · when") or `full` (one item a
  screen, snapping, its picture framed on a blurred copy of itself, who, the channel and what at its foot, Follow,
  Share and More down the right). A video taller than it's wide (a Short: `video.upright`, from the player part
  videos.list gives with `maxWidth`, `VideoSource.upright`) is framed 9:16, in the middle of a 4:5 band edge to edge
  and on a player's Feed, and full screen between the tabs and what's at its foot, so nothing sits over YouTube's
  player (BHA-358). Since BHA-384 a Short plays as Shorts do: in the full-screen layout on black, its frame under
  the tabs as wide as the phone allows (`feed.shortFrame`, `.reel.short`), who and what in a strip under it, the tab bar
  dark and see-through over that strip, YouTube's player without its controls; tapped from edge to edge, it opens a
  viewer of the Feed's Shorts over the page (`?short=<id>` on the web, so Back closes it), swiping from one to the
  next. Nothing of the app's goes over the player, as YouTube's rules want. The Android app plays a Feed video where its picture was too, YouTube's player in a WebView
  on the site's address (`feedLayouts.tsx` InlineVideo), the tab bar where it was; and by itself as it comes on
  screen (BHA-359): muted until the fan turns the sound on (the app's sound button beside the video, YouTube's own,
  or a tap to play one), then with it until they mute (BHA-381, `mobile/src/sound.ts`, kept for the day); one at a
  time, the reel on screen or the topmost post more than half in view, stopping once it's off the screen or the
  Feed's left. Always by default; the Account page has On Wi-Fi and Off
  (`mobile/src/autoplay.ts`, kept on the phone); a tap on one playing keeps it as the fan scrolls on. The web's
  Feed does the same (`web/src/autoplay.ts`, `FeedLayouts.tsx` usePlaying; the setting kept in the browser), and
  also never while the browser asks to save data; its On Wi-Fi is "not on mobile data or slower than 4G" where the
  browser says (Chrome), as Always where it doesn't. A click into a player that started by itself keeps it. Its
  sound is kept as the app's is (`web/src/sound.ts`, FeedPlayer), but a browser lets a video start with its sound
  only once the page has been tapped: before that one starts muted with its sound held back, and the fan's next tap
  on the page lets it out. It's
  `SPORTS_FOLLOW_FEED_LAYOUT` until the admin panel's Feed tab sets it, live in every process within seconds.
  Every video's card shows its source's icon, linked to the video there (`VideoSource.mark`, YouTube's red play
  button as YouTube draws it, sent with each video as `video.mark` and drawn by `SourceMark` in both apps, 20 high
  at least, in clear space the size of its triangle): YouTube's API policies want every card showing its content to
  say so with its brand (III.F.2, BHA-652). It's by the "…" edge to edge, before the channel's name full screen
  (not down the side, whose buttons would grow toward the player), in a Short's row, and beside the words on a
  player's Feed. Privacy says the Feed uses the YouTube API Services, that YouTube's player may set cookies, and
  links Google's Privacy Policy; Terms (`/terms`, linked wherever Privacy is) says using the app means agreeing to
  YouTube's Terms of Service (III.A.1–2; the links are `web/src/legal.ts`).
  Titles are shown as YouTube gives them, since its API policies want them unmodified (BHA-652): emoji, hashtags,
  the channel's name and capitals stay; only HTML's escapes are read as they're served (`sports_follow/titles.py`: a
  search's titles came escaped before BHA-358 read them as written).
  A video's picture is the biggest YouTube has for it (maxres, then sd, then hq; the sizes come with each read). In the dev server, `?feed-sample` on a player's page
  shows made-up videos and posts (`web/src/dev/feedSample.ts`, never in a build). The Android app has
  it all too: the Feed tab with its dot and circles (`mobile/src/app/feed.tsx`, its own address `/feed`, since
  claiming `/me/` would take the site's `/me/leagues` into the app too), the same cards
  (`mobile/src/post.tsx`) on it, on Today and on a player's Feed tab, and Overview's "From the fans"
  (`mobile/src/feed.tsx`). A tapped video opens in the YouTube app (or the browser without it) rather than
  playing inside the card; `EXPO_PUBLIC_FEED_SAMPLE=1` on Metro shows the made-up items on a player's page.
- **Campaign pages: a big event as a row, never code.** `stann.in/gukesh` (`sports_follow/campaigns.py`,
  `web/src/pages/CampaignPage.tsx`) is the page a launch post links to: the event in a block of its sport's
  colour, the games on now (the fan's live cards of its players, else Live now's games they're in), its
  players side by side to follow with a tap or all at once, or whole teams, its games in India time (and
  the fan's own beside it when they're elsewhere), then turning alerts on and, on a phone, how (Chrome's
  Install on Android; Add to Home Screen on an iPhone, where Web Push needs it). Following is the ordinary
  follow with key alerts, which the sport words. **A whole team** ("Follow T1") follows every player on its
  roster at once (`POST /api/campaigns/<slug>/follow`): one action over the roster's players, not a new kind
  of follow. A roster is its source's (an adapter's `team()`; PandaScore's for esports), read when the team is
  added and again each morning by the worker, so a change of line-up reaches the page within a day; a source
  that doesn't answer keeps the roster last read. Its players are registry athletes a running source has
  (checked as a starter is), so following never needs AI research. A campaign is a row in `campaign`, set
  with a command; a later one (the WPL, the IPL) is a new row. Its address is any one word the app doesn't
  have (`campaigns.TAKEN`, which `tests/test_campaigns.py` holds to the app's routes), in lower case; a link
  typed with capitals (`stann.in/Gukesh`, from a poster) is the same page, its preview and the Android app's
  too, and the web app moves it to `/gukesh`. A campaign of a sport
  that's turned off has no page, and a player removed with `players remove` leaves every campaign.

      python -m sports_follow.campaigns set gukesh --sport chess --title "Gukesh v Sindarov" --event "World Chess Championship" --line "…"
      python -m sports_follow.campaigns player add gukesh Q56026072          # a registry id, player id or system:id
      python -m sports_follow.campaigns game add gukesh "2026-11-24 15:00" "Game 1"   # India time, or --utc
      python -m sports_follow.campaigns team find T1 --sport esports          # teams by name, with their ids
      python -m sports_follow.campaigns team add worlds pandascore_esports:126061
      python -m sports_follow.campaigns show gukesh                           # also list, remove, refresh; player/team/game remove
- **Push notifications for moments, without the noise.** Each poll compares the player's line with the
  last one (`sports_follow/moments.py`): the line-up (starts, or on the bench), a goal, a fifty, 30
  points, a set, the start and the result; for a player no live source has (`SPORTS_FOLLOW_RESEARCH=1`), news
  from a rebuild adds injuries, transfers and retirements, from stories the agent marks as breaking that news. Each moment is stored once (a
  stable key) with a priority: a hundred over a fifty, a late equaliser over a fourth goal. The noise
  rules (`sports_follow/alerts.py`) then plan each fan's
  pushes: their level for the player (key moments, the default; everything; results only; off) says
  what reaches the phone, and only key moments make a sound. Each game is one notification on the
  phone, replaced as moments come with the story so far underneath, whichever followed players are
  in it (two teammates' same play, "TenZ and zekken: Sentinels win 2–0", is one line); "Mute this game" stops it. A game under several fixtures (a chess board is one of each player's)
  is one game too, its fixtures found by the players on it (`Sport.board_players`): one notification all
  through a mini-match, one Android live card and one mute, and a fan who follows both players hears each
  moment once, from the side it went toward (`Found.voice`), where that side's fixture is being polled to
  say it (else from both: twice beats never), and late from the other side should it never say it. A goal VAR rules out is corrected on that notification, for the
  fans who were sent it, and told again if the source shows it again (a goal the source credits to
  another player instead is left as it was). A game called off and set again for later under the same fixture (a
  match abandoned and replayed) is told afresh, its line-up and start too, and nothing of the game called off is
  taken back in it (`notify.editions`).
  Sound is rationed (once every 3 minutes a game, 6 times an hour a phone) except for the biggest
  moments. `sports_follow/notify.py` claims each batch once, logs every
  push (`notification`), sends it to each device at once (by Web Push with VAPID to browsers, by FCM to
  the Android app, `fcm.py`), and the tick sweeps up any batch a restart dropped (a game's at a time, should the
  batch fail, so a moment that can't be planned holds back no other). A player or a team with more fans than one
  query can name (Postgres takes 65,535 values) is planned a part of them at a time (`notify.chunks`). Devices that took their last
  push go by threads of their own, apart from new and failing ones, and each push service gets 2 s to connect, so
  a device that never answers holds up no other fan's alerts; push addresses must be a known service's own, and
  one network turns notifications on for only so many new devices a day. Quiet hours are per device and make notifications arrive without a sound or vibration (on Android still on the lock screen, so a phone set to wake its screen for notifications may light it); one that arrived silent on every device doesn't count toward the hour's sound.
  With the app open and in front, a moment shows as an in-app toast instead. The Alerts tab's Latest
  list shows every moment of a fan's players, pushed or not, with what's new counted on the tab.
  `python -m sports_follow.notify stats` shows what was sent and how much was opened, by kind.
- **Results history, read once and kept.** Every finished game a source returns is stored as the
  player's result (`event_player.result`), so the Matches tab's results page through a whole history and each
  game opens to its final score and the player's full line. A history lane (`sports_follow/history.py`)
  fills in missing scorecards and older seasons at most 20 source requests a minute system-wide, and
  the Stats tab shows form over the last 10 or 5 results from the stored lines (`sports_follow/form.py`).
- **Only what's read is kept.** Polls append a state and a player line per change; once a game has been
  final and quiet for 14 days, the worker's daily prune (`sports_follow/retention.py`, 02:15 UTC) keeps
  only its latest and final states and each player's latest line. Moments and the push log go after 90
  days, a spell a player was out (`sidelined`) once no read has listed it for 14, and the hot copy of a card
  nobody follows expires a day later (the API rebuilds it from Postgres).
  `python -m sports_follow.retention --count` shows what a run would remove, without removing it.
- **Chess swings from our own engine, on a fixed CPU budget.** Lichess broadcasts carry positions but
  no evaluations, so Stockfish evaluates live games of followed players (`sports_follow/engine.py`) and
  a fan hears when their player is winning, in trouble, or turns a game around (key), or is better,
  worse or level again (everything). The work is per game, never per follower: each game has at most
  one waiting position (the newest), positions are cached, the game that has waited longest goes next,
  and a game is evaluated at most once every 20 s. One engine lane runs system-wide (a Redis lock) on
  one thread at low priority, about a second per position, and rests as long as it worked: at most
  half of one core. Every position gets that first look, and alerts go by first looks alone: each is
  deterministic (its own engine process, cleared before each look, one thread, a node budget and no
  clock), so a position scores the same however busy the server is or whatever was searched before, on
  one Stockfish version. While someone has the game's match page open (web or Android), the lane then
  deepens the position it's on with the time it would otherwise idle: one search of it, in a second
  process, a second at a time and paused in between (a new position anywhere goes first), to depth 30,
  so the evaluation beside the board gets deeper the longer a player thinks; with several boards on,
  they take turns. A position scores the same whether the game is watched, and deeper readings are
  shown, never alerted on. A band changes only when the first looks of two positions in a row agree, so a board glitch
  isn't a swing. It's settled once a board, so both players' events of it agree, and a decided game (a
  mini-match stays live after it) isn't evaluated any more. The live state carries the latest evaluation (`eval`), and the board draws it
  as a bar beside the board, on the site and in the app, White's share by Lichess's winning-chances curve (`whiteShare` in
  `web/src/sports/facts/chess.ts`).
- **A chess game's match page.** Its board is as big as the band and the screen let it be (up to 560px), the
  players level with its edges (beside it on a wide screen, above and under it on a phone), and its moves beside
  it: a finished game's from its PGN; a live game's so far, its PGN read once by the page's first read (without
  clocks, so never the finished game's copy) and kept in Redis per game (`lichess:moves:<game>`), each poll or
  read of the board that finds it one move on adding that move as Lichess gives it (`d5e3`, the page's `more`)
  at no request, and a read reading the PGN again only after moves were missed between looks
  (`LichessChess._moves_so_far`); the browser writes them in SAN with chess.js, and adds the board's own last
  move where the list is one short of it. The ‹ › buttons by the moves' heading step the board back and on along
  them, as do the left and right arrow keys on the site (its bar empty meanwhile: the live evaluation is the
  position now's), with Back to live (To the end, once it's over) under it; new moves leave it where it is. The
  Android app draws it alike, the board as wide as the screen with the moves under it
  (`mobile/src/sports/chess.tsx`).
- **Play from here, on the fan's device.** A chess game's match page opens an analysis board over the page (a
  modal dialog, the board up to 720px; in the Android app a full-screen Modal, `mobile/src/sports/chessAnalysis.tsx`): the fan plays either side's legal moves from the game's position (or
  from any move in its list that replays, or the one the board was stepped back to), steps along their line (the
  arrow keys too, on the board as well, unless the fan moves square to square there by keyboard), plays any of the engine's best three lines to
  a move, with its best move as an arrow, flips, starts over, turns the engine off, and Back to live (or Escape,
  or Android's back) returns to the game. A
  Freestyle (Chess960) position that can still castle isn't offered: chess.js would castle it the standard way.
  Its bar comes from Stockfish 18 (the lite single-threaded WebAssembly build, GPL-3.0, vendored in
  `web/src/sports/engine/` with its source commit, licence and checksums) in a Web Worker of the page, each
  position to depth 18 from a cleared hash on one thread, so every device ends on the same result (a slow phone
  later; the lite network's, which can differ from the server's full engine; three lines on the web and in the
  Android app alike, as the line count changes the search too). The build names its files by
  their content under `/assets/`, a fan's browser fetches them only when they open the board, and the service
  worker keeps them then, as it does every built asset. Moves come from chess.js in a chunk of its own
  (`web/src/sports/facts/chessLine.ts`, `chessAnalysis.tsx`). Nothing the fan plays reaches the server, so it
  never reaches an alert. The Caddyfile's Content-Security-Policy allows WebAssembly (`'wasm-unsafe-eval'`).
- Adapter parsers are pure functions over the source's JSON, tested against recorded responses in
  `tests/fixtures/espn` (`.venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m pytest`).
- **Adding or removing a data source** is one module and one line (a source with a key of its own also
  names the key in the deploy's secrets lists: the `secrets` skill, `.claude/skills/secrets`, says where). A source is a module in
  `sports_follow/adapters/` with an `Adapter` subclass (`adapters/base.py`) that describes itself: its
  `system`, the `sports` it serves (one, or each esports game it covers, with `sport_of` saying which an
  athlete plays and `running()` which run now, so it reads only theirs), its `name` on a page, its `provider` (the Credits entry, shared with
  the provider's other adapters), what it `covers`, its athlete `id_pattern`, and `available()` (a
  licensed source needs its token). Optional capabilities are opt-in, so nothing else changes: a name
  `search` for the registry, a `roster` of pros for the registry (esports) and the Wikidata property
  that keeps its athlete ids (`wikidata_property`, Liquipedia's P10918), a provider's live search and
  `recognise` for search as you type, a provider's `research` (its public API, which the research agent's
  prompt points to while it runs), `credit` for a live card carrying another's data too (OpenDota's),
  `snapshots` for batched live polling, `Snapshot.sides` on a scorecard that says which side each player is in
  (a result is then won or lost by the side they played for, a traded player's old one too), `live_now` for "Live now" (every game it has on, league-wide;
  `sports_follow/live.py`; where a game's id isn't the fixture id a player's card has it under, its
  `LiveGame.fixtures` says those, as chess's boards do, so Today shows a fan's game once, as theirs; where
  the source rates its events, it leaves out the small ones and sets `LiveGame.tier`, the biggest first, as
  esports does by PandaScore's tournament tiers, letting a game's tier C series stand in while none bigger is on),
  `locator` for following a game by itself (the locator its live polls take, from a fixture's id alone, so
  a game nobody follows a player in can be polled; `sports_follow/matches.py`; without it, only a game a
  followed player's fixtures bring can be followed that way, and its others say alerts aren't available),
  `match` for a game's match page (one game by a fixture's id, whoever follows it, live, to come or over,
  with its line-ups and every player's line, in a request or two: a chess board's moves are a second, once
  while it's on and kept up after, and once it's over; a finished game's read that lacks what the source will have later, or may still change, sets
  `LiveGame.partial`, so it's read again rather than kept a week; where the source lists the game's
  broadcasts, `LiveGame.streams` has them, as its fixture's `streams` do, for the page's Watch live; ids of
  another shape than its `fixture_pattern` are refused; `sports_follow/match.py`; a league game whose source
  has none has no page of its own),
  `schedule` for Today's big results and big games coming up (its competitions' games between two days,
  whoever plays them, three days back and a fortnight on; `sports_follow/league.py`; each game ranked by its
  sport's `notable`, and a source that rates its events sets `Fixture.tier`, and what else its sport ranks
  by in `Fixture.facts`; a source of several sports says each game's in `Fixture.sport`, as in
  `LiveGame.sport`; read a few times a day at
  most, only while fans look: Sportmonks football 3 or 4 requests a read, cricket 1, PandaScore about 6, PUBG
  none),
  `sidelined` for the Wire's facts (every player out of its competitions, injured or suspended, by team;
  `sports_follow/sidelined.py` compares each read with the last, so a new spell is news that a player is
  out and one that's over news that they're back, as their sport's `out_fact` and `back_fact` word it; read
  twice a day by the worker: Sportmonks football's 5 Team requests a read, and a League one a day),
  `live_note` and `lag_note` for a player page's words on how its live scores come in and how late they
  can be, where the app's general sentences don't fit (Liquipedia's and PUBG's standings after each match),
  `team` and `find_teams` for a source that lists teams' rosters (a campaign page's whole-team follow, with
  `team_id_pattern` the shape of its team ids; PandaScore's `/teams`),
  `teams` and `team_fixtures` for a source fans can follow teams from (every team it covers in its sports that
  run, read weekly for the team registry in a few requests, and one team's games, finished and to come, over
  the window `fixtures` reads for a player; `sports_follow/teams.py`; its `team` gives the team page its squad,
  and its `result_label` says each result from the team's side),
  `spare` for background reads, `rekey` for a source that changes
  how it names its athletes (the worker moves every player bound to it to the new ids, and the registry
  drops its rows with an id of the old shape).
  The one line is its place in `adapters.KNOWN`, which is also the order of preference when two sources
  serve a sport. A source of videos for the feed (`adapters/youtube.py`) is a `VideoSource` instead
  (`adapters/base.py`): it describes itself the same way, plus the shape of its channel ids
  (`channel_pattern`), lists a channel's latest `uploads` and what it says of up to 50 videos at once
  (`facts`), how its player plays one (`embed`), a still of it (`picture`), whether it's upright (`upright`) and
  whether its terms let it be served (`allowed`: not YouTube's made for kids), and its icon (`mark`), which every
  card of its videos shows linked to the video there; one with API terms of its own adds its part to Privacy and
  Terms (both apps' pages name it in their words, and `web/src/legal.ts` holds the links). Its one line is its
  place in `adapters.VIDEOS`. A source of news for the feed (`adapters/publisher_rss.py`)
  is a `NewsSource`: the shape of the feed addresses it reads (`feed_pattern`) and a feed's latest `articles`, one
  line in `adapters.NEWS`. Parsing that two sources of a sport share lives in `sports_follow/sports/`, so removing a
  source never breaks another. `tests/test_sources.py` fails if shared code names a source: its system,
  its provider's name or an API it reads, or its URL.
- **One rate-limit layer for every source** (`sports_follow/adapters/budget.py`). Each source's client
  declares a `BUDGET`: requests a window (an hour, a minute, a day, several at once), a margin never
  spent, a reserve kept for live games, and, for one with a reserve or a scarce one, fans' own share (fewer than
  the reserve: `tests/test_budget.py` holds every source to it). Its provider lists it
  (`Provider.budgets`), so the usage report finds it through `adapters.KNOWN`. Before each request it
  sends it takes one; it reports what the source says is left and its 429s, which pause every process.
  The research agent's `fetch_url` spends the same budget when it reads a source's API (`Budget.urls`).
  Counts are kept in Redis, so the API and the worker spend from one allowance. A minute's allowance (a
  rate limit) is counted over the last minute, whenever it began, so a burst across a minute's edge can't
  send twice it; an hour's or a day's from its start, as the source resets it. Every request is in a
  lane, set once where the work starts: `live` (polls), `fan` (every API request: search, previews,
  following), `refresh`, `history`, `import` and `feed` (the curator's). The background lanes stop at the reserve; a fan loop
  stops at its share and is logged, so live games keep the rest of the reserve. `python -m sports_follow.usage` shows each
  source's spend this hour and today, by lane.
- **One cache for every source** (`sports_follow/adapters/cache.py`). Each client reads through a `Cache`
  named after its budget: this process's copy, then Redis, then the source, and only a request sent is
  counted. A response is kept as the source sent it, with when it was fetched, and each caller judges it
  by its own ttl (the same match at 5 s while it's live, 24 h once it's over); a ttl of 0 is never kept.
  "Fetched" is when the request went out, and Redis keeps the latest: a slow answer to an earlier request
  never replaces a newer one.
  Threads asking for the same thing at once send one request. The Redis is shared by the API and the
  worker and outlives deploys: `SPORTS_FOLLOW_CACHE_REDIS_URL`, in production a capped Redis of its own
  that evicts the least recently used key. While it's down, each process keeps its own copy, as before.
- **The feed's fetch runs in a process of its own**, the curator (`sports_follow/curator.py`, `python -m
  sports_follow.curator`, the `curator` service in `deploy/compose.yaml`): an arq worker on its own queue, so
  it never takes the worker's slots, logged, traced, measured and stopped as the worker is, its requests in
  the `feed` lane. Every half hour (`sports_follow/candidates.py`) it reads the latest uploads of every
  channel a running sport lists (`Sport.video_channels`: official channels, meaning leagues, federations, clubs,
  broadcasters and tournaments, and a few top creators; the only channels any stream keeps a video from,
  BHA-349) from each channel's uploads playlist, through the Data API (`playlistItems.list`, 1 unit: never
  youtube.com's public channel feeds, since YouTube's Developer Policies allow no other way to get its data,
  III.I.14, BHA-652); keeps those whose title or description's opening (up to its first blank line or link, 300
  characters at most: not the related-video or podcast links under it) names a followed player of that sport, or one
  of its pool (below) (their full name or a hashtag of it, a nickname fans typed, or their surname when no other
  such player of the sport has it: the last word of their name, or of an alias that shares a word with it, and the
  given name of one written as FIDE does, "Gukesh" for "Gukesh D"; never an initial); one that names none of them but names a team they play for ("Falcons vs
  Vitality", "Liverpool v City": the team as its sport's titles write it, `Sport.team_names`, without the words its
  titles leave off, `team_affixes`; not "India Women" for India, `other_sides`) is kept for the team's followed
  players and 3 of its pool players, marked `about: team`, which the judge reads as a share about the team
  (`team`, served for them at 0.8 of one about them; it reads one such video once for all of them); asks YouTube about the new ones, 1 unit for every 50, which also says whether the
  maker declared paid promotion in it; and keeps those that pass the hard
  filters (embeddable, public, not age-restricted, not made for kids (`status.madeForKids`: YouTube's III.E.4.j),
  playing in India (`SPORTS_FOLLOW_FEED_COUNTRY`), not live, 1 to 20 minutes)
  in `feed_candidates`, one row per player and video, which goes with the player. A day later each is read
  again for its pace, and every stored video of a followed or pool player is read again weekly and dropped once
  removed, private, no longer embeddable, age-restricted, made for kids or blocked in India; while one is under two
  days old it's read again every six hours. A player nobody follows any more and the pool has left isn't offered in
  For you, and their videos, unread, go within a month.
  Some still won't play inside the app: a Content ID owner (LaLiga's, say) keeps them off other sites, which
  the Data API doesn't say (embeddable, no region rule). Only the player knows: YouTube's IFrame Player API
  says so (error 101 or 150, or 100 for one gone), and the apps then show "This one plays only on YouTube"
  with the way there, and tell the server once (`POST /api/players/<id>/feed/<item>/unplayable`). It leaves that
  fan's Feed at once, and it's their network's vote (an IPv6 one's /48, kept a month in Redis) on whether it leaves
  everyone's: once three networks have said so (`feed.UNPLAYABLE_VOTES`), the server marks every row of the video
  (`unplayable_at`), no Feed serves it again, and a fetch that finds it for another player keeps the mark. One fan,
  or a script with new cookies, takes it from their own Feed alone. A fan's votes are counted so many an hour, and
  an address's (10 and 30, each count taken before the report is acted on); past either, the video still leaves
  that fan's Feed and only the vote is dropped. Reports never ask YouTube anything. The admin panel's Feed tab lists
  every video taken out this way under Won't play, once each, newest first, with the players it's kept for. Put back
  (`POST /api/admin/feed/<row>/put-back`, `feed.put_back`) takes the mark off every row of it, each served or not as
  the judge and any admin's word say (nothing is approved), and starts its votes again; approving or pinning it puts
  it back too, and then no app's word overrides that. Any admin word on it starts its votes again. The weekly check
  drops one YouTube says is gone, private or no longer embeddable, marked or not, so those leave the list by
  themselves. (Whether 101/150 can also mean a block in the viewer's country alone isn't known; only apps whose
  time zone is India's vote, `SPORTS_FOLLOW_FEED_ZONES`.)
  After each poll the judge (below) reads the new rows and `sports_follow/rank.py` scores every judged one:
  the judge's interest, how widely it's watched for its age (log views a day since it was published, as its
  latest read counted them, so the score is age-fair and stays put as days pass) and its like ratio, times its
  channel's trust (high while a running sport lists it, what the admin panel's Feed tab sets, lower for each
  of its videos fans hid or reported). Only a share about the player is served: a verdict that it only
  mentions them, isn't about them, is paid for or has a risk keeps the row but never serves it; the admin's approve, hide or pin stands over the judge. Each row is also
  tied to the player's moment it was published within two days after, so the Feed groups it there. Features,
  verdict and score are kept apart: `python -m sports_follow.curator rescore` works every score out again
  without fetching, and `--rejudge` asks the judge again first. The admin panel's Feed tab (`/admin/feed`)
  lists the candidates newest first with the verdict, score and reason, to approve, hide or pin each, and
  sets each channel's trust. The feed only reads the table and never waits on the curator. YouTube's
  free quota is 10,000 units a day (reset at midnight Pacific), counted as the `youtube-data` budget with 1,000
  never spent: the channel poll is 48 units a channel a day (its uploads playlist every half hour: 4,848 for the
  101 channels the sports list now, and 101 more for a curator restarted mid-day), and reading what's new and what's
  stored again a few hundred more, so about 5,450 of the 9,000 spendable with the searches' reads. Each channel a
  sport adds costs 48 a day: about 70 more fit before the poll needs a slower cadence or more quota
  (`tests/test_moment_search.py` holds all of it to that). Searches
  have a quota of their own (BHA-342; YouTube moved `search.list` to it on 1 June 2026): 100 a day, 1 unit each,
  none of the 10,000, counted as the `youtube-search` budget with 5 never spent. Both are counted from midnight
  Pacific, as YouTube counts them; the Google Cloud console's quota page for the YouTube Data API v3 (APIs &
  Services, YouTube Data API v3, Quotas) shows both, "Queries per day" and "Search Queries per day".
  Without the key the source is off, the admin panel says so, and nothing else changes.
- **Big moments are searched for too** (`sports_follow/searches.py`, BHA-303): fans post edits of a hundred or a
  brace within hours, and a channel's feed holds only its latest 15 (what a search finds is kept only from a channel
  the player's sport lists, as everything is). After each poll the curator queues a search for each key
  moment of a followed player's (not one taken back; a search whose moment is taken back goes) that its sport has words for (`Sport.search_words`: "century
  Lord's", "brace Liverpool vs Everton", "wins game 4"), after the player's name as fans write it (below): one per player and game, `feed_search`,
  which a later moment in the game takes over (the brace over the goal) until its last run. Each runs twice, three
  hours after the moment and a day after, for videos published since, most viewed first, embeddable and syndicated,
  as a viewer in India finds them; one the cap held back for a day runs once, its last at least eight hours after
  its first, a run no source answered is tried again, and one two days old is dropped. What
  it finds goes the channel poll's way: kept where it names the player and passes the hard filters, unless it's
  stored for them already, with its moment attached, then judged and ranked. Searches are 100 a day, so they're
  capped at `SPORTS_FOLLOW_FEED_MOMENT_SEARCHES` runs in any 24 hours (40: 20 moments searched twice; rolling, so
  YouTube's Pacific quota day holds no more), the biggest moments first when there are more, and none once the
  day's searches are spent. Moment searches have first claim on them: the sweeps leave what this cap may still
  spend. What each finds is read with one `videos.list` (1 of the 10,000 units). The ranking keeps a searched
  video's moment while it stands, rather than the biggest one near it. Off without the key.
- **Players with nothing to watch are searched for, and so are their season's and their most watched** (`sports_follow/sweeps.py`,
  BHA-336): a channel's feed holds only its latest 15 uploads and most games fire no big moment, so a followed
  player can go a week with nothing. Once a day (08:15 UTC, just after YouTube's quota day starts; again at 20:15, which only does what a missed morning
  run didn't) the curator runs a
  **gap search** for each followed player with nothing the Feed would serve published in the last 7 days (the
  coverage gauge's test): their name and their sport's words (`Sport.video_words`: "Shreyas Iyer cricket", "TenZ
  Valorant"), videos of the last 14 days, most viewed first, embeddable and syndicated, as a viewer in India finds
  them. The most followed (by signed-in fans, then by anyone) go first, up to `SPORTS_FOLLOW_FEED_GAP_SEARCHES` a day (20; a sweep's day is the last 23 hours, so a run a little earlier than the day before's still has its own); a
  player is searched once a day at most, and after a search that kept nothing, not again for 3 days. Then the
  **wide sweep** (BHA-340): every followed player, gaps or not, for videos of the last six months (183 days), most
  viewed first, which the score weighs for their age and the Feed fades as it serves them; each player at most once
  a week, a seventh of them a day, up to `SPORTS_FOLLOW_FEED_WIDE_SEARCHES` a day (14), the longest unswept first and the most
  followed of those; a player the same run gap-searches waits for a later day. Then the
  **famous sweep**: each followed player's most watched videos ever (no date limit), for "The ones everyone's seen",
  each player at most once a month, `SPORTS_FOLLOW_FEED_FAMOUS_SEARCHES` a week (35, over the last 7 days less an hour) and a seventh of that a
  day (5), the longest unswept first and the most followed of those, so a cap that binds gets round
  everyone before anyone's swept again. What they find goes the moment search's way (hard filters, the judge,
  the ranking; what's stored already isn't kept again, and counts as `already_stored`). Each search is a
  `feed_sweep` row with what it found and kept, and each kept candidate names its sweep, so
  `python -m sports_follow.curator sweeps` lists the last week's with what each serves (`--run` runs the day's
  first). In the feed's funnel they're the `gap`, `wide` and `famous` streams, run in that order. Moment searches
  come first: sweeps stop while what's left of the day's 95 spendable searches is what the moment searches' cap
  may still spend in the last 24 hours (40), so on a day no moment search has run yet the sweeps have room for 55
  and their three caps (20 gap, 14 wide, 5 famous: 39) bind first; all four caps together are 79 searches a day,
  under the 95, and their finds' reads under 100 of the 10,000 units. Then the **pool sweep** (BHA-382): each pool
  player nobody follows (below), for videos of the last 60 days, each at most once a fortnight, up to
  `SPORTS_FOLLOW_FEED_POOL_SEARCHES` a day (10), the longest unswept first and the best known of those:
  the five caps are 89 searches a day, and 10 more units of the 10,000 for reading what they find. At 19 followed players (17 uncovered) the
  first day is the heaviest: 17 gap searches, the 2 covered players' wide searches and 5 famous, 24 searches;
  after that a day is the gap searches still due (fewer as players are covered or wait out the 3 days), about 3
  wide searches and the famous sweep's 5 for its first 4 days, then under 1. At 500 the caps bind: 20 gap, 14 wide
  and 5 famous a day, so the wide sweep gets round each player every 5 weeks or so. The 16 searches a day no cap
  claims are room to raise the sweeps' caps (6 since the pool sweep's 10); more than that means a lower `SPORTS_FOLLOW_FEED_MOMENT_SEARCHES` or
  more YouTube quota (Google's quota extension form). Off without the key.
- **A pool of top players fills For you** (`sports_follow/feedpool.py`, BHA-382): besides followed players, the
  curator fetches for each running sport's best known: `SPORTS_FOLLOW_FEED_POOL_SIZE` (50) a sport, and that many
  for each running game of esports, by Wikipedia editions (the starters' measure, the home country's double), then
  their team's tier (`Team.tier`, all an esports pro has); one neither rates, or an operator removed, is left out.
  Once a day (07:40 UTC, and when the curator starts without one) it works the pool out and makes a player of each
  member who has none, found in their sport's live source as a fan's follow from search would, up to
  `SPORTS_FOLLOW_FEED_POOL_ADDS` (40) a day, the sports taking turns; one no live source has is looked for again
  after a week. Nobody follows them, so the worker reads nothing of theirs until a fan does: then their page is
  built and their games read as for any follow. The pool (player ids, in Redis `feed:pool`) joins the followed
  players in the channel poll (no quota) and gets the pool sweep above; its videos reach fans as For you's others,
  never Following. `python -m sports_follow.curator pool [--run]` lists it (and works it out first).
- **What every search asks for** (BHA-341): the player by the name fans and titles write (`candidates.search_name`):
  of their own name and the aliases with fewer words than it, the shortest that still names them and no other
  followed player of their sport, sharing a word with their own name (never a namesake's slug, "mohamed salah
  football"): "Jasprit Bumrah" for the registry's "Jasprit Jasbirsingh Bumrah", "MS
  Dhoni" for "Mahendra Singh Dhoni", never "Bumrah" or "Boom Boom" alone, but "Gukesh" for FIDE's "Gukesh D" (their
  own name without the initial it ends in), and their own name when two followed players share the rest. Matching stays as it was: any of their names. And each search lists 50 results (25 before:
  the same one search, and one `videos.list` read either way) and leaves out what's tagged `#shorts` (`q` ends
  `-#shorts`, YouTube's NOT): most watched first, results are mostly Shorts, and those under a minute are refused
  for length. `videoDuration` can't do it: its `short` is anything under 4 minutes, where 1-4 minute highlight
  clips are too. A Short nothing tags is still refused, as `too_short` in the funnel, which is how to tell whether
  the NOT works.
- **News comes from publishers' own feeds** (`sports_follow/news.py`, `sports_follow/adapters/publisher_rss.py`,
  BHA-325), into the same pipeline as videos. Every 20 minutes the curator reads each RSS or Atom feed a running sport
  lists (`Sport.news_feeds`), through the source cache (a feed at most every 15 minutes) and conditionally (the ETag
  and Last-Modified of its last answer, so an unchanged feed is a 304), in the `feed` lane, counted as the
  `publisher-rss` budget (no published limit). Of the stories published in the last three days it keeps those whose
  headline or description names a followed player of that sport: their full name or own one-word name as for videos,
  or their surname alone only when no other followed player of the sport has it and fewer than three of the sport's
  athletes in the registry do ("Smith" doesn't qualify); otherwise only with one of the player's sides (a team, their country)
  in the story, and no other player whose name it is. A one-word name counts only capitalised (a handle as the
  player writes it) and not next to another capitalised name ("head coach" isn't Travis Head, "Deepti Sharma"
  isn't Rohit). One `feed_candidates` row per player and story, kind
  `article`, known by its address with tracking parameters dropped, so two feeds listing it are one row. Only the
  headline, link, time and the feed's own description (cut to 300 characters) are kept, never the story. The same
  judge reads them; `rank.py` scores a story by the judge's interest and how many outlets ran it (in place of
  views and likes), and of a player's stories about the same thing within a day (similar words in headline and
  description) only the best is served, with "N outlets". Served stories are the Feed's news in place of the card's
  (the research agent's) while there are any; the curator also writes them onto the card as `stories` after each
  run (only where they changed, version bumped and published like any card), so the player page's news section
  shows them wherever the card comes from (page, stream, follow list); the agent's `news` stays underneath. The
  Wire shows each followed player's best story of the last three days beside the agent's injury lines. A weekly prune (Mondays) drops stories over 30 days old but those an admin approved or pinned; removing a
  player removes their rows. `python -m sports_follow.curator news` reads every feed once, judges and ranks, and
  prints each sport's counts. The admin panel's Sources tab turns the source off like any other.
- **Whether the feed gets enough** (`sports_follow/feedstats.py`, BHA-326) is measured by the curator, by sport and
  stream (`channels` or `search`), never by channel or player: each step of the funnel (uploads read, matched to a
  followed player, kept, judged, passed, served; each player and video counted once, so a channel's latest uploads
  read again every poll don't count again), why what was matched wasn't kept (not embeddable, blocked in India, too
  long, too short, live, private, gone; and for a search, already stored or refused lately: units spent for
  nothing), the judge's verdicts by about_player, sponsored, risk and stage, its errors by kind (out of credit,
  timeout, invalid output, declined, unavailable, off) and its time, moment searches and the units they spend, and
  after each poll each sport's coverage: its followed players, and those with a video the Feed would serve
  published in the last 7 days. The **Feed** dashboard shows them; three alert rules mail when nothing was kept for a
  day, when the judge fails over 5 times of a kind in an hour (the OpenRouter credit spent, on 21 Oct, went unseen),
  and when a sport's followed players have had no servable video for a day. The admin panel's Feed tab gives each
  channel's yield over 30 days (uploads published, those naming someone followed, kept, served, and its latest
  upload), from sets in Redis by the day each upload was published: a channel that posts nothing, or nothing about
  anyone followed, is one to drop, and a sport whose players nobody covers needs a channel.
- **Adding or removing a sport** is one module and one line, too. Everything about a sport lives in
  `sports_follow/sports/<sport>.py`, a `Sport` subclass (`sports/base.py`): its names, scoreboard
  kinds, whether it's a team sport and whether fans can follow its teams (`follow_teams`, with a result from one
  side in its words: `side_final`), its Wikidata ids and registry rules, what its players' pages read from
  Wikidata (`profile_nation`, `profile_role` and the awards worth naming, `honours`: wiki.py), its moments (start, result,
  line-up, what happens during a game, whether a player's first line in a game polled all along is news
  (`first_line_is_news`: a cricketer's first ball) and whether a result is still to come (`result_pending`: cricket's
  summary a poll behind its "Result"), what a source takes back (`taken_back`), and which of them are a
  player's highs on Today's "This week" and in what words (`poster`: a "Five-for"), and the same off a
  finished game's line when no moment was found in it (`finished_poster`: "5/23 (4 ov)")), how big a game
  its sources list league-wide is and what its card says (`notable`: a final, "1st v 3rd", a last-over
  finish, or none, as for a cricket final whose sides are still "TBC"; for Today's big results and big games
  coming up), what a fan following a whole game hears (`game_moments`: by default its start, every named
  player's moments as `during` finds them, and its result by `verdict`; football's are the source's goals and
  cards, chess's the board turning, esports' maps and lobby matches, tennis's sets, each from the side that
  won them), what its match page shows under the board (`match_page`: a football timeline, a cricket
  scorecard, an esports series by map, a finished chess game's moves; by default the source's key events)
  and who won a finished game over its board (`verdict`: "Arsenal won", "India won by 57 runs"), what fans may call
  before a game and how its board answers it (`predicts`, `asks`, `closed`, `answer`, and `board` where one game is
  under several fixtures: a chess game's result, each map of an esports series, a football or cricket match's
  winner; predictions.py; Today's cards say the game by `board` too, so a chess board two of a fan's players are
  on is one card, and in Coming up by the id a source puts on its fixtures before there's a board to read,
  `Fixture.board`: Lichess's once a round is paired; and by `board_players` and `pairing` (which meeting of the
  players it is) alerts take its fixtures for one game, each moment from the side its `voice` names), how an Android
  notification draws a game at a glance (`glance`: its two sides' short names and scores, where it's at, and how far
  along it is, the match clock or the overs, with the goals marked), its form summary and a result's win, draw or
  loss (`outcome`: kept with the result, from the side the player played for and the one the source marks the winner,
  where its result is the game's own, as cricket's "India won by 57 runs"),
  how search shows its athletes, its colours (a player's page and link previews are drawn in them), what the Feed's
  content judge reads about it (`content_hints`: a few lines on what a milestone and a big moment are there, and what a
  promo, a rumour or a re-upload looks like to its fans; `restream_after`: how long a video from a channel nobody
  vouches for may run before it's taken for a whole game re-uploaded, or `None` where long videos are normal, as in
  chess), the only channels the feed's videos about its players come from (`video_channels`, official ones and a
  few top creators, in one block at the end of its class: real channel ids only, each checked to have an uploads playlist the Data API lists
  (`playlistItems.list` with `playlistId` `UU` and the id after its `UC`); a sport that's off has
  none read; esports' are the `Esports` class's, for every game, and each game's own, its leagues' and teams', given
  with its `Game(..., channels=...)`), the words its videos' titles leave off a team's name (`team_affixes`: "FC",
  "Team") for matching a video to a player's team, the publishers' feeds it reads for news about its
  players (`news_feeds`, right after: each feed's address with the publisher's name as stories credit it; real RSS
  or Atom feeds only, each checked to answer with headlines, links, dates and descriptions, never a search engine's
  feed of other sites; each publisher's terms for headlines with links are noted in the pull request that adds it),
  and which of its moments the feed searches for
  fans' videos of, in what words (`search_words`: a hundred and the ground, a brace and the game; `None` for the
  rest), and the words a search for one of its players' videos adds to their name (`video_words`: "cricket", a
  game's own name). Shared code
  asks the sport (`sports.get(name)`), and a sport no module knows (golf, from an AI-built page) gets the
  rules every sport shares. The one line is its place in `sports.ALL`, the order the app lists them;
  its sources each declare the `sports` they serve. `SPORTS_FOLLOW_SPORTS` turns sports off without a
  code change, and the admin panel without a restart. `tests/test_sports.py` fails if shared code names a sport.
  Its look in the web app is `web/src/sports/<sport>.tsx` and its facts, `web/src/sports/facts/<sport>.ts`, and
  whatever else of its own sits beside them under its name: chess's analysis board (`chessAnalysis.tsx`,
  `chessEngine.ts`, `facts/chessLine.ts`, `facts/chessEngineCore.ts`) and the engine it runs in the fan's browser
  (`web/src/sports/engine/`, with chess.js in `web/package.json`). In the Android app it's `mobile/src/sports/<sport>.tsx`,
  and chess's board and engine there (`chessAnalysis.tsx`, `chessEngine.tsx`, the module `mobile/modules/chess-engine`,
  chess.js in `mobile/package.json`). Removing chess removes those, and the Caddyfile's `'wasm-unsafe-eval'`
  can go with them.

  Sports that share their rules are a group: each esports game (Valorant, CS2, League of Legends, Dota 2,
  BGMI, Free Fire, PUBG…) is a sport of its own, with its own tile, search filter, colour and switch, and
  one `Game` entry in `sports_follow/sports/esports.py` (its key, name, other names and colour, which must
  read at 4.5:1 with its text and stand apart from every other sport's colour: `tests/test_sports.py`);
  everything else (series and lobby moments, form, alert wording, how big a game is, who's a pro on
  Wikidata) is the `Esports` class's, once for all of them. Adding a game is its entry and the source
  that covers it (its slug or wiki in that adapter's map to the game's key). The group itself
  (`sports.GROUPS`, "esports") isn't listed: `SPORTS_FOLLOW_SPORTS=esports`, or "esports" turned off in the
  admin panel before each game was a sport, stands for every game; it's the sport of a pro whose game
  can't be told (still found by search while any game runs) and of a game no source covers ("Call of
  Duty: Mobile"); and Today's big results and big games give the group one turn among the sports, which
  its games take in turn.

  The web app keeps no list of sports: it takes them from the server (`GET /api/sports`: names, the
  free-text names that are each sport, alert wording, what runs; `/api/sports.css`: colours), loaded
  before the first render and kept on the device; each sport there says the group it's drawn as (`group`:
  an esports game is drawn as esports), and the groups come apart (`groups`). How a sport is drawn is in three places, each with one
  line in its list: its facts, `web/src/sports/facts/<sport>.ts` (in `FACTS`: the scoreboard kinds it
  draws, its ground as line art behind Today's cards (`art`), how its line reads (`highlight`, `noLine`,
  `inHeadline`), its score in a few characters for a live player's pill (`short`)), shared by both apps;
  and its live board, `web/src/sports/<sport>.tsx` (in `VIEWS`, with its match page's part, `Match`, drawn
  from what its `match_page` sends) and `mobile/src/sports/<sport>.tsx` (in `VIEWS`, with its match page's
  part, `Match`). A sport without them shows the plain score and no line art. The Android app (`mobile/`)
  takes the same list, and the colours too, from `GET /api/sports` (a sport's `colour`: its block, text
  on it, softer text on it, and itself as text on ink).
- **An admin panel** at `/admin` (`sports_follow/admin.py`, `web/src/admin/`) changes the running app, with
  no restart: it turns sports (each esports game on its own) and sources off and on, sets what each source may spend (its allowance a
  minute, an hour or a day, as a new plan gives; the margin; the reserve for live games and fans; the
  fans' share; the pause after a 429) next to what was spent, by lane, and looks after accounts (find
  one by address, see the players, teams and games they follow, their calls and devices, sign them out everywhere,
  block them from signing in, delete them) and clips (those fans reported first: hide one, put it back or take it
  down; revoke a publishing key, hiding its clips too if it leaked). A change of a source's allowance made from a
  view that's since gone stale (another tab saved one) is refused, and the panel shows the newer one. What it changes is rows in `control` (`sports_follow/controls.py`), which
  every process reads again every 10 seconds; `sports.ENABLED`, `adapters.ADAPTERS` and the source
  budgets follow them. A sport turned off while its games are on is safe: the next tick puts them off
  (`structured.put_off`: no longer polled, nor LIVE on cards or phones), and once it's on again its
  players' and teams' games are read at the next tick, which brings each back as its source has it. One that
  polling comes back to (a worker down for days too) only after it's over, past the longest its sport's games run
  and half a day more (`Sport.over_by`: 12 hours, a Test's six days), tells nothing: its result is old news.
  The server's settings stay the ceiling: a sport `SPORTS_FOLLOW_SPORTS` leaves out, or a source
  `SPORTS_FOLLOW_SOURCES` leaves out or without its token, can't be turned on there. It opens
  with a password of its own, not a fan's account, and a 6-digit code from an authenticator app (TOTP,
  RFC 6238; each code works once): `python -m sports_follow.admin password` saves the password's scrypt
  hash and `python -m sports_follow.admin code` the app's key, checked against a code from the app before
  it's kept, both in `SPORTS_FOLLOW_ADMIN_PASSWORD_FILE`. Without a password the panel isn't there (404);
  without a key it asks for the password alone and says so on every page. An admin stays signed in 12
  hours, a new password or key signs every admin out, and tries are limited per address and, for wrong
  ones, across everyone. Beside the sources' allowances it shows Linear's plan and how many issues the
  project's workspace holds against the Free plan's limit (`sports_follow/linear.py`, with a read-only
  Linear key in `SPORTS_FOLLOW_LINEAR_KEY_FILE`; each API process asks Linear at most once an hour). Each sign-in is counted (`metrics.admin_sign_in`): Grafana emails when one works (with
  how many in the last 5 minutes), and when many don't. The page tells search engines not to list it (`X-Robots-Tag`), and it's its
  own chunk of the web build, so fans don't download it.
- **Adding or removing a player** is a command, `python -m sports_follow.players` (`sports_follow/players.py`),
  for whoever runs the app. `add` puts an athlete no source lists in the registry (`--sport`, `--name`,
  and any `--id system=id`, `--alias`, `--team`, `--country`, `--league`, `--born`), or pins a registry
  athlete by id so no import drops them. `remove` takes a player's page out (by page id, registry id or
  `system:id`) with everything that hangs off it, and their hot card; it shows what it will delete and
  asks first unless given `--yes`. `restore` takes a decision back, and `list` shows them. Decisions are
  rows in `athlete_override`, which every registry import respects, so they work on a running server and
  survive the weekly import; a removed athlete's ids in the sources are left out of search and refused
  for following, in every source of their sport (`remove` asks each it has no id in, by name and birth date),
  and so is a pick with their name and birth date from a source added since. `unmatch <ref> [Q…]` is for an esports pro matched to the wrong Wikidata person, so
  shown with someone else's face: it takes the match and photo back at once (off every card showing it,
  followed or not, and off the Credits page) and
  no run of `roster_wikidata` matches them again, to that person if one is named, else to anyone;
  `rematch <ref> [Q…]` takes that back, and the next weekly run may match them again. Unmatches are rows
  in `athlete_unmatch`, apart from the keep and remove decisions: a kept pro can be unmatched, and a
  removed one stays unmatched, so restoring them doesn't bring the face back. `socials <ref> x=handle
  instagram=handle …` gives a followed player's page their own accounts (`player.socials`), in place of what it
  had, for the Feed's judge to know a share is theirs; with no handles it shows them, and `--clear` takes them off.
- **Adding or removing a team** is a command too, `python -m sports_follow.teams` (`sports_follow/teams.py`).
  `import [system]` reads the running sources' lists now (the worker does it weekly); `list [name]` shows the
  registry's teams with their ids, and what's been decided. `add <system> <team id>` reads a team the lists
  don't have from its source and keeps it, so no import drops it; `remove <id or system:team id>` takes a team
  out with everything that hangs off it (its follows, its games' links and results; the games stay, they're
  everyone's) and keeps it out; `restore <system> <team id>` takes a decision back. Decisions are rows in
  `team_override`, which every import respects. A team no import has listed for three weeks that nobody
  follows goes by itself; a followed one stays.
- **Starter players**, who a new fan is offered in each sport (the welcome, an empty Today, search's
  "Try"), find themselves, the same way for every sport (`sports_follow/starters.py`): who's playing
  now or soon, worked out as asked from the live cards; the most followed by signed-in fans (at least two:
  a cookie's follow is its own, and an inbox is one fan however many plus-addresses' accounts it has made), counted as asked; and the
  best known (Wikipedia editions, the home country's counting double: `SPORTS_FOLLOW_STARTERS_HOME_COUNTRY`)
  with a game soon, which the worker finds in their sources once a day. The operator can pin a few
  ahead of them with `players starter add <registry id>` (it checks the source has them, and keeps
  them in the registry), `starter remove` (they stay kept; `restore` lets them go) and `starter list`;
  `starter refresh` builds the best-known pool now. A sport that's turned off offers none, without research
  a page no live source has isn't offered, and removing a player takes their pin with them, and takes them off
  every campaign page.
- **The Feed's content judge** (`sports_follow/curate.py`, BHA-297) decides whether a share about a player (an
  unofficial video, a news headline, the player's own post) is fit to show, before any fan sees it. Given a
  `Candidate` (what was found, who posted it, and who it was found for, with their own accounts) it returns a
  `Verdict`: whether it's about the player (`subject`, `mention`, `no`), who made it, what kind of thing it is (a
  match moment, a milestone, an interview or analysis, news, a rumour, a promo…), how interesting it is (0 to 1),
  its risks (betting, misinformation, deepfake, abuse, restream), whether it's sponsored, and one line a fan reads
  under "Why this is here". Rules decide alone when they're sure: a paid-partnership flag, an ad hashtag or a
  discount code (or YouTube's "Includes paid promotion") is a sponsored promo; a betting or fantasy brand (not one in the player's own team name) is ruled
  out; a video that can't play here, or a full-length one from a channel nobody vouches for, is a restream. They
  also settle that a post from one of the player's own handles is theirs, and that nothing else is. Everything else goes to a small model
  through the gateway (`SPORTS_FOLLOW_CURATE_MODEL`), with the sport's `content_hints`, and what the rules settled
  stands over what it says. The judge is a pure function, with no database and no fetching, and `judge_many`
  runs the model's calls side by side; a candidate the model can't judge now (or with `SPORTS_FOLLOW_CURATE_AI=0`)
  is left unjudged, to try again: at the next run when the gateway was down or out of credit, but days later when
  the model declined it or answered off the schema (2 days, then 4), and after 3 such tries it's held back for good
  (`rank.JUDGE_TRIES`: a verdict whose stage is `held`, never served unless an admin approves it), so no row is
  paid for at every run. `feed_candidates` keeps each row's tries, last failure and when it may be asked again
  (`judge_tries`, `judge_error`, `judge_after`); `curator rescore --rejudge` asks every row again. To tune it, label candidates by hand and run
  `python -m sports_follow.curate candidates.jsonl --labels labels.jsonl`: it prints the verdicts as JSONL and how
  often each field agrees with the labels, then each disagreement (`--rules-only` makes no model calls).
  `tests/fixtures/curate_*.jsonl` are 41 made-up ones across cricket, football, chess and esports, among them a
  channel's own boilerplate (subscribe and membership calls, socials, its own merch, academy, app or podcast, a
  sponsor's link under a video about something else), which isn't sponsorship, beside real sponsorship.
- **Renaming the app** is one line: `sports_follow/app.json` holds the name fans see ("Slam"). The API
  reads it (`config.APP_NAME`: its error messages, the sign-in email, the Credits notice, link previews and
  their pictures, and `GET /api/config`), the web build fills it into the page title, the manifest, the
  service worker and the app's code (`web/vite.config.ts`, `web/src/app.ts`), and `mobile/app.config.ts`
  gives it to the Android app, whose code reads it from Expo's config; `mobile/babel.config.js` fills it
  into the shared `web/src/app.ts` the app loads, as Vite does. The logo writes it in lower case, as the
  badge's "s" (`.brand` in `web/src/styles.css`, the share picture's band in `share.py`, and the app's Today
  head and campaign page). `tests/test_app_name.py` fails if any of them writes the name out itself, or if
  the app loads shared code that reads something only Vite fills in. Before the first Play upload, the
  Android app's package id and its own link's scheme go with the name (`mobile/app.config.ts`; the Kotlin
  module says the scheme again, in `Cards.kt`; then `npx expo prebuild --platform android --clean` once, as a
  plain prebuild keeps the old Firebase setup); afterwards the package id can't change. The domain is a
  setting of its own: the server's `DOMAIN` (`deploy/compose.yaml`) and the mail addresses in
  `deploy/settings.env`, and in the app `SITE` in `app.config.ts`, `src/api.ts`'s default and the Kotlin
  module's (`Settings.kt`), which the same test keeps in step. Play's listing writes the name out on purpose,
  as a store name that says what the app is: rename it in `mobile/store/listing.md` (30 characters at most) and
  `closed-testing.md`'s message, and draw the feature graphic again (`mobile/store/make_graphics.py`, which
  reads `app.json`). Internal names keep the old ones, as
  `sports_follow` does: the notifications module (`mobile/modules/stann-notifications`, `io.stann.notifications`,
  its `stann_*` resources) and the app's own storage keys (`stann.*`), which no fan sees.
- CI (`.github/workflows/ci.yml`) runs on every pull request: the tests against a real Postgres, so none
  are skipped (no test needs Stockfish: the engine's tests talk to stand-in engines); one migration head, `alembic upgrade head` on an empty
  database and `alembic check`; the web app's type-check and build; and the Android app's type-check
  (route names are checked only locally, where Expo has generated their types). In `web/src`,
  `tests/test_web_names.py` lets only a sport's view name a sport, and holds the files that still name a
  source (its system, or the name a page credits it by) to how many times they do now, so they can lose
  those names but not gain them.

## Run it

Needs Docker, Python 3.11+, Node 20.18+, and the [llm-providers](https://github.com/Codewiz2898/llm-providers)
gateway running on 127.0.0.1:8787 with a `sports-follow` app token.

```bash
docker compose up -d                                   # Postgres :5433, Redis :6380
brew install stockfish                                 # chess swings (optional; apt install stockfish on Linux)
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/alembic upgrade head
.venv/bin/python -m sports_follow.registry     # the player registry; ~90 min, then weekly by the worker
.venv/bin/python -m sports_follow.notify keys  # push keys, once (kept in ~/.config/sports-follow)
cd web && npm install && cd ..
```

Then three processes:

```bash
.venv/bin/uvicorn sports_follow.server:app --port 8421 --reload --timeout-graceful-shutdown 3   # API
.venv/bin/arq sports_follow.worker.WorkerSettings                  # pipeline worker
cd web && npm run dev                                              # app on http://localhost:5173
```

and a fourth for the feed's videos, with a YouTube key (`~/.config/sports-follow/youtube-key`, or
`SPORTS_FOLLOW_YOUTUBE_KEY`): `.venv/bin/python -m sports_follow.curator`.

The checks CI runs on every pull request, after `.venv/bin/pip install -r requirements-dev.txt`:

```bash
.venv/bin/python -m pytest -n 4                             # four workers, each on its own copy of the test database
.venv/bin/ruff check .                                      # lint: rules that find bugs (pyproject.toml)
.venv/bin/basedpyright --pythonpath .venv/bin/python       # types: fails on errors not in .basedpyright/baseline.json
cd web && npm run build                                     # type-check and build the app
cd mobile && npm run typecheck                              # type-check the Android app
```

The database tests run with `SPORTS_FOLLOW_TEST_DATABASE_URL` set to a throwaway database at alembic head
(they empty its tables). Under `-n`, each worker makes a copy of it for the run (`<name>_<run>_gw0`, …)
and drops it at the end; nothing may be connected to that database while the copies are made.

The type baseline holds the errors from before CI checked types. After fixing some, run basedpyright
with `--writebaseline` to shrink it; never add to it to get a pull request through.

For a single-process deploy, `npm run build` in `web/` and the API serves the built app itself (with the
service worker, manifest and icons; try installing it from http://localhost:8421). Notifications need
that build too: turn them on under Alerts. Always pass
`--timeout-graceful-shutdown`: fans' live streams never close on their own, so without it a restart waits forever.

| Env var | Default | |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://sports:sports@127.0.0.1:5433/sports_follow` | |
| `REDIS_URL` | `redis://127.0.0.1:6380/0` | |
| `SPORTS_FOLLOW_CACHE_REDIS_URL` | `REDIS_URL` | where the data sources' answers are cached (`adapters/cache.py`), shared by the API and the worker and kept across deploys. In production a Redis of its own, capped and evicting the least recently used key (deploy/compose.yaml); locally, `docker compose --profile cache up -d` and `redis://127.0.0.1:6382/0` run it as production does |
| `SPORTS_FOLLOW_MODEL` | `openrouter:moonshotai/kimi-k2.6:nitro` | any gateway model ref, e.g. `openrouter:anthropic/claude-opus-5` |
| `SPORTS_FOLLOW_EFFORT` | `medium` | `anthropic:` route only |
| `SPORTS_FOLLOW_CARD_MAX_AGE` | `21600` | seconds before a page the research agent writes whole (a player no live source has, with `SPORTS_FOLLOW_RESEARCH=1`) is researched again, when a fan opens it |
| `SPORTS_FOLLOW_LIVE_INTERVAL` | `60` | seconds between agent live checks during a game (sports without an adapter) |
| `SPORTS_FOLLOW_FIXTURES_MAX_AGE` | `1800` | seconds before a bound player's fixtures and stats are re-read |
| `SPORTS_FOLLOW_POLL_SESSION` | `600` | seconds one live-poll job or lane runs before handing over |
| `SPORTS_FOLLOW_ARM_BEFORE` | `1800` | seconds before its start that a game is armed and polled; a polled game of a sport whose team sheets come out earlier is armed sooner (`Sport.team_sheet_before`) |
| `SPORTS_FOLLOW_KEEP_TIMELINE_DAYS` | `14` | days a finished game keeps every poll's state and line before the daily prune cuts it to what's read |
| `SPORTS_FOLLOW_KEEP_MOMENTS_DAYS` | `90` | days moments and the push log are kept; never fewer than the activity list's 30 |
| `LLM_GATEWAY_URL` | `http://127.0.0.1:8787` | |
| `SPORTS_FOLLOW_GATEWAY_TOKEN` | from `~/.config/llm-providers/keys.env` | this app's gateway token |
| `SPORTS_FOLLOW_VAPID_KEY` | `~/.config/sports-follow/vapid-private.pem` | push signing key; never in the repo |
| `SPORTS_FOLLOW_FCM_KEY_FILE` | `~/.config/sports-follow/fcm-service-account.json` | the Android app's Firebase service-account key (JSON), for its notifications; without it they're off ("Android app", below) |
| `SPORTS_FOLLOW_COOKIE_SECRET` | from `~/.config/sports-follow/cookie-secret`, made on first start | the key fan cookies are signed with (at least 32 characters; `SPORTS_FOLLOW_COOKIE_SECRET_FILE` names another file). Every API process needs the same one, and it must never change: under a new key every fan starts over |
| `SPORTS_FOLLOW_RESEND_KEY` | from `SPORTS_FOLLOW_RESEND_KEY_FILE`, else `~/.config/sports-follow/resend-key` | Resend API key for sign-in emails; without it (and `MAIL_FROM`) email sign-in is off |
| `SPORTS_FOLLOW_MAIL_FROM` | none | the address sign-in emails come from, e.g. `signin@your-domain` (a domain verified in Resend); they're sent from the app's name at that address |
| `SPORTS_FOLLOW_LOG_FORMAT` | `text` | `json` writes each log line as one JSON object on stdout, for a server's log collector (the production setting); every line from a request or a job carries its id (`rid`, also in each answer's `X-Request-ID`), and fans appear only as a hash |
| `SPORTS_FOLLOW_LOG_LEVEL` | `INFO` | the lowest level of this app's own lines that is logged (other libraries' lines from `WARNING`) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | none: nothing leaves the server | where traces and log lines go over OTLP (HTTP), e.g. a Grafana Cloud OTLP gateway; OpenTelemetry's own setting, so any backend works (`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` or `_LOGS_ENDPOINT` for one alone). Each API request, worker job and live-poll round is a span, with its source requests, queries, Redis commands and model calls; a request and the jobs it queues are one trace; each log line goes too, with its request's or job's fields and its trace. On the server it's in `otlp.env` (deploy/README.md, "Traces and logs") |
| `OTEL_EXPORTER_OTLP_HEADERS` | none | the backend's key, e.g. `Authorization=Basic%20…`: a secret, so in the server's `otlp.env`, never `settings.env` |
| `OTEL_TRACES_EXPORTER` | `otlp` | `console` prints spans instead, for development; `none` turns traces off |
| `OTEL_LOGS_EXPORTER` | `otlp` | `none` keeps log lines on stdout only, while traces still go |
| `OTEL_METRICS_EXPORTER` | `otlp` | `none` sends no metrics, while traces and logs still go. Metrics: API requests by route, worker jobs, pushes, moments, card builds and refreshes, source requests by host; and from the worker, every minute, live games and how long since each was polled, the job queue, cards by status, each source's allowance (`metrics.py`); and from the curator, the feed's funnel, judge and coverage (`feedstats.py`) |
| `OTEL_METRIC_EXPORT_INTERVAL` | `60000` | milliseconds between metric exports |
| `OTEL_TRACES_SAMPLER`, `OTEL_TRACES_SAMPLER_ARG` | every trace | e.g. `parentbased_traceidratio` and `0.2` to keep a fifth; a job keeps its request's choice |
| `OTEL_SERVICE_NAME` | `sports-follow-api`, `sports-follow-worker` | the process's name in the traces (`OTEL_RESOURCE_ATTRIBUTES` adds more, e.g. `deployment.environment.name=production`) |
| `SPORTS_FOLLOW_MAIL_LOG` | `0` | `1` logs sign-in codes instead of emailing them, for development; never over HTTPS |
| `SPORTS_FOLLOW_MAIL_CODES_PER_DAY` | `100` | sign-in codes a UTC day to everyone, the mail plan's daily allowance (Resend's free plan: 100); one client address (an IPv4 address, an IPv6 /64) gets at most 20 a day; 30% is kept for accounts at least a day old, and of the rest, for new addresses, an IPv6 /48's /64s together get half at most (and 200); an inbox (plus-addresses together) gets 5 an hour |
| `SPORTS_FOLLOW_GOOGLE_CLIENT_ID` | none | the Google OAuth web client for "Continue with Google"; redirect URI `<origin>/api/auth/google/callback` (locally `http://localhost:5173/...`) |
| `SPORTS_FOLLOW_GOOGLE_CLIENT_SECRET` | from `SPORTS_FOLLOW_GOOGLE_CLIENT_SECRET_FILE`, else `~/.config/sports-follow/google-client-secret` | that client's secret; without it the Google button is hidden |
| `SPORTS_FOLLOW_ADMIN_PASSWORD_FILE` | `~/.config/sports-follow/admin-password` | the admin panel's password hash and authenticator key, written by `python -m sports_follow.admin password` and `code`; without it there's no panel |
| `SPORTS_FOLLOW_LINEAR_KEY_FILE` | `~/.config/sports-follow/linear-key` | a Linear personal API key with the Read permission alone, for the admin panel's Linear plan and issue count (deploy/README.md); without it the panel says how to add one |
| `SPORTS_FOLLOW_LINEAR_FREE_ISSUES` | `250` | the issues Linear's Free plan holds (not counting archived ones), which the panel counts against; Linear's API doesn't say it |
| `SPORTS_FOLLOW_CONTACT_EMAIL` | none | where fans write about their data, shown on the privacy page (no Questions part without it) and in the user agent data sources see |
| `SPORTS_FOLLOW_SITE_URL` | none | the app's public address; compose sets `https://$DOMAIN`. Push services and data sources are told it, and link previews' addresses are on it (without it, on the address each request came to) |
| `SPORTS_FOLLOW_API_DOCS` | off | `1` serves FastAPI's own docs (`/docs`, `/redoc`, `/openapi.json`) on a development server; they list every route, the admin panel's too, so never on the site |
| `SPORTS_FOLLOW_COMMIT` | none | the commit the image was built from, which `/api/health` says; `deploy.sh` passes it to the build, so set it by hand only to test |
| `SPORTS_FOLLOW_VAPID_SUBJECT` | the site address, else `https://localhost` | the contact push services see; never a person's address |
| `SPORTS_FOLLOW_STOCKFISH` | `stockfish` on the PATH | chess engine; without it, no chess swings |
| `SPORTS_FOLLOW_ENGINE_BUSY` | `0.5` | the share of one CPU core the engine may use |
| `SPORTS_FOLLOW_ENGINE_NODES` | `500000` | work for a position's first look, which alerts go by (about a second on one core, depth about 20); the same nodes give the same score on one Stockfish version |
| `SPORTS_FOLLOW_ENGINE_DEEPEN` | `watched` | when a position is deepened past its first look: `watched`, while someone has the game's match page open (web or Android); `all`, whenever its game is evaluated; `off`, never. Alerts go by first looks either way; an unknown value is taken as `watched`, with a warning |
| `SPORTS_FOLLOW_ENGINE_DEPTH` | `30` | a position a game stays on is deepened until it's this deep… |
| `SPORTS_FOLLOW_ENGINE_POSITION_NODES` | `32000000` | …or has had this much work in all (about a minute on one core): the most CPU one position gets |
| `SPORTS_FOLLOW_ENGINE_HASH_MB` | `128` | the deep search's hash table, which holds its tree. Each Stockfish process takes about 250 MB besides: the lane runs one for first looks (16 MB hash) and, while something's deepened, one for deep searches |
| `SPORTS_FOLLOW_ENGINE_GAME_INTERVAL` | `20` | seconds between first looks at one game's positions, from when its polls asked for the last one (deepening doesn't count) |
| `SPORTS_FOLLOW_HISTORY_READS` | `20` | source requests a minute for older results and scorecards |
| `SPORTS_FOLLOW_SPORTS` | every sport | the sports that may run, e.g. `football,cricket,chess`: a sport left out isn't searched, imported or polled, its sources don't run (a source of several esports games reads only those left in), and its followed players' pages stay as they are. Each esports game is a sport (`valorant,cs2,lol,dota2,bgmi,freefire,pubgmobile,pubg,mlbb,hok,r6siege,overwatch,apex,rocketleague,cod,wildrift`), and `esports` stands for every game. The admin panel turns these off and on while the app runs |
| `SPORTS_FOLLOW_SOURCES` | every source | the sources that may run; a public build lists only licensed ones, e.g. `lichess_chess,sportmonks_football,sportmonks_cricket`; ESPN's unofficial endpoints are for development only. The admin panel turns these off and on while the app runs, never one left out here |
| `SPORTS_FOLLOW_RESEARCH_PER_HOUR` | `10` | AI researches an hour across all fans |
| `SPORTS_FOLLOW_BACKGROUNDS_PER_HOUR` | `6` | backgrounds the research agent writes an hour across all players (a player a live source has and Wikipedia has no article on, new or a month old); past it a page waits for a fan to open it in a later hour |
| `SPORTS_FOLLOW_SOURCE_LOOKUPS_PER_MINUTE` | `30` | previews and follows a minute from one fan that ask a live source for an athlete not on the app yet |
| `SPORTS_FOLLOW_SOURCE_LOOKUPS_PER_ADDRESS` | `120` | the same from one client address (an IPv6 /64), higher since many fans can share one; not counted for private addresses |
| `SPORTS_FOLLOW_STARTERS_HOME_COUNTRY` | `India` | whose athletes count double among the best-known starters new fans are offered (empty: nobody's) |
| `SPORTS_FOLLOW_FEED_MOMENT_SEARCHES` | `40` | moment searches in any 24 hours (`searches.py`), each run counted (a moment's search runs twice; each 1 of YouTube's 100 searches a day); the biggest moments first; `0` turns them off |
| `SPORTS_FOLLOW_FEED_GAP_SEARCHES` | `20` | gap searches a day, counted over the last 23 hours so a daily run a little early still has its own (`sweeps.py`): a followed player with nothing to watch this week, the most followed first; each 1 of YouTube's 100 searches a day; `0` turns them off |
| `SPORTS_FOLLOW_FEED_WIDE_SEARCHES` | `14` | wide-sweep searches a day, the last 23 hours (`sweeps.py`): every followed player's most watched videos of the last six months, each player at most weekly, the longest unswept first; each 1 of YouTube's 100 searches a day; `0` turns it off |
| `SPORTS_FOLLOW_FEED_FAMOUS_SEARCHES` | `35` | famous-sweep searches a week, the last 7 days less an hour (`sweeps.py`), a seventh of them a day: a followed player's most watched videos ever, each player at most monthly; `0` turns it off |
| `SPORTS_FOLLOW_FEED_POOL_SIZE` | `50` | the feed's pool of top players a sport (`feedpool.py`), and that many for each running esports game: fetched for besides followed players, for For you; `0` turns the pool off |
| `SPORTS_FOLLOW_FEED_POOL_ADDS` | `40` | pool players made a day at most, each found in their sport's live source as a follow from search would (a request or a few each) |
| `SPORTS_FOLLOW_FEED_POOL_SEARCHES` | `10` | pool-sweep searches a day, the last 23 hours (`sweeps.py`): a pool player nobody follows, their last 60 days, each at most fortnightly; `0` turns it off |
| `SPORTS_FOLLOW_FEED_COUNTRY` | `IN` | where the feed's fans watch from (ISO 3166-1 alpha-2): a video that doesn't play there isn't kept |
| `SPORTS_FOLLOW_FEED_ZONES` | `Asia/Kolkata,Asia/Calcutta` with `IN`, else empty | the time zones a device in `SPORTS_FOLLOW_FEED_COUNTRY` says it's in, comma-separated: only an app in one of them helps take a "won't play" video from every fan's Feed (it always leaves that fan's); empty, every app does |
| `SPORTS_FOLLOW_FEED_LAYOUT` | `edge` | how the apps draw the Feed tab: `edge` (posts edge to edge) or `full` (a video a screen); the admin panel's Feed tab changes it live, and every page of the Feed and `/api/config` say which |
| `SPORTS_FOLLOW_CLIPS_ENDPOINT` | none | the clips bucket's S3 endpoint (`https://<account>.r2.cloudflarestorage.com`); clips are off without it and the next three |
| `SPORTS_FOLLOW_CLIPS_BUCKET` | none | the clips bucket's name |
| `SPORTS_FOLLOW_CLIPS_PUBLIC_BASE` | none | where fans fetch clips from: the CDN domain on the bucket (`https://clips.example.com`) |
| `SPORTS_FOLLOW_CLIPS_KEY_FILE` | `~/.config/sports-follow/clips-key` | the bucket's access key id, then its secret, a line each |
| `SPORTS_FOLLOW_CLIPS_REGION` | `auto` | the region the bucket's URLs are signed for: R2's `auto`, or an AWS region |
| `SPORTS_FOLLOW_RESEARCH_PER_ADDRESS` | `10` | AI researches a UTC day from one client address (an IPv6 /64; an IPv6 /48's /64s together get four times it); not counted for private addresses |
| `SPORTS_FOLLOW_AGENT` | `1` | `0` runs without the research agent, as with no OpenRouter key: fans follow athletes their sources have (from the registry and search), those pages come from the sources (scores, games, stats, live and alerts) with their profile from Wikidata and Wikipedia and no news, nothing offers AI research, and pages the agent built before stay as they were, no longer updated |
| `SPORTS_FOLLOW_RESEARCH` | `0` | `1` gives players no live source has a page the research agent writes whole: a typed name no source knows, a registry athlete no source has, offered as "AI research" (3 a fan a day). Off, they can't be followed, search doesn't list them, and nothing offers AI research; the agent only writes the background of a player Wikipedia has no article on |
| `SPORTS_FOLLOW_AGENT_LIVE` | `0` | `1` lets the AI agent check live scores for sports no source covers (costs model calls every minute of a game); only with `SPORTS_FOLLOW_RESEARCH=1` |
| `SPORTS_FOLLOW_CURATE_MODEL` | `openrouter:anthropic/claude-haiku-5.5` | the Feed's content judge's model (`curate.py`): small and cheap, about $0.0004 a verdict; any gateway model ref |
| `SPORTS_FOLLOW_CURATE_AI` | `1` | `0` judges the Feed's shares by the rules alone: what they can't settle waits unjudged and isn't shown; off with the agent too |
| `SPORTS_FOLLOW_LICHESS_TOKEN` | from `~/.config/sports-follow/lichess-token` | Lichess API token (scope `study:read`); broadcasts need it |
| `SPORTS_FOLLOW_SPORTMONKS_TOKEN` | from `~/.config/sports-follow/sportmonks-token` | Sportmonks API token (football and cricket); with one, football reads Sportmonks instead of ESPN and followed footballers move over on their next refresh. Cricket stays on ESPN in a development build until the plan covers ODIs, Tests and the IPL; a build that lists `sportmonks_cricket` without `espn_cricket` uses it |
| `SPORTS_FOLLOW_PANDASCORE_TOKEN` | from `~/.config/sports-follow/pandascore-token` | PandaScore API token (esports; the free plan is enough); without one, esports players get no page (AI-built ones with `SPORTS_FOLLOW_RESEARCH=1`) |
| `SPORTS_FOLLOW_PANDASCORE_RESERVE` | `300` | PandaScore requests an hour (of 1,000) kept for live series and fans; refreshes, the history lane and the registry import stop below it |
| `SPORTS_FOLLOW_ESPORTS_GAMES` | every team title | the titles whose pros the registry imports (PandaScore slugs: `valorant,cs-go,league-of-legends,dota-2,mlbb,kog,r6-siege,ow,rl,cod-mw,lol-wild-rift`; `cs-go` is CS2, `kog` Honor of Kings), of the games that run. Naming a few imports just those, and removes only their stale players |
| `SPORTS_FOLLOW_OPENDOTA` | `1` | `0` turns off OpenDota's Dota 2 map data (kills, clock, gold lead live; each player's hero and numbers after each map, and on the match page the live map's gold line, buildings and heroes and each finished map's gold by minute, moments, items and draft), laid over PandaScore's Dota 2 series; no key, 60 requests a minute and 3,000 a day |
| `SPORTS_FOLLOW_PUBG_TOKEN` | from `~/.config/sports-follow/pubg-token` | PUBG developer API key (PUBG esports); the worker indexes recent tournaments every 10 minutes |
| `SPORTS_FOLLOW_YOUTUBE_KEY` | from `~/.config/sports-follow/youtube-key` (or `SPORTS_FOLLOW_YOUTUBE_KEY_FILE`) | YouTube Data API v3 key: the feed's videos, read by the curator; without it the source is off |
| `SPORTS_FOLLOW_LIQUIPEDIA_TOKEN` | from `~/.config/sports-follow/liquipedia-token` | LiquipediaDB API key (battle royale esports: BGMI, Free Fire); the free key is for non-commercial use only |
| `SPORTS_FOLLOW_LIQUIPEDIA_HOURLY` | `60` | the key's requests an hour; counted in Redis across processes (each counts its own while Redis is down) and never overspent (a paid plan raises it) |
| `SPORTS_FOLLOW_LIQUIPEDIA_RESERVE` | `30` | of those, kept for live lobbies and fans; refreshes, the history lane and the registry import stop below it |
| `SPORTS_FOLLOW_LIQUIPEDIA_WIKIS` | `pubgmobile,freefire,apexlegends` | the battle royale wikis whose active players the registry imports |
| `SPORTS_FOLLOW_SPORTMONKS_RESERVE` | `1000` | Sportmonks requests an hour kept for live matches, of the 2,000 that football allows per entity and cricket per endpoint, and for fans; refreshes and the history lane stop below it |

## Deploy

One server runs the whole app with Docker Compose, with HTTPS from Caddy: see
[deploy/README.md](deploy/README.md). The public build runs licensed sources, plus OpenDota by choice (BHA-208); never a development-only one (ESPN).

## Mac app

`desktop/mac/` is Slam for Mac (BHA-366): a menu-bar app that records a game's window (Dota 2), keeps the last
30 s, cuts a moment into a clip with Slam's overlays and end card on ⌃⇧S, and publishes it to Slam (the clips
API, BHA-364). It builds with the Command Line Tools alone (`./build.sh`, `swift test`); its README has the flow,
the publishing key and the developer tools. For Bharath's Mac only for now (ad-hoc signed).

## Android app

`mobile/` is the Android app (package `io.slam.app`; `io.stann.app` before the rename to Slam): React
Native with Expo, in TypeScript like the web app. It looks like the site on a phone, page for page: each
of the web app's pages and blocks has its React Native twin in `mobile/src`, drawn from `web/src/styles.css`'s values (`mobile/src/theme.ts`), with
the same fonts, icons and line art. The display type leans as the site's does, but Android won't skew text,
so the lean is in the letters: `mobile/assets/fonts/AntonSlanted.ttf`, a copy of Anton that
`mobile/scripts/slant_font.py` makes (run it again only if Anton changes). It asks the same API (stann.in,
or `EXPO_PUBLIC_API_URL` when Metro bundles it) and is the same fan, by the same cookie, or the same account
once signed in.

What the pages decide is shared with the web app, not copied: the API's calls and types
(`web/src/endpoints.ts`, `types.ts`), what Today shows and how cards word a game (`today.ts`,
`lines.ts`), Today's game chips and what each leaves on the page (`filter.ts`), search's sections
(`search.ts`), alert levels (`alerts.ts`), dates and names (`format.ts`, `follow.ts`), what a Share button
shares (`share.ts`), what a campaign page offers and when its games are (`campaign.ts`), what a match page's
head shows and when it looks again (`match.ts`), what a player's Feed shows and how its cards say when
(`feed.ts`), and each sport's facts
(`sports/facts/`: the scoreboard kinds it draws, its line art, how it words a player's line, what its match
page's part says). Only the drawing is each app's own: a sport's live board is
`web/src/sports/<sport>.tsx` on the web and `mobile/src/sports/<sport>.tsx` in the app. Like the web app,
the app takes the sports, their names and their colours from `GET /api/sports`:
`tests/test_web_names.py` holds `mobile/src` to naming no sport and no source. The name is
`sports_follow/app.json`'s and the icons are the web's (`mobile/app.config.ts`).

Every page is there: Today (with the welcome for a new fan), Live now, Search and an athlete's preview, a
player's page with its tabs, a game's match page (with Follow for the game itself), Following, Alerts,
Account (sign-in by email code), Credits, Privacy, Terms and a campaign's page (stann.in/gukesh, without the web's
"install the app" part). Where the web listens to the fan stream, the app reads again every 30 s for now
(BHA-231). A match page's Watch live plays Twitch, Kick and YouTube in a WebView (`react-native-webview`), in the
page or, expanded, turned sideways (`expo-screen-orientation`), so a build from before those two (BHA-345) has to be rebuilt. A server
from before `/watch/` answers it with the web app's own page, so the server goes out before an app build that
plays streams. Notifications come through
FCM (below), drawn by the app, with a live card on the lock screen; a home-screen widget follows. The native project
(`mobile/android`) is generated by Expo and not committed.

**A chess game's Play from here** is the web's analysis board on the phone (`mobile/src/sports/chessAnalysis.tsx`):
tap then tap or drag a piece with a finger (a swipe from anywhere but the side to move's pieces still scrolls),
and its line, rules and words are the web's (`web/src/sports/facts/chessLine.ts`, with chess.js in
`mobile/package.json`). Its engine is the site's own Stockfish files, `web/src/sports/engine/`, which the app
carries rather than downloads: the app's module `mobile/modules/chess-engine` copies them into its assets at each
build (a file missing there stops the build) and runs them in a Web Worker of a page in a hidden WebView of its
own, which WebViewAssetLoader serves that folder alone at `https://appassets.androidplatform.net/engine/`, with no
file access, no network (its Content-Security-Policy, and the WebView refuses any other address) and no
navigation. The conversation with it is the web's (`web/src/sports/facts/chessEngineCore.ts`), each position to
depth 18 from a cleared hash. The WebView opens with the board and goes with it, and while another page is over
the board. Behind another app the engine stays and only its search stops, so coming back doesn't compile it again;
if Android ended its page meanwhile (short of memory), a fresh one starts when the fan comes back. A new engine
needs a new app build, not only new JavaScript. Removing chess removes the module and those files as well.

**stann.in links open the app** when it's installed (Android App Links), at the same page: a player
shared on WhatsApp, a campaign's link in a launch post. The app claims the addresses it has a page for,
which `mobile/app.config.ts` reads from its routes (`mobile/src/app`): `/`, `/player/…`, `/match/…`,
`/search` and the rest, and any one-word address for a campaign, capitals and all, since Android matches a
path as it's typed (Android 12 and later; older phones open campaigns in the browser). Not the admin panel's
`/admin`, which is one-word too but the app has no page for: `WEB_ONLY` in `mobile/links.js` lists such
addresses, and the campaigns' patterns are spelled out around them (Android's can't say "but not"); a new one
needs a new app build. Android trusts the claim only once
`https://stann.in/.well-known/assetlinks.json` names the package and the SHA-256 fingerprint of the
certificate that signed the installed app: on the server, `SPORTS_FOLLOW_ANDROID_PACKAGE=io.slam.app` and
`SPORTS_FOLLOW_ANDROID_CERT_SHA256` with the upload key's and Play App Signing's fingerprints, comma
separated (BHA-25), never the debug key's, which anyone can sign with. A build signed with the debug key
(`npm run android`) gets the links when they're turned on by hand on the phone: the app's Android settings,
Open by default, Add link, stann.in; or over USB,
`adb shell pm set-app-links-user-selection --user cur --package io.slam.app true stann.in`.
`adb shell pm get-app-links io.slam.app` says whether Android verified the site.

Needs JDK 17 and the Android SDK (platform 36, an emulator or a phone with USB debugging):

```bash
cd mobile && npm install
export JAVA_HOME=~/.local/jdk-17/Contents/Home ANDROID_HOME=~/.local/android-sdk
npm run android                                     # builds, installs and opens it on the emulator or phone
EXPO_PUBLIC_API_URL=http://10.0.2.2:8421 npm start  # then Metro serves it the local stack (10.0.2.2 is this Mac)
npm run typecheck                                   # what CI checks
```

**Google Play.** `mobile/store/` holds what the Play Console asks for (the listing, graphics, the data
safety form, the closed-testing plan) and `release-build.md`, the signed `.aab`: an upload key in
`~/.config/sports-follow/`, never in the repo, that `mobile/plugins/with-upload-key.js` puts on the release
build when `SPORTS_FOLLOW_UPLOAD_KEYSTORE` names it; `versionCode` in `mobile/app.config.ts` goes up for every
upload.

**Notifications** come through Firebase Cloud Messaging, planned as the web's are (one notification a
game, replaced as its moments come; sound only for a key moment outside quiet hours). The server sends
them itself with FCM's HTTP v1 API (`sports_follow/fcm.py`), with no Expo push service in between, as data
the app draws itself (designs: https://claude.ai/artifact/6YxZ16GcGD4zSMNaJLpwX9):

- **A moment** is a scorecard in its sport's colour: collapsed, the moment's word ("Goal", "Hundred") and its
  line; opened, the block with the live tag, the player's photo, the word large, the game's board (its sport's
  `glance`) and the game's earlier moments, with Match page and Mute this game.
- **A live card** for each game the fan follows while it's on (`sports_follow/livecards.py`): its board, how far
  along it is and the fan's own players in it, sent after a poll that changed it (at once for the score, else at
  most every 20 s, and again every 10 minutes when nothing has, as the phone takes down a card nothing has come
  for in 20), ongoing and silent, and taken down at the end, and overnight (a Test at stumps: `Sport.paused`) till play
  resumes. Cards aren't collapsible FCM messages: FCM lets a
  phone have a burst of 20 of those, then one every 3 minutes, and a moment would wait behind them. On an Android 16 phone that allows it, a live
  card is a Live Update instead: first on the lock screen, with the score in the status bar. Alerts' Settings has
  a switch for them (Live scores), and each card a Stop button; the phone tells the server either, which then
  sends it none it would throw away: the switch with its registration, a stop by `PUT /api/push/fcm/stopped/<event>`
  with its token (the newest 30 games each, kept on the phone too, so a card already on its way isn't shown).

With Sport colours off in Alerts' Settings, the app draws both in the app's ink, white on it, with the icon in
the app's blue (a Live Update, which Android draws itself, has a blue bar): quieter beside other apps'
notifications. That's the phone's own choice, which the server never hears.

The drawing is the app's own Expo module, `mobile/modules/stann-notifications` (Kotlin): it takes every FCM
message ahead of expo-notifications, which still asks for permission and gives the token. Android can't load the
app's fonts in a notification, so the module draws the display type (Anton) as pictures, from the app's own font
files, copied in at build time. It makes three channels, "Key moments" with sound, "Other moments" without and
"Live scores"; all show on the lock screen, whole (every one is public, and none is the low "silent" kind most
phones leave off it). Android 14 and earlier bundle four or more of an app's notifications under a summary of
their plain titles unless each is in a group, so each is in its own; Android 16 bundles them all the same. It
asks to notify the first time the fan follows someone or a game (Android 13 and later); then Alerts'
Settings turns them on and off for the phone, with its quiet hours and a test alert, as on the web. While
they're on, it registers its FCM token with the API (`POST /api/push/fcm`, with its timezone, quiet
hours and `live_cards`) at every start, after a sign-in or out, whenever FCM replaces the token, and when
its quiet hours or Live scores change; a tap opens the
player's page, or for a game followed by itself, its match page, by the app's own link (`slam://player/7?n=31`,
n the notification, which the app counts as opened). Without the Firebase project's files there are no notifications and nothing else changes. To turn them
on, once:

1. In the [Firebase console](https://console.firebase.google.com), make a project and add an Android app
   with the package `io.slam.app` (the project is `stann-300ed`, which had `io.stann.app` before the rename).
2. **The app:** download its `google-services.json` to `mobile/google-services.json` (gitignored; or set
   `GOOGLE_SERVICES_JSON` to its path, `~/.config/sports-follow/google-services.json` on Bharath's Mac), then
   build again with `npm run android`. It's not a secret, but it stays out of the repo with the project it
   names. A file with no app for the package in it (one downloaded before the rename) builds without
   notifications, with a warning: download it again once the app is added, and it names both. After changing
   the file or the package, prebuild with `--clean` once (`npx expo prebuild --platform android --clean`): a
   plain prebuild never takes Firebase out of an `android/` that had it.
3. **The server:** under Project settings, Service accounts, generate a private key and save it as
   `~/.config/sports-follow/fcm-service-account.json` (`chmod 600`; `SPORTS_FOLLOW_FCM_KEY_FILE` names
   another file). Restart the API and the worker: `GET /api/push` answers `"fcm": true`. For the server,
   see deploy/README.md, "Android notifications".
4. In the app, follow a player and allow notifications. Their next moment arrives as a notification.

`android/` still holds the earlier route, a Trusted Web Activity around the site; it goes once the
native app is on Play (BHA-39). `/.well-known/assetlinks.json` names one package: set to `io.slam.app`, it's
the native app's, and the TWA loses its full screen.

## Design

- **App design** (Claude Design canvas): https://claude.ai/artifact/6qWRYFCmugDe292LKwowAB
- **Backend design**: [docs/backend-design.html](docs/backend-design.html), also at https://claude.ai/artifact/Quj8eSYYNmQPsPEbCpCNTe

## Limitations

- **ESPN's endpoints are unofficial.** They are the JSON behind espn.com and espncricinfo.com: no key,
  no contract, and they can change or be blocked without notice. Fine for development and personal
  use; a public launch should use a licensed feed, which fits in as another adapter.
- The registry's current teams are Wikidata's, which can lag a transfer by weeks; the live source's team
  is what fixtures use. Chess ratings aren't in it (the FIDE list needs FIDE's permission for commercial
  use), so chess rows show title and country.
- Photos are fetched only for followed and starter players, so the preview of another athlete shows one
  only once someone follows them. Most esports pros have none: only about one in eighty is matched to a
  Wikidata person, and fewer than half of those have a photo there; a pro whose source has no real name
  or birth date (PUBG's) is never matched. Each is the one file Wikidata names (its preferred one, when it names
  several), however old. When a photo has several faces, the profile shows the largest, which is usually
  the player's but can be someone standing nearer the camera.
- When the registry is thin, search still asks ESPN and Lichess live: ESPN's search has no typo tolerance
  and no sport filter, and Lichess allows one request at a time.
- Search reads names in Latin letters only (accents folded; "Đoković" and "mueller" also find Djokovic
  and Müller). A name in Devanagari, Tamil, Chinese or Cyrillic finds no one and can't be researched:
  the standard library has no transliteration, and none is added as a dependency.
- An anonymous fan's three AI researches a day are counted per cookie, so clearing cookies resets them
  (the per-address and hourly limits still hold); a signed-in fan's are counted per account.
- A name typed into "Research with AI" that the sources don't recognize still waits for the agent (1–4
  minutes) before the adapter is bound, because the agent is what says which sport it belongs to.
- A typed name that only a US college athlete has on ESPN ("Caitlyn Clark", for Caitlin Clark) isn't
  found, and the agent isn't sent: college players aren't followed here, so a misspelt star gets no page.
  The first try counts as one of the fan's AI researches; asking again that day answers at once and
  doesn't. After a day the sources are asked again (a college player may have turned pro).
- Cricket: ESPN serves no career stats, so the Stats tab shows recent form computed from the player's
  scorecards in the last 30 days. Domestic matches often have no scorecard, so no player line.
- Football: national-team games show up for every player of that nationality's team; a player who
  isn't called up sees "Not in the matchday squad" rather than the game being hidden, and gets no
  notification for its result. Women's players follow their club only: ESPN's search can't tell a
  women's national team from the men's.
- Tennis: ESPN has no player schedule or match summary, so matches are read from tour scoreboards;
  a player's next match appears only once the draw is made (a day or two ahead). Singles only.
- Chess: Lichess relays most elite and many open events, but not every tournament. A player's events
  are found from their Lichess FIDE page and broadcast search; Lichess rate-limits hard, so reads are
  one request a second and a rate limit on a broadcast fails the refresh instead of dropping results.
  The FIDE pages share one limit with every server in a datacenter, so they're read a few times a day
  and, when refused, the last copy stands in; FIDE records come from FIDE search, which has no limit.
- Other sports still come from the agent alone, with its generic scoreboard and events deduplicated
  by sport, title and date.
- Signing in is optional. Without it each browser or phone is its own anonymous fan (a signed cookie);
  signed in (a code sent by email, or Google), a fan's players, alert levels, muted games and devices
  are the same on every device, and what they followed before signing in comes with them. Google's
  button is hidden in the iPhone home-screen app until its sign-in sheet is tried there.
- Notifications: on iPhone, Web Push works only once the app is added to the Home Screen (iOS 16.4+),
  and there are no lock-screen live scores (Live Activities); the Android app has them, but a web page can't.
