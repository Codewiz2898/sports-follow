---
name: debug
description: Find out why something went wrong in Sports Follow, on the production server (stann.in) or locally. Use for a fan who didn't get a push, a card stuck in building or failed, a stale live score, a failed request or job, or an alert email from Grafana. Covers where the logs, traces, errors and metrics are, finding a request, fan, player or game by id, the queries for the usual questions, the Redis keys, and what's safe to run on production. Use it before reading code or poking at the database or server by hand.
---

# Debugging Sports Follow

Find the reason before you change anything. Most questions are answered by reading: a log line, a row,
a Redis key, a trace. The usual ones have their own page:

| Question | Page |
|---|---|
| A fan didn't get a push (for a goal, a result, a line-up) | [push.md](push.md) |
| A card is stuck in `building`, or `failed`, or old | [cards.md](cards.md) |
| A live game's score is stale, or a game was never polled | [live.md](live.md) |
| What a Redis key means, and whether it's safe to touch | [redis.md](redis.md) |

For anything else, start from the request's or job's log lines, then its trace.

## What's safe on production

Production is real fans' data and phones, and paid source and model quotas. Without asking:

- Read logs (`c logs`, Grafana, `grafana.py`), traces, metrics and alerts.
- Run SQL through `psql_ro` below, whose session is read-only: a write by mistake is refused. (It
  guards against accidents only: the `sports` user could switch it off. Don't.)
- Run Redis reads: `GET`, `TTL`, `EXISTS`, `HGETALL`, `ZRANGE`, `ZCARD`, `--scan` (never `KEYS`), and `SUBSCRIBE`.
- Run `python -m sports_follow.usage`, `python -m sports_follow.notify stats`, `python -m sports_follow.retention --count`, `deploy/deploy.sh status` and `c ps`.

Ask Bharath first, saying what it will do:

- Any write to the database, or deleting or setting a Redis key (a lock, a budget, a backoff).
- Restarting or recreating a container, a deploy or rollback, a migration, a restore.
- Anything that spends a source's quota or the model's budget: a refresh, a registry import,
  `players starter add`, deleting `budget:*` or `buildretry:*` keys.
- Anything that reaches a person: a test push (`notify.send_test`, `POST /api/push/test`), a sign-in email.
- `players add/remove/restore/unmatch/rematch`, `retention` without `--count`, `notify keys`, and any `deploy/*.sh`
  other than `status`.

Never print or copy secrets (`/etc/sports-follow`, `~/.config/sports-follow`, `deploy/.env`). Never put
a fan's id or email, or a client's address, in an issue, PR, commit or chat summary: name a fan by their
12-character log hash.

## Where things are

| | Production | Locally |
|---|---|---|
| Get in | `ssh deploy@$(dig +short stann.in \| head -1)`, then `cd /opt/sports-follow` and define `c() { docker compose -f deploy/compose.yaml "$@"; }` | this checkout; `docker compose up -d` for Postgres and Redis |
| Logs | `c logs --since 2h api worker` (JSON lines), or Grafana (below) | the terminals running the API (`uvicorn … --port 8421`) and the worker (`arq sports_follow.worker.WorkerSettings`), as text lines `HH:MM:SS LEVEL logger: message  [rid=… fan=… job=…]` |
| `psql_ro`, read-only | `psql_ro() { c exec -T -e PGOPTIONS='-c default_transaction_read_only=on' postgres psql -U sports sports_follow "$@"; }` | `psql_ro() { docker exec -i -e PGOPTIONS='-c default_transaction_read_only=on' sports-follow-postgres psql -U sports sports_follow "$@"; }` |
| Redis | `c exec -T redis redis-cli` | `docker exec -i sports-follow-redis redis-cli` |
| A command of the app's | `c exec -T worker python -m sports_follow.<module> …` | `.venv/bin/python -m sports_follow.<module> …` |
| What's deployed | `deploy/deploy.sh status`, or from anywhere `curl -s https://stann.in/api/health` (its `commit`); the Deploy workflow's runs (`gh run list --workflow deploy.yml`), and one waiting for Bharath's approval (`--status waiting`) | `git log -1` |

Define `psql_ro` as a shell function, as written (a variable loses the quotes, and with them the
read-only setting), and feed it SQL on stdin: `psql_ro <<'SQL' … SQL`. The database is the last word of
the function: change it for a copy (an isolated stack's `sports_follow_<name>`). From the Mac, in one go:

```bash
ssh deploy@$(dig +short stann.in | head -1) "cd /opt/sports-follow && docker compose -f deploy/compose.yaml exec -T -e PGOPTIONS='-c default_transaction_read_only=on' postgres psql -U sports sports_follow" <<'SQL'
select status, count(*) from player_card group by status;
SQL
```

Only production sends logs, traces and metrics to Grafana Cloud. Locally there's only the terminal.

### Grafana

Grafana Cloud holds production's logs (Loki), traces (Tempo), metrics (Prometheus), dashboards and
alerts. Bharath opens it in the browser. From the Mac, [grafana.py](grafana.py) reads it with
`~/.config/sports-follow/grafana-admin.env`:

```bash
.venv/bin/python .claude/skills/debug/grafana.py alerts                      # what's firing now
.venv/bin/python .claude/skills/debug/grafana.py logs '{service_name=~"sports-follow.*"} | rid="<rid>"' 120
.venv/bin/python .claude/skills/debug/grafana.py logs '{service_name="sports-follow-worker"} | player_id="12"' 360
.venv/bin/python .claude/skills/debug/grafana.py logs '{service_name=~"sports-follow.*"} | error != ""' 1440
.venv/bin/python .claude/skills/debug/grafana.py trace <trace_id>             # every span, in order
.venv/bin/python .claude/skills/debug/grafana.py traces '{status=error}' 120
.venv/bin/python .claude/skills/debug/grafana.py metric 'sports_follow_live_games_behind'
```

- **LogQL.** The only stream label is `service_name` (`sports-follow-api` or `sports-follow-worker`).
  Every other field is filtered after a `|`:
  - `scope_name` is the logger: `sports_follow.requests`, `.worker`, `.pipeline`, `.structured`,
    `.notify`, `.web`, `.budget`.
  - The rest: `rid`, `fan`, `job`, `player_id`, `event_id`, `system`, `route`, `status`, `outcome`,
    `error`, `place`, `trace_id`.
  - `|= "text"` matches the message.
- **Dashboards** (folder Sports Follow): **API**, **Sources**, **Jobs and live polls**, **Notifications**
  and **Errors**. Errors groups every exception by what failed (`error`) and where (`place`), with its stack.
- **Alerts** email Bharath. Each rule's summary in `deploy/grafana/app-rules.json` and `rules.json` says
  what it means and where to look.

## Finding things by id

- **A request:** its id is the `X-Request-ID` answer header, and every line it logs carries it as `rid`.
  So do the jobs it queued. Find them with `c logs api worker | grep <rid>`, or LogQL `| rid="<rid>"`.
  The request line's `trace_id` opens its trace.
- **A job:** its closing line is `<job> done|failed (…)|cancelled in <s>s, after <s>s in the queue`, on
  `sports_follow.worker`, with `job`, `outcome`, `duration_ms`, `waited_ms`, `job_id`. Its context has
  the job's `player_id`, `event_id` or `system`. A failed build still says `build_card done`: look at
  the `build <id> failed:` line before it.
- **A fan:**
  - A signed-in fan's id is `account.fan_id` (`acct-…`), from their email:
    `select fan_id from account where email = lower('…')`. An anonymous fan's id is in their cookie.
  - Logs carry `fan`, the first 12 hex characters of the id's SHA-256. In SQL that's
    `left(encode(sha256(convert_to(fan_id, 'UTF8')), 'hex'), 12)`; in a shell,
    `printf %s '<fan id>' | shasum -a 256 | cut -c1-12`.
  - A fan's rows: `follow`, `push_subscription`, `notification`, `notification_mute`.
- **A player:** `select id, name, sport, slug from player where name ilike '%…%'`. Their page is
  `/player/<id>`, and their row in each source is `player_identity` (`system`, `external_id`).
- **A game:** `event.id` (in a card at `card->'live'->>'event_id'`); `event.key` is `<system>:<the source's id>` (`sportmonks_football:19134567`).
- **An error a fan saw in the app:** the web app reports it (logger `sports_follow.web`) with `page`,
  `version` (the build) and, for a failed call, `call_rid`: the failed request's own `rid`.

## How it fits together

- **The API** (`server.py`) answers the app, under one `rid` per request.
- **The worker** (`worker.py`, arq) runs the jobs:
  - The tick, every minute, queues due work (`pipeline.due_work`): card builds, structured refreshes,
    live polls, and moments nobody sent.
  - `build_card` runs the research agent through the LLM gateway (`agent.py`).
  - `refresh_structured` reads a bound source.
  - `poll_event` and `poll_lane` poll live games (`structured.py`) and find moments (`moments.py`),
    which `notify.py` pushes by the rules in `alerts.py`.
- **Sources** (`adapters/`) share per-source budgets in Redis (`adapters/budget.py`), in lanes:
  `live`, `fan`, `refresh`, `history` and `import`.
- **Postgres** holds the truth. **Redis** holds locks, budgets, backoffs, the job queue and each card's
  hot copy (redis.md).

When the code and this skill disagree, the code is right: fix the skill in the same PR.
