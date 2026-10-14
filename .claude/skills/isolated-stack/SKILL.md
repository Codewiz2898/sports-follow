---
name: isolated-stack
description: Run a second, throwaway Sports Follow stack (API, web, optionally the worker) from its own worktree, on a copy of the database and a Redis of its own, apart from the main checkout and the main stack that other sessions use; and tear it down after. Use for bug-bounty rounds, for trying a branch against real data, or for booting merged fixes on a migrated database.
---

# An isolated stack

The main stack (API :8421, web on localhost:5173, worker, the `sports_follow` database, the
`sports-follow-redis` container) belongs to the main checkout, where other sessions work. A stack of your
own shares the Postgres container with it and nothing else:

| | Main stack | Isolated stack `<name>` |
|---|---|---|
| Code | the main checkout | `<main checkout>/.claude/worktrees/<name>`, at the commit under test |
| Database | `sports_follow` | `sports_follow_<name>`, a copy |
| Redis | `sports-follow-redis`, :6380 | a container of its own, `sports-follow-redis-<name>`, :6381 |
| API / web | :8421 / `http://localhost:5173` | :18421 / `http://<name>.localhost:15173` |
| Secrets, keys | `~/.config/sports-follow` | made fresh in your scratchpad; paid sources off |

Pick a short `<name>` for the round (`r2`) and use it everywhere below. A second stack at the same time
needs other ports (API, web and Redis) as well as another name.

Why each part is separate:
- **Its own Redis container**, not another db number: Redis pub/sub ignores the db, so a card or moment
  the isolated stack publishes would stream to everyone with the main app open, and back.
- **Its own host name** (`<name>.localhost`): browsers keep cookies per host, not per port, so the
  isolated app at localhost:15173 would replace the main app's fan cookie with one the main API rejects,
  and that browser's fan would start over with nothing followed.
- **Paid sources off**: the API spends a source's key too (search, previews, follows), not just the
  worker, and counts it in its own Redis, where the main stack can't see it. Together the two can spend
  past a source's limit, and live games on the main stack pay for it.

`<venv>` below is the main checkout's `.venv` (a worktree has none), always by absolute path.

## Start it

1. **The worktree**, from the main checkout: `git worktree add <main checkout>/.claude/worktrees/<name> <commit>`.
   For the web app, `npm install` once in its `web/`.
2. **Redis**: `docker run -d --name sports-follow-redis-<name> -p 127.0.0.1:6381:6379 redis:7-alpine`.
3. **A copy of the database.** Two `docker exec`s piped together, not one `sh -c` (quoting breaks it):

       docker exec sports-follow-postgres createdb -U sports sports_follow_<name>
       docker exec sports-follow-postgres pg_dump -U sports sports_follow | docker exec -i sports-follow-postgres psql -q -U sports -d sports_follow_<name>

   Then migrate it from the worktree: `cd <worktree> && DATABASE_URL=postgresql+psycopg://sports:sports@127.0.0.1:5433/sports_follow_<name> <venv>/bin/alembic upgrade head`.
4. **Secrets of its own**, in your scratchpad: the cookie secret is made on first start at the path given;
   push keys with `SPORTS_FOLLOW_VAPID_KEY=<scratch>/vapid-private.pem <venv>/bin/python -m sports_follow.notify keys`.
   The copied database holds real fans' push subscriptions; a fresh key means a push from this stack
   can't reach their devices.
5. **The environment.** Every process of the stack (API, worker, a hunter's own server) runs with exactly
   these, and nothing falls back to `~/.config`:

       DATABASE_URL=postgresql+psycopg://sports:sports@127.0.0.1:5433/sports_follow_<name>
       REDIS_URL=redis://127.0.0.1:6381/0
       SPORTS_FOLLOW_COOKIE_SECRET_FILE=<scratch>/cookie-secret
       SPORTS_FOLLOW_VAPID_KEY=<scratch>/vapid-private.pem
       SPORTS_FOLLOW_SPORTMONKS_TOKEN_FILE=<scratch>/none
       SPORTS_FOLLOW_PANDASCORE_TOKEN_FILE=<scratch>/none
       SPORTS_FOLLOW_PUBG_TOKEN_FILE=<scratch>/none
       SPORTS_FOLLOW_LIQUIPEDIA_TOKEN_FILE=<scratch>/none
       SPORTS_FOLLOW_LICHESS_TOKEN_FILE=<scratch>/none
       LLM_GATEWAY_URL=http://127.0.0.1:9
       LLM_PROVIDERS_KEYS_FILE=<scratch>/none
       SPORTS_FOLLOW_MAIL_LOG=1

   That leaves ESPN (unofficial, no key, development only) and Lichess without a token; the research
   agent is off (nothing answers at its gateway address) and sign-in codes go to the log. Turn a paid
   source on (its real token file) only when the round needs it, say so on the board, and keep hunters
   off loops against it.
6. **The API and web**, as preview servers: two entries in the worktree's `.claude/launch.json`, never
   committed (they hold your scratchpad's paths):

       {"name": "<name>-api", "runtimeExecutable": "/usr/bin/env", "port": 18421,
        "runtimeArgs": [<the environment above, one "NAME=value" each>,
          "<venv>/bin/uvicorn", "sports_follow.server:app", "--app-dir", "<worktree>",
          "--port", "18421", "--timeout-graceful-shutdown", "3"]},
       {"name": "<name>-web", "runtimeExecutable": "/bin/sh", "port": 15173, "url": "http://<name>.localhost:15173",
        "runtimeArgs": ["-c", "cd <worktree>/web && SPORTS_FOLLOW_API=http://127.0.0.1:18421 PATH=$HOME/.local/node/bin:$PATH npm run dev -- --port 15173 --strictPort"]}

   Open the app only at `http://<name>.localhost:15173`, never at localhost:15173.

## The worker: only if the round needs it

Only when the round tests live polling, moments or alerts. Start it from the worktree (arq runs the code
of the directory it starts in) with the environment above, as a background task:
`cd <worktree> && env <environment> <venv>/bin/arq sports_follow.worker.WorkerSettings`. Put its task id
or PID in the board's header, and stop it the moment the round no longer needs it. (Round two's worker
once ran all night.)

## When the round ends

Tear it all down; nothing of it should outlive the round:

1. Stop its processes: `preview_stop` for the API and web, the worker's task, and any server a hunter
   started. Then check nothing still runs from the worktree (the worker listens on no port):
   `lsof -d cwd | grep '.claude/worktrees/<name>'` prints nothing.
2. Remove its Redis: `docker rm -f sports-follow-redis-<name>`. Never the main `sports-follow-redis`.
3. Drop the database once nothing is connected to it:
   `docker exec sports-follow-postgres psql -U sports -d sports_follow -c "DROP DATABASE sports_follow_<name>;"`
4. Remove the worktree: `git worktree remove <worktree>` (`--force` only for the uncommitted
   `launch.json` entries, after checking that's all `git status` shows).
