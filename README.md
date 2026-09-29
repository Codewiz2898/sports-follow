# Sports Follow

Follow any athlete in any sport. Each followed player gets one shared page: live score and the
player's own numbers while they play, upcoming games, news, recent results and season stats. The
Following screen puts all of your players together, live games first.

## How it's built

This is milestone 1 of the [backend design](docs/backend-design.html): the persistent core.

```
React app (web/) ──/api──▶ FastAPI (sports_follow/server.py) ──▶ Postgres (registry, cards, history)
      ▲                         │  enqueue jobs                   Redis (hot cards, per-player channels)
      └──── SSE: card updates ──┘         ▼
                              arq worker (sports_follow/worker.py)
                                ├─ build_card: research agent → report → registry rows → card
                                ├─ refresh_live: quick live check → card
                                └─ tick, every minute: arm events, rebuild stale cards, queue live checks
                                         │
                              llm-providers gateway ──▶ Claude (web_search / fetch_url tools run locally)
```

- **Work is keyed by player, never by fan.** Following adds one row. The first fan to follow a player
  triggers the build; every later fan gets the shared card at once.
- **Names converge.** "Kohli" and "Virat Kohli" end up on the same player: every typed name becomes an
  alias, and when a build discovers the athlete already exists, the rows are merged.
- **Live checks are armed by the schedule.** Upcoming games are stored as events; 30 minutes before
  one starts, every followed player in it gets a live check each minute until it ends.
- **One stream per fan.** The app holds one SSE connection for all of a fan's players.
- The research agent (`agent.py`, `tools.py`) is still the only data source. Its reports are written
  into the design's tables (events, event states, player lines, news) so the real adapters from the
  design can fill the same rows without the API or app changing.

## Run it

Needs Docker, Python 3.11+, Node 20.18+, and the [llm-providers](https://github.com/Codewiz2898/llm-providers)
gateway running on 127.0.0.1:8787 with a `sports-follow` app token.

```bash
docker compose up -d                                   # Postgres :5433, Redis :6380
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/alembic upgrade head
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
| `SPORTS_FOLLOW_LIVE_INTERVAL` | `60` | seconds between live checks during a game |
| `LLM_GATEWAY_URL` | `http://127.0.0.1:8787` | |
| `SPORTS_FOLLOW_GATEWAY_TOKEN` | from `~/.config/llm-providers/keys.env` | this app's gateway token |

## Design

- **App design** (Claude Design canvas): https://claude.ai/artifact/6qWRYFCmugDe292LKwowAB
- **Backend design**: [docs/backend-design.html](docs/backend-design.html), also at https://claude.ai/artifact/Quj8eSYYNmQPsPEbCpCNTe

## Limitations of this milestone

- Every card comes from the research agent, so a first build takes 1–3 minutes and a live check about
  30 seconds. Structured adapters (design section 4) replace it for live data, sport by sport.
- The live scoreboard is one generic layout, because the agent reports the score as text.
  Sport-specific boards need the structured state adapters provide.
- Events are deduplicated by sport, title and date, so two players in one game only share an event
  when the agent names the game the same way for both.
- Fans are anonymous (a cookie). Accounts, push notifications and alert rules come later.
