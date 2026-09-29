# Sports Follow

Follow any athlete in any sport. Each followed player gets one shared page: live score and the
player's own numbers while they play, upcoming games, news, recent results and season stats. The
Following screen puts all of your players together, live games first.

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
                                ├─ refresh_live: agent live check, for sports with no adapter
                                ├─ import_registry, weekly: Wikidata ──▶ athlete table (the player registry)
                                └─ tick, every minute: arm events, queue polls, refreshes and rebuilds
                                         │                                  │
                              llm-providers gateway ──▶ LLM         adapters/ ──▶ ESPN JSON (football, cricket, basketball, tennis)
                                                                              └─▶ Lichess API (chess)
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
  query service asks; football is most of it); `--new` reads only people not imported yet.
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
  (`search.RESEARCH_PER_DAY`, counted in Redis). Following a player already here, or a search result
  with a live source, doesn't count. The gateway's per-app budget is the hard ceiling behind it.
- **Names converge.** "Kohli" and "Virat Kohli" end up on the same player: every typed name becomes an
  alias, and when a build discovers the athlete already exists, the rows are merged. A player bound to a
  source is never renamed or merged by an agent report, so a namesake's report can't take them over.
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

  Every other sport still gets everything from the agent.
- **Live polling is armed by the schedule.** Fixtures are stored as events keyed by the source's id.
  30 minutes before one starts it is armed; one `poll_event` job then polls it (every minute before
  kick-off, every 8–15 s in play), appends event states and player lines, and pushes every followed
  player's card. The job hands over every 10 minutes; the tick restarts it while the game is on.
- **One stream per fan.** The app holds one SSE connection for all of a fan's players.
- Adapter parsers are pure functions over the source's JSON, tested against recorded responses in
  `tests/fixtures/espn` (`.venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m pytest`).

## Run it

Needs Docker, Python 3.11+, Node 20.18+, and the [llm-providers](https://github.com/Codewiz2898/llm-providers)
gateway running on 127.0.0.1:8787 with a `sports-follow` app token.

```bash
docker compose up -d                                   # Postgres :5433, Redis :6380
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/alembic upgrade head
.venv/bin/python -m sports_follow.registry     # the player registry; ~90 min, then weekly by the worker
cd web && npm install && cd ..
```

Then three processes:

```bash
.venv/bin/uvicorn sports_follow.server:app --port 8421 --reload --timeout-graceful-shutdown 3   # API
.venv/bin/arq sports_follow.worker.WorkerSettings                  # pipeline worker
cd web && npm run dev                                              # app on http://localhost:5173
```

For a single-process deploy, `npm run build` in `web/` and the API serves the built app itself. Always pass
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
| `SPORTS_FOLLOW_POLL_SESSION` | `600` | seconds one live-poll job runs before handing over |
| `LLM_GATEWAY_URL` | `http://127.0.0.1:8787` | |
| `SPORTS_FOLLOW_GATEWAY_TOKEN` | from `~/.config/llm-providers/keys.env` | this app's gateway token |

## Design

- **App design** (Claude Design canvas): https://claude.ai/artifact/6qWRYFCmugDe292LKwowAB
- **Backend design**: [docs/backend-design.html](docs/backend-design.html), also at https://claude.ai/artifact/Quj8eSYYNmQPsPEbCpCNTe

## Limitations

- **ESPN's endpoints are unofficial.** They are the JSON behind espn.com and espncricinfo.com: no key,
  no contract, and they can change or be blocked without notice. Fine for development and personal
  use; a public launch should use a licensed feed, which fits in as another adapter.
- The registry's current teams are Wikidata's, which can lag a transfer by weeks; the live source's team
  is what fixtures use. Chess ratings aren't in it (the FIDE list needs FIDE's permission for commercial
  use), so chess rows show title and country. Photos are stored as Commons file names but not shown yet:
  each needs its licence and credit fetched first.
- When the registry is thin, search still asks ESPN and Lichess live: ESPN's search has no typo tolerance
  and no sport filter, and Lichess allows one request at a time.
- AI research counts are per anonymous cookie, so clearing cookies resets them; accounts will fix that.
- A name typed into "Research with AI" that the sources don't recognize still waits for the agent (1–4
  minutes) before the adapter is bound, because the agent is what says which sport it belongs to.
- Cricket: ESPN serves no career stats, so the Stats tab shows recent form computed from the player's
  scorecards in the last 30 days. Domestic matches often have no scorecard, so no player line.
- Football: national-team games show up for every player of that nationality's team; a player who
  isn't called up sees "Not in the matchday squad" rather than the game being hidden. Women's players
  follow their club only: ESPN's search can't tell a women's national team from the men's.
- Tennis: ESPN has no player schedule or match summary, so matches are read from tour scoreboards;
  a player's next match appears only once the draw is made (a day or two ahead). Singles only.
- Chess: Lichess relays most elite and many open events, but not every tournament. A player's events
  are found from their Lichess FIDE page and broadcast search; Lichess rate-limits hard, so reads are
  one request a second and a rate limit fails the refresh instead of dropping results.
- Other sports still come from the agent alone, with its generic scoreboard and events deduplicated
  by sport, title and date.
- Fans are anonymous (a cookie). Accounts, push notifications and alert rules come later.
