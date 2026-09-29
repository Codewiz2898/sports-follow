# Sports Follow

Type a player's name (Ronaldo, Magnus Carlsen, Federer, Kohli, …) and get one page with:

- **Live now**: current score, match clock/state and the player's live stats, with optional auto-refresh every 60s
- **Upcoming** matches/events for every team or tournament they're in, shown in your local time with a countdown
- **Latest news** with links to the source
- **Recent results** and what the player contributed
- **Season stats**

It works for any sport. Nothing is hard-coded per sport: the agent researches the player and
decides what "score" and "stats" mean for that sport (innings and batting figures for cricket,
sets for tennis, round and move for chess, and so on).

## Run it

The model is reached through the local **llm-providers gateway** (`~/repos/llm-providers`, config in
`~/repos/platform/llm-gateway/gateway.json`). The gateway must be running on 127.0.0.1:8787. It holds
the provider keys; this app only presents its own `sports-follow` app token.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn sports_follow.server:app --port 8421
```

Open http://localhost:8421. You can also link straight to a player: `http://localhost:8421/?player=Kohli`.

The app token is `SPORTS_FOLLOW_GATEWAY_TOKEN`. The app reads it from the environment. If it isn't
set there, the app reads that one variable, and nothing else, from `~/.config/llm-providers/keys.env`.

## How it works

```
browser ──SSE──▶ FastAPI (/api/follow, /api/live)
                   │
                   ▼
           agent loop (agent.py) ──▶ llm-providers gateway ──▶ Claude (Anthropic or OpenRouter)
                   │
                   └─ runs tools locally (tools.py):
                        web_search  – DuckDuckGo web + news
                        fetch_url   – any page (main text) or JSON API (ESPN, Lichess, …)
                        submit_player_report – the structured report
```

- The gateway passes client-side tools only, not a provider's server-side web search. So search and
  fetch run here. `fetch_url` refuses loopback and private addresses, because the model chooses the URLs.
- `schema.py` defines the report as Pydantic models. Their JSON schema becomes the submit tool's input
  schema, and each submission is validated against them before it reaches the UI.
- Each tool call is streamed to the page as progress. After 6 rounds of research (2 for a live
  check), the agent is told to submit what it has.
- `/api/live` is a smaller, low-effort check that only looks at the live game. The UI polls it for auto-refresh.
- Results are cached in memory: full reports for 10 minutes, live checks for 60 seconds.

| Env var | Default | |
|---|---|---|
| `SPORTS_FOLLOW_MODEL` | `openrouter:anthropic/claude-opus-5` | any gateway model ref, `provider:model`, e.g. `anthropic:claude-opus-5` |
| `SPORTS_FOLLOW_EFFORT` | `medium` | `low` / `medium` / `high`; only the `anthropic:` route uses it |
| `LLM_GATEWAY_URL` | `http://127.0.0.1:8787` | |
| `SPORTS_FOLLOW_GATEWAY_TOKEN` | from `keys.env` | this app's gateway token |

## Design

- **App design** (Claude Design canvas, private link): https://claude.ai/artifact/6qWRYFCmugDe292LKwowAB — phone flow (Following, Player Live/Stats/News, Search), Player · Live for cricket, football, basketball, chess and tennis, a retired-player screen, a desktop dashboard, and long-term wireframes for Compare and Teams.
- **Backend design**: [docs/backend-design.html](docs/backend-design.html) (also published at https://claude.ai/artifact/Quj8eSYYNmQPsPEbCpCNTe). The target architecture this prototype grows into: player-first pipeline, event-armed live polling, automatic source discovery, fan-out of one shared player record, and a market section.

## Limitations

- A full report takes about 2 minutes (measured: Kohli 193s before the research budget, Carlsen 133s
  after it). A live refresh takes about 30s.
- Live scores come from web pages and public APIs, so they can lag the real game by a minute or two.
- DuckDuckGo search is keyless and can rate-limit under heavy use. The agent works around failed lookups.
