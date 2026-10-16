# Sports Follow

Follow any athlete in any sport. Each followed player gets one shared page: live score and the
player's own numbers while they play, upcoming games, news, recent results and season stats. The
Today screen puts all of your players together: on a phone a row of them along the top (the live
score, or when they next play), then their live games, a row of the other games on anywhere ("Also
live", outlined, with players to follow from each), a row of every result since yesterday and a row of
every game in the next two weeks, each scrolling sideways and ending in the big games of the
competitions the sources cover that none of your players are in, outlined too (a fan who follows nobody
sees only those: "Big results", "Big games coming up"). The Following page has a tile for each player in
their sport's colour.

## How it's built

This is milestones 1 and 2 of the [backend design](docs/backend-design.html): the persistent core,
plus structured adapters for cricket and football.

```
React app (web/) ──/api──▶ FastAPI (sports_follow/server.py) ──▶ Postgres (registry, cards, history)
      ▲                         │  enqueue jobs                   Redis (hot cards, per-player channels)
      └──── SSE: card updates ──┘         ▼
                              arq worker (sports_follow/worker.py)
                                ├─ build_card: research agent → profile + news → card, then bind to an adapter
                                ├─ refresh_structured: adapter → fixtures, results, stats → card
                                ├─ poll_event: one job per live game, shared by every player in it
                                ├─ poll_lane: every live Sportmonks football match, one request a round
                                ├─ refresh_live: agent live check, for sports with no adapter
                                ├─ import_registry, weekly: Wikidata ──▶ athlete table (the player registry)
                                ├─ prune, daily: finished games' timelines, old moments and pushes, idle hot cards
                                ├─ fetch_photos, every 15 min: followed and starter players' photos from Wikimedia Commons, for their profiles
                                ├─ read_sidelined, twice a day: who's out and who's back, league-wide, for the Wire (sidelined.py)
                                └─ tick, every minute: arm events, queue polls, refreshes and rebuilds
                                         │                                  │
                              llm-providers gateway ──▶ LLM         adapters/ ──▶ ESPN JSON (football, cricket, basketball, tennis)
                                                                              ├─▶ Lichess API (chess)
                                                                              ├─▶ PandaScore API (esports: team titles)
                                                                              ├─▶ LiquipediaDB API (esports: battle royale)
                                                                              └─▶ PUBG API (esports: PUBG)
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
  Liquipedia, by handle and real name, with each source's key
  (`python -m sports_follow.registry esports`, part of the weekly run). A run by hand takes the weekly
  run's lock, so it refuses to start while that one is going; a sport (or esports source) that fails
  keeps last week's rows and the rest still run.
- **Player photos from Wikimedia Commons, on the profile only.** A player's page, and the preview of an
  athlete before following, show their Commons photo; the home page, lists and search don't (the home page
  is getting each sport's own line art instead). `sports_follow/photos.py`: every 15 minutes the worker asks
  the Commons API about the registry's photo file of each followed and starter athlete it hasn't got, 50 to
  a request, and keeps those under a licence fans may see them under (public domain, CC0, CC BY, CC BY-SA,
  GODL-India; not GFDL, Commons' generic "Attribution" or "copyrighted free use"). Their 330-pixel
  thumbnails, author, licence and file page go in the `photo` table, and the app gets them from our own
  origin (`/api/photos/<digest>.jpg`, cached a year, never hot-linked) with the card and the previews. Each
  file is asked about again every 90 days (a new upload, licence or author, or a file Commons deleted,
  which then stops showing), one that failed the next day; a file fetched over 30 days ago that nobody
  follows or is offered now is forgotten. The app recolours each photo in its sport's colour (a duotone
  from the sport's `deep` colour, or the text on it, to its block colour: `sports.photo_tones`), so its
  credit says so: "Photo: Bryan Berlin, CC BY-SA 4.0, Wikimedia Commons, recoloured", under the photo,
  linking to the file's page. The Credits page lists them all. Players without a photo show none. Never a
  data provider's photos (their terms don't license them), and never generated likenesses.
  The job also finds the face in each new thumbnail, once (`sports_follow/faces.py`: OpenCV's YuNet
  detector, a 230 KB MIT model in `sports_follow/data/`, a few milliseconds a photo, loaded by the worker
  only), and keeps its box on the photo row. The app gets the head-and-shoulders square around it
  (`crop`) with the photo, and the profile shows that square; a photo with no face found is shown from
  near its top.
  `python -m sports_follow.photos` runs the job once.
- **Search answers from the registry.** `GET /api/search` (`sports_follow/search.py`) reads Postgres only:
  players on Sports Follow, then registry athletes by popularity, with a trigram index for typos ("did you
  mean"). ESPN's search and Lichess's FIDE search are asked only when the registry has fewer than three
  matches. Two athletes with the typed name get a picker with birth years.
- **Following binds the exact athlete.** A registry athlete is looked up in their live source by the id
  Wikidata has, or, for most footballers (Wikidata has their Transfermarkt id, rarely ESPN's), by name
  accepted only if ESPN's birth date matches; the id found is written back, so it's by id from then on.
  The page is live in about a second and the agent, told exactly who it is, adds the profile and news.
  An athlete no live source has gets an agent-built page (counted as an AI research).
  `GET /api/athletes/{system}/{id}` and `/api/athletes/wikidata/{qid}` preview before following.
- **AI research is capped.** A name no source knows goes to the research agent, 3 per fan per UTC day
  (`search.RESEARCH_PER_DAY`, counted in Redis), 10 per client address per day and 10 an hour across all
  fans (`SPORTS_FOLLOW_RESEARCH_PER_ADDRESS`, `SPORTS_FOLLOW_RESEARCH_PER_HOUR`). Following a player
  already here, or a search result with a live source, doesn't count. The gateway's per-app budget is
  the hard ceiling behind it.
- **Names converge.** "Kohli" and "Virat Kohli" end up on the same player: every typed name becomes an
  alias, and when a build discovers the athlete already exists, the rows are merged. A player bound to a
  source is never renamed or merged by an agent report, and keeps the source's sections when a typed
  name is merged into them; a player picked in search is never merged into another by name. So a
  namesake's report can't take them over.
- **Structured sources where they exist, the agent everywhere else.** After the agent says who a
  player is, `structured.bind` looks them up in their sport's adapter (`sports_follow/adapters/`) and
  records the identity and bindings. From then on the source owns the fixtures, results, stats and live
  score; the agent keeps the profile and news. A player followed before their sport had an adapter is
  bound by the next scheduled refresh.

  | Sport | Source | Fixtures | Live | Stats |
  |---|---|---|---|---|
  | Football | ESPN | club + national team schedules | score, scorers, cards, player line | season totals |
  | Cricket | ESPN (ESPNcricinfo ids) | day-by-day calendar, filtered by squad | innings, player's batting/bowling | recent form from scorecards |
  | Basketball | ESPN (NBA, WNBA; NCAA if researched) | team schedule, every phase | score, quarters, box-score line | season averages |
  | Tennis | ESPN (ATP, WTA singles) | draws, current + 6 weeks back | set scores, serve | ranking, points, win–loss |
  | Chess | Lichess broadcasts (FIDE ids) | the player's broadcast rounds | board, clocks, match score | FIDE ratings, recent score |
  | Esports | PandaScore (team titles: Valorant, CS2, LoL, Dota 2…) | the team's series | series score, map by map (no in-game data); Dota 2 also inside the map, from OpenDota | team's series and maps won; a Dota player's kills, deaths, assists, GPM and heroes |
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
  requests an hour, not 240 per match, and one worker thread, not one per match.
- **One stream per fan.** The app holds one SSE connection for all of a fan's players; a player's page
  listens on it too, and opens that player's own stream only for someone the fan doesn't follow.
- **Installable (PWA), the first step to Android.** A manifest, icons (`web/scripts/make_icons.py`) and a
  service worker (`web/public/sw.js`, production builds only) let Chrome on Android and desktop offer
  "Install app"; the app then opens full screen from the home screen. The app shell opens offline and
  shows the last update of your players; live scores and streams always go to the network. A new
  version waits for the fan to tap Reload, so a game in progress isn't cut off. For the Play Store the
  same site is wrapped as a Trusted Web Activity; `/.well-known/assetlinks.json` publishes the app's
  package and certificate once `SPORTS_FOLLOW_ANDROID_PACKAGE` and `SPORTS_FOLLOW_ANDROID_CERT_SHA256`
  are set.
- **Links that preview.** A player's page shared in WhatsApp or X shows a card, not a bare address: the
  server writes each page's Open Graph and Twitter card tags into it (`sports_follow/share.py`), since the
  crawlers that read them run no JavaScript. A player's page says who they are and what they're doing
  now, live, last or next; a game of theirs (`/player/<id>?g=<event id>`, the link a This week poster
  shares) says its big moment in the sport's own words, as This week does (`week.game`); every other
  page says what the app is. Each has a 1200 × 630 picture drawn by the server with Pillow
  (`/api/players/<id>/share.png`, `?g=` for a game; `/api/share.png` for the app): a block of the sport's
  colour, the name and the moment's word in Anton, the lines in Manrope, the app's name in the logo's
  Fredoka (the fonts are in `sports_follow/data/fonts`, OFL). No photos, no likenesses, no league or
  team marks. Its address carries a digest of what it shows, so a crawler that caches pictures by
  address gets a new one once the score moves. Each API process keeps the 256 it drew last and draws new
  ones one at a time, so asking for many can't take the threads the database calls use. The player page
  and each This week poster have a Share button, on the web and in the Android app: the phone's share
  sheet (on the web where the browser has one, else the link copied). It shares a link on the site, never
  the app's own, so it previews for anyone; `web/src/share.ts` says which link and title, for both. Every
  link carries `s=share`, for when the app counts visits.
- **Push notifications for moments, without the noise.** Each poll compares the player's line with the
  last one (`sports_follow/moments.py`): the line-up (starts, or on the bench), a goal, a fifty, 30
  points, a set, the start and the result; news from a rebuild adds injuries, transfers and
  retirements, from stories the agent marks as breaking that news. Each moment is stored once (a
  stable key) with a priority: a hundred over a fifty, a late equaliser over a fourth goal. The noise
  rules (`sports_follow/alerts.py`) then plan each fan's
  pushes: their level for the player (key moments, the default; everything; results only; off) says
  what reaches the phone, and only key moments make a sound. Each game is one notification on the
  phone, replaced as moments come with the story so far underneath, whichever followed players are
  in it; "Mute this game" stops it. A goal VAR rules out is corrected on that notification, for the
  fans who were sent it, and told again if the source shows it again (a goal the source credits to
  another player instead is left as it was).
  Sound is rationed (once every 3 minutes a game, 6 times an hour a phone) except for the biggest
  moments. `sports_follow/notify.py` claims each batch once, logs every
  push (`notification`), sends it to each device at once (by Web Push with VAPID to browsers, by FCM to
  the Android app, `fcm.py`), and the tick sweeps up any batch a restart dropped. Quiet hours are per device and make notifications arrive silently.
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
  half of one core. A band changes only when two evaluations agree, so a board glitch isn't a swing.
- Adapter parsers are pure functions over the source's JSON, tested against recorded responses in
  `tests/fixtures/espn` (`.venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m pytest`).
- **Adding or removing a data source** is one module and one line. A source is a module in
  `sports_follow/adapters/` with an `Adapter` subclass (`adapters/base.py`) that describes itself: its
  `system`, the `sport` it serves, its `name` on a page, its `provider` (the Credits entry, shared with
  the provider's other adapters), what it `covers`, its athlete `id_pattern`, and `available()` (a
  licensed source needs its token). Optional capabilities are opt-in, so nothing else changes: a name
  `search` for the registry, a `roster` of pros for the registry (esports), a provider's live search and
  `recognise` for search as you type, a provider's `research` (its public API, which the research agent's
  prompt points to while it runs), `credit` for a live card carrying another's data too (OpenDota's),
  `snapshots` for batched live polling, `live_now` for "Live now" (every game it has on, league-wide;
  `sports_follow/live.py`; where a game's id isn't the fixture id a player's card has it under, its
  `LiveGame.fixtures` says those, as chess's boards do, so Today shows a fan's game once, as theirs; where
  the source rates its events, it leaves out the small ones and sets `LiveGame.tier`, the biggest first, as
  esports does by PandaScore's tournament tiers),
  `schedule` for Today's big results and big games coming up (its competitions' games between two days,
  whoever plays them, three days back and a fortnight on; `sports_follow/league.py`; each game ranked by its
  sport's `notable`, and a source that rates its events sets `Fixture.tier`; read a few times a day at
  most, only while fans look: Sportmonks football 3 or 4 requests a read, cricket 1, PandaScore about 6, PUBG
  none),
  `sidelined` for the Wire's facts (every player out of its competitions, injured or suspended, by team;
  `sports_follow/sidelined.py` compares each read with the last, so a new spell is news that a player is
  out and one that's over news that they're back, as their sport's `out_fact` and `back_fact` word it; read
  twice a day by the worker: Sportmonks football's 5 Team requests a read, and a League one a day),
  `live_note` and `lag_note` for a player page's words on how its live scores come in and how late they
  can be, where the app's general sentences don't fit (Liquipedia's and PUBG's standings after each match),
  `spare` for background reads, `rekey` for a source that changes
  how it names its athletes (the worker moves every player bound to it to the new ids, and the registry
  drops its rows with an id of the old shape).
  The one line is its place in `adapters.KNOWN`, which is also the order of preference when two sources
  serve a sport. Parsing that two sources of a sport share lives in `sports_follow/sports/`, so removing a
  source never breaks another. `tests/test_sources.py` fails if shared code names a source: its system,
  its provider's name or an API it reads, or its URL.
- **One rate-limit layer for every source** (`sports_follow/adapters/budget.py`). Each source's client
  declares a `BUDGET`: requests a window (an hour, a minute, a day, several at once), a margin never
  spent, a reserve kept for live games, and, for a scarce one, fans' own share. Its provider lists it
  (`Provider.budgets`), so the usage report finds it through `adapters.KNOWN`. Before each request it
  sends it takes one; it reports what the source says is left and its 429s, which pause every process.
  The research agent's `fetch_url` spends the same budget when it reads a source's API (`Budget.urls`).
  Counts are kept in Redis, so the API and the worker spend from one allowance. Every request is in a
  lane, set once where the work starts: `live` (polls), `fan` (every API request: search, previews,
  following), `refresh`, `history` and `import`. The background lanes stop at the reserve; a fan loop on
  Liquipedia or PUBG stops at its share and is logged. `python -m sports_follow.usage` shows each
  source's spend this hour and today, by lane.
- **Adding or removing a sport** is one module and one line, too. Everything about a sport lives in
  `sports_follow/sports/<sport>.py`, a `Sport` subclass (`sports/base.py`): its names, scoreboard
  kinds, whether it's a team sport, its Wikidata ids and registry rules, its moments (start, result,
  line-up, what happens during a game, what a source takes back (`taken_back`), and which of them are a
  player's highs on Today's "This week" and in what words (`poster`: a "Five-for"), and the same off a
  finished game's line when no moment was found in it (`finished_poster`: "5/23 (4 ov)")), how big a game
  its sources list league-wide is and what its card says (`notable`: a final, "1st v 3rd", a last-over
  finish; for Today's big results and big games coming up), its form summary,
  how search shows its athletes, and its colours (a player's photo is recoloured with them, and their link
  previews are drawn in them). Shared code
  asks the sport (`sports.get(name)`), and a sport no module knows (golf, from an AI-built page) gets the
  rules every sport shares. The one line is its place in `sports.ALL`, the order the app lists them;
  its sources each declare the `sport` they serve. `SPORTS_FOLLOW_SPORTS` turns sports off without a
  code change, and the admin panel without a restart. `tests/test_sports.py` fails if shared code names a sport.

  The web app keeps no list of sports: it takes them from the server (`GET /api/sports`: names, the
  free-text names that are each sport, alert wording, what runs; `/api/sports.css`: colours), loaded
  before the first render and kept on the device. How a sport is drawn is in three places, each with one
  line in its list: its facts, `web/src/sports/facts/<sport>.ts` (in `FACTS`: the scoreboard kinds it
  draws, its ground as line art behind Today's cards (`art`), how its line reads (`highlight`, `noLine`,
  `inHeadline`), its score in a few characters for a live player's pill (`short`)), shared by both apps;
  and its live board, `web/src/sports/<sport>.tsx` (in `VIEWS`) and `mobile/src/sports/<sport>.tsx` (in
  `BOARDS`). A sport without them shows the plain score and no line art. The Android app (`mobile/`)
  takes the same list, and the colours too, from `GET /api/sports` (a sport's `colour`: its block, text
  on it, softer text on it, and itself as text on ink).
- **An admin panel** at `/admin` (`sports_follow/admin.py`, `web/src/admin/`) changes the running app, with
  no restart: it turns sports and sources off and on, sets what each source may spend (its allowance a
  minute, an hour or a day, as a new plan gives; the margin; the reserve for live games and fans; the
  fans' share; the pause after a 429) next to what was spent, by lane, and looks after accounts (find
  one by address, see who they follow and their devices, sign them out everywhere, block them from
  signing in, delete them). What it changes is rows in `control` (`sports_follow/controls.py`), which
  every process reads again every 10 seconds; `sports.ENABLED`, `adapters.ADAPTERS` and the source
  budgets follow them. The server's settings stay the ceiling: a sport `SPORTS_FOLLOW_SPORTS` leaves out,
  or a source `SPORTS_FOLLOW_SOURCES` leaves out or without its token, can't be turned on there. It opens
  with a password of its own, not a fan's account: `python -m sports_follow.admin password` saves its
  scrypt hash (in `SPORTS_FOLLOW_ADMIN_PASSWORD_FILE`); without one the panel isn't there (404). An admin
  stays signed in 12 hours, a new password signs every admin out, and tries are limited per address and,
  for wrong ones, across everyone. The panel is its own chunk of the web build, so fans don't download it.
- **Adding or removing a player** is a command, `python -m sports_follow.players` (`sports_follow/players.py`),
  for whoever runs the app. `add` puts an athlete no source lists in the registry (`--sport`, `--name`,
  and any `--id system=id`, `--alias`, `--team`, `--country`, `--league`, `--born`), or pins a registry
  athlete by id so no import drops them. `remove` takes a player's page out (by page id, registry id or
  `system:id`) with everything that hangs off it, and their hot card; it shows what it will delete and
  asks first unless given `--yes`. `restore` takes a decision back, and `list` shows them. Decisions are
  rows in `athlete_override`, which every registry import respects, so they work on a running server and
  survive the weekly import; a removed athlete's ids in the sources are left out of search and refused
  for following.
- **Starter players**, who a new fan is offered in each sport (the welcome, an empty Today, search's
  "Try"), find themselves, the same way for every sport (`sports_follow/starters.py`): who's playing
  now or soon, worked out as asked from the live cards; the most followed, counted as asked; and the
  best known (Wikipedia editions, the home country's counting double: `SPORTS_FOLLOW_STARTERS_HOME_COUNTRY`)
  with a game soon, which the worker finds in their sources once a day. The operator can pin a few
  ahead of them with `players starter add <registry id>` (it checks the source has them, and keeps
  them in the registry), `starter remove` (they stay kept; `restore` lets them go) and `starter list`;
  `starter refresh` builds the best-known pool now. A sport that's turned off offers none, and removing a
  player takes their pin with them.
- **Renaming the app** is one line: `sports_follow/app.json` holds the name fans see ("stann"). The API
  reads it (`config.APP_NAME`: its error messages, the sign-in email, the Credits notice, and
  `GET /api/config`), the web build fills it into the page title, the manifest, the service worker and
  the app's code (`web/vite.config.ts`, `web/src/app.ts`), and `mobile/app.config.ts` gives it to the
  Android app, whose code reads it from Expo's config. `tests/test_app_name.py` fails if any of them
  writes the name out itself.
- CI (`.github/workflows/ci.yml`) runs on every pull request: the tests against a real Postgres with
  Stockfish installed, so none are skipped; one migration head, `alembic upgrade head` on an empty
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

The checks CI runs on every pull request, after `.venv/bin/pip install -r requirements-dev.txt`:

```bash
.venv/bin/python -m pytest
.venv/bin/ruff check .                                      # lint: rules that find bugs (pyproject.toml)
.venv/bin/basedpyright --pythonpath .venv/bin/python       # types: fails on errors not in .basedpyright/baseline.json
cd web && npm run build                                     # type-check and build the app
cd mobile && npm run typecheck                              # type-check the Android app
```

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
| `SPORTS_FOLLOW_MODEL` | `openrouter:moonshotai/kimi-k2.6:nitro` | any gateway model ref, e.g. `openrouter:anthropic/claude-opus-5` |
| `SPORTS_FOLLOW_EFFORT` | `medium` | `anthropic:` route only |
| `SPORTS_FOLLOW_CARD_MAX_AGE` | `21600` | seconds before a card is rebuilt |
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
| `OTEL_METRICS_EXPORTER` | `otlp` | `none` sends no metrics, while traces and logs still go. Metrics: API requests by route, worker jobs, pushes, moments, card builds and refreshes, source requests by host; and from the worker, every minute, live games and how long since each was polled, the job queue, cards by status, each source's allowance (`metrics.py`) |
| `OTEL_METRIC_EXPORT_INTERVAL` | `60000` | milliseconds between metric exports |
| `OTEL_TRACES_SAMPLER`, `OTEL_TRACES_SAMPLER_ARG` | every trace | e.g. `parentbased_traceidratio` and `0.2` to keep a fifth; a job keeps its request's choice |
| `OTEL_SERVICE_NAME` | `sports-follow-api`, `sports-follow-worker` | the process's name in the traces (`OTEL_RESOURCE_ATTRIBUTES` adds more, e.g. `deployment.environment.name=production`) |
| `SPORTS_FOLLOW_MAIL_LOG` | `0` | `1` logs sign-in codes instead of emailing them, for development; never over HTTPS |
| `SPORTS_FOLLOW_MAIL_CODES_PER_DAY` | `100` | sign-in codes a UTC day to everyone, the mail plan's daily allowance (Resend's free plan: 100); one network gets at most 20 a day |
| `SPORTS_FOLLOW_GOOGLE_CLIENT_ID` | none | the Google OAuth web client for "Continue with Google"; redirect URI `<origin>/api/auth/google/callback` (locally `http://localhost:5173/...`) |
| `SPORTS_FOLLOW_GOOGLE_CLIENT_SECRET` | from `SPORTS_FOLLOW_GOOGLE_CLIENT_SECRET_FILE`, else `~/.config/sports-follow/google-client-secret` | that client's secret; without it the Google button is hidden |
| `SPORTS_FOLLOW_ADMIN_PASSWORD_FILE` | `~/.config/sports-follow/admin-password` | the admin panel's password hash, written by `python -m sports_follow.admin password`; without it there's no panel |
| `SPORTS_FOLLOW_CONTACT_EMAIL` | none | where fans write about their data, shown on the privacy page (no Questions part without it) and in the user agent data sources see |
| `SPORTS_FOLLOW_SITE_URL` | none | the app's public address; compose sets `https://$DOMAIN`. Push services and data sources are told it, and link previews' addresses are on it (without it, on the address each request came to) |
| `SPORTS_FOLLOW_VAPID_SUBJECT` | the site address, else `https://localhost` | the contact push services see; never a person's address |
| `SPORTS_FOLLOW_STOCKFISH` | `stockfish` on the PATH | chess engine; without it, no chess swings |
| `SPORTS_FOLLOW_ENGINE_BUSY` | `0.5` | the share of one CPU core the engine may use |
| `SPORTS_FOLLOW_ENGINE_NODES` | `500000` | work per position (about a second on one core) |
| `SPORTS_FOLLOW_ENGINE_GAME_INTERVAL` | `20` | seconds between evaluations of one game |
| `SPORTS_FOLLOW_HISTORY_READS` | `20` | source requests a minute for older results and scorecards |
| `SPORTS_FOLLOW_SPORTS` | every sport | the sports that may run, e.g. `football,cricket,chess`: a sport left out isn't searched, imported or polled, its sources don't run, and its followed players' pages stay as they are. The admin panel turns these off and on while the app runs |
| `SPORTS_FOLLOW_SOURCES` | every source | the sources that may run; a public build lists only licensed ones, e.g. `lichess_chess,sportmonks_football,sportmonks_cricket`; ESPN's unofficial endpoints are for development only. The admin panel turns these off and on while the app runs, never one left out here |
| `SPORTS_FOLLOW_RESEARCH_PER_HOUR` | `10` | AI researches an hour across all fans |
| `SPORTS_FOLLOW_STARTERS_HOME_COUNTRY` | `India` | whose athletes count double among the best-known starters new fans are offered (empty: nobody's) |
| `SPORTS_FOLLOW_RESEARCH_PER_ADDRESS` | `10` | AI researches a UTC day from one client address (an IPv6 /64); not counted for private addresses |
| `SPORTS_FOLLOW_AGENT` | `1` | `0` runs without the research agent, as with no OpenRouter key: fans follow athletes their sources have (from the registry and search), those pages come from the sources alone (scores, games, stats, live and alerts, no profile or news), nothing offers AI research, and pages the agent built before stay as they were, no longer updated |
| `SPORTS_FOLLOW_AGENT_LIVE` | `0` | `1` lets the AI agent check live scores for sports no source covers (costs model calls every minute of a game); only with the agent on |
| `SPORTS_FOLLOW_LICHESS_TOKEN` | from `~/.config/sports-follow/lichess-token` | Lichess API token (scope `study:read`); broadcasts need it |
| `SPORTS_FOLLOW_SPORTMONKS_TOKEN` | from `~/.config/sports-follow/sportmonks-token` | Sportmonks API token (football and cricket); with one, football reads Sportmonks instead of ESPN and followed footballers move over on their next refresh. Cricket stays on ESPN in a development build until the plan covers ODIs, Tests and the IPL; a build that lists `sportmonks_cricket` without `espn_cricket` uses it |
| `SPORTS_FOLLOW_PANDASCORE_TOKEN` | from `~/.config/sports-follow/pandascore-token` | PandaScore API token (esports; the free plan is enough); without one, esports players get AI-built pages (none with `SPORTS_FOLLOW_AGENT=0`) |
| `SPORTS_FOLLOW_PANDASCORE_RESERVE` | `300` | PandaScore requests an hour (of 1,000) kept for live series and fans; refreshes, the history lane and the registry import stop below it |
| `SPORTS_FOLLOW_ESPORTS_GAMES` | every team title | the titles whose pros the registry imports (PandaScore slugs: `valorant,cs-go,league-of-legends,dota-2,mlbb,kog,r6-siege,ow,rl,cod-mw,lol-wild-rift`; `cs-go` is CS2). Naming a few imports just those, and removes only their stale players |
| `SPORTS_FOLLOW_OPENDOTA` | `1` | `0` turns off OpenDota's Dota 2 map data (kills, clock, gold lead live; each player's hero and numbers after each map), laid over PandaScore's Dota 2 series; no key, 60 requests a minute and 3,000 a day |
| `SPORTS_FOLLOW_PUBG_TOKEN` | from `~/.config/sports-follow/pubg-token` | PUBG developer API key (PUBG esports); the worker indexes recent tournaments every 10 minutes |
| `SPORTS_FOLLOW_LIQUIPEDIA_TOKEN` | from `~/.config/sports-follow/liquipedia-token` | LiquipediaDB API key (battle royale esports: BGMI, Free Fire); the free key is for non-commercial use only |
| `SPORTS_FOLLOW_LIQUIPEDIA_HOURLY` | `60` | the key's requests an hour; counted in Redis across processes (each counts its own while Redis is down) and never overspent (a paid plan raises it) |
| `SPORTS_FOLLOW_LIQUIPEDIA_RESERVE` | `30` | of those, kept for live lobbies and fans; refreshes, the history lane and the registry import stop below it |
| `SPORTS_FOLLOW_LIQUIPEDIA_WIKIS` | `pubgmobile,freefire,apexlegends` | the battle royale wikis whose active players the registry imports |
| `SPORTS_FOLLOW_SPORTMONKS_RESERVE` | `1000` | Sportmonks requests an hour kept for live matches, of the 2,000 that football allows per entity and cricket per endpoint, and for fans; refreshes and the history lane stop below it |

## Deploy

One server runs the whole app with Docker Compose, with HTTPS from Caddy: see
[deploy/README.md](deploy/README.md). The public build runs licensed sources only.

## Android app

`mobile/` is the Android app (package `io.stann.app`): React Native with Expo, in TypeScript like the web
app. It looks like the site on a phone, page for page: each of the web app's pages and blocks has its
React Native twin in `mobile/src`, drawn from `web/src/styles.css`'s values (`mobile/src/theme.ts`), with
the same fonts, icons and line art. It asks the same API (stann.in, or `EXPO_PUBLIC_API_URL` when Metro
bundles it) and is the same fan, by the same cookie, or the same account once signed in.

What the pages decide is shared with the web app, not copied: the API's calls and types
(`web/src/endpoints.ts`, `types.ts`), what Today shows and how cards word a game (`today.ts`,
`lines.ts`), search's sections (`search.ts`), alert levels (`alerts.ts`), dates and names (`format.ts`,
`follow.ts`), what a Share button shares (`share.ts`), and each sport's facts (`sports/facts/`: the scoreboard kinds it draws, its line art, how it
words a player's line). Only the drawing is each app's own: a sport's live board is
`web/src/sports/<sport>.tsx` on the web and `mobile/src/sports/<sport>.tsx` in the app. Like the web app,
the app takes the sports, their names and their colours from `GET /api/sports`:
`tests/test_web_names.py` holds `mobile/src` to naming no sport and no source. The name is
`sports_follow/app.json`'s and the icons are the web's (`mobile/app.config.ts`).

Every page is there: Today (with the welcome for a new fan), Search and an athlete's preview, a player's
page with its tabs, Following, Alerts, Account (sign-in by email code), Credits and Privacy. Where the web
listens to the fan stream, the app reads again every 30 s for now (BHA-231). Notifications come through
FCM (below); the lock-screen live score and a widget follow (BHA-232). The native project
(`mobile/android`) is generated by Expo and not committed.

Needs JDK 17 and the Android SDK (platform 36, an emulator or a phone with USB debugging):

```bash
cd mobile && npm install
export JAVA_HOME=~/.local/jdk-17/Contents/Home ANDROID_HOME=~/.local/android-sdk
npm run android                                     # builds, installs and opens it on the emulator or phone
EXPO_PUBLIC_API_URL=http://10.0.2.2:8421 npm start  # then Metro serves it the local stack (10.0.2.2 is this Mac)
npm run typecheck                                   # what CI checks
```

**Notifications** come through Firebase Cloud Messaging, planned as the web's are (one notification a
game, replaced as its moments come; sound only for a key moment outside quiet hours). The server sends
them itself with FCM's HTTP v1 API (`sports_follow/fcm.py`), with no Expo push service in between. The
app (`mobile/src/push.ts`) makes two channels, "Key moments" with sound and "Other moments" without. It
asks to notify the first time the fan follows someone (Android 13 and later); then Alerts' Settings turns
them on and off for the phone, with its quiet hours and a test alert, as on the web. While they're on, it
registers its FCM token with the API (`POST /api/push/fcm`, with its timezone and quiet hours) at every
start, after a sign-in or out, and whenever FCM replaces the token; a tap opens the player's page.
Without the Firebase project's files there are no notifications and nothing else changes. To turn them
on, once:

1. In the [Firebase console](https://console.firebase.google.com), make a project and add an Android app
   with the package `io.stann.app`.
2. **The app:** download its `google-services.json` to `mobile/google-services.json` (gitignored; or set
   `GOOGLE_SERVICES_JSON` to its path), then build again with `npm run android`. It's not a secret, but it
   stays out of the repo with the project it names.
3. **The server:** under Project settings, Service accounts, generate a private key and save it as
   `~/.config/sports-follow/fcm-service-account.json` (`chmod 600`; `SPORTS_FOLLOW_FCM_KEY_FILE` names
   another file). Restart the API and the worker: `GET /api/push` answers `"fcm": true`. For the server,
   see deploy/README.md, "Android notifications".
4. In the app, follow a player and allow notifications. Their next moment arrives as a notification.

`android/` still holds the earlier route, a Trusted Web Activity around the site; it goes once the
native app is on Play (BHA-39). `/.well-known/assetlinks.json` stays: the native app will use it to open
stann.in links (BHA-27).

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
  only once someone follows them. Each is the one file Wikidata names (its preferred one, when it names
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
  and there are no lock-screen live scores (Live Activities) or Android ongoing live-score
  notifications; those need a native app.
