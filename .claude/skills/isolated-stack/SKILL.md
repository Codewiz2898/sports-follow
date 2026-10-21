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
| API / web | :8421 / `http://localhost:5173` | `http://<name>.localhost:18421` / `http://<name>.localhost:15173` |
| Secrets, keys | `~/.config/sports-follow` | made fresh in your scratchpad; paid sources off |

Pick a short `<name>` for the round (`r2`) and use it everywhere below. A second stack at the same time
needs other ports (API, web and Redis) as well as another name.

Why each part is separate:
- **Its own Redis container**, not another db number: Redis pub/sub ignores the db, so a card or moment
  the isolated stack publishes would stream to everyone with the main app open, and back.
- **Its own host name** (`<name>.localhost`): browsers keep cookies per host, not per port, so the
  isolated app at localhost:15173 would replace the main app's fan cookie with one the main API rejects,
  and that browser's fan would start over with nothing followed. The API's own port counts too: with a
  web build in the worktree it serves the app, and `/api/me/following` and `/api/starters` set the fan
  cookie on whatever host it was opened at.
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
       SPORTS_FOLLOW_YOUTUBE_KEY_FILE=<scratch>/none
       SPORTS_FOLLOW_RESEND_KEY_FILE=<scratch>/none
       SPORTS_FOLLOW_GOOGLE_CLIENT_SECRET_FILE=<scratch>/none
       SPORTS_FOLLOW_FCM_KEY_FILE=<scratch>/none
       SPORTS_FOLLOW_LINEAR_KEY_FILE=<scratch>/none
       SPORTS_FOLLOW_ADMIN_PASSWORD_FILE=<scratch>/none
       LLM_GATEWAY_URL=http://127.0.0.1:9
       LLM_PROVIDERS_KEYS_FILE=<scratch>/none
       SPORTS_FOLLOW_MAIL_LOG=1

   That leaves ESPN (unofficial, no key, development only) and Lichess without a token; the research
   agent is off (nothing answers at its gateway address) and sign-in codes go to the log. Turn a paid
   source on (its real token file) only when the round needs it, say so on the board, and keep hunters
   off loops against it. The admin panel, the feed's YouTube, Android pushes and Linear are off too: the
   real keys are production's (the `secrets` skill). To test the panel, give the stack a password of its
   own: `SPORTS_FOLLOW_ADMIN_PASSWORD_FILE=<scratch>/admin-password <venv>/bin/python -m sports_follow.admin password`,
   and set that same `SPORTS_FOLLOW_ADMIN_PASSWORD_FILE=<scratch>/admin-password` in the environment above
   in place of `<scratch>/none`.
6. **The API and web**, as preview servers: two entries in a `.claude/launch.json`, never committed
   (they hold your scratchpad's paths):

       {"name": "<name>-api", "runtimeExecutable": "/usr/bin/env", "port": 18421, "url": "http://<name>.localhost:18421",
        "runtimeArgs": [<the environment above, one "NAME=value" each>,
          "<venv>/bin/uvicorn", "sports_follow.server:app", "--app-dir", "<worktree>",
          "--port", "18421", "--timeout-graceful-shutdown", "3"]},
       {"name": "<name>-web", "runtimeExecutable": "/bin/sh", "port": 15173, "url": "http://<name>.localhost:15173",
        "runtimeArgs": ["-c", "cd <worktree>/web && SPORTS_FOLLOW_API=http://127.0.0.1:18421 PATH=$HOME/.local/node/bin:$PATH npm run dev -- --port 15173 --strictPort"]}

   **Which `launch.json`:** `preview_start` reads the one in your session's own folder, whoever ran step 1,
   not the stack's worktree. A session in the main checkout gets the main checkout's file, which git
   tracks and other sessions start their servers from. There, add the entries only to start the two
   servers and put the file back at once: copy it to your scratchpad first, `preview_start` both, copy it
   back, and check it with `cmp` against your copy. Its `git status` should then be what it was before
   you started, which needn't be clean: never `git checkout` or `git restore` it, as another session's
   edit may be in it. `preview_stop` goes by server id, so the entries needn't stay; put them back only
   to restart a server. A session in a worktree of its own does the same with that worktree's file.

   **Both `url`s matter.** Without one, `preview_start` opens its tab at `http://localhost:<port>/`, and
   the API's tab there sets a fan cookie on `localhost`, replacing the main app's in the browser pane (a
   tab closed afterwards doesn't bring it back). After starting, `tabs_context` should show both tabs on
   `<name>.localhost`. Open the app only at `http://<name>.localhost:15173`, never at localhost:15173.

## The worker: only if the round needs it

Only when the round tests live polling, moments or alerts. Start it from the worktree (arq runs the code
of the directory it starts in) with the environment above, as a background task:
`cd <worktree> && env <environment> <venv>/bin/arq sports_follow.worker.WorkerSettings`. Put its task id
or PID in the board's header, and stop it the moment the round no longer needs it. (Round two's worker
once ran all night.)

## A Twitch player on the web

Twitch's player takes a plain-http page as its parent only at exactly `localhost`. At
`http://<name>.localhost` its CSP answers `frame-ancestors https://<name>.localhost` and the frame is
blocked. To try it, run a second Vite on `localhost` (say :15184) from a scratch config whose `/api`
proxy strips the cookie both ways, so the main app's `localhost` fan cookie is neither sent nor replaced:

    // <scratch>/vite.nocookie.ts
    import base from '<worktree>/web/vite.config.ts'

    export default {
      ...base,
      server: {
        proxy: {
          '/api': {
            target: 'http://127.0.0.1:18421',
            changeOrigin: false,
            configure: (proxy: { on: (e: string, f: (...a: any[]) => void) => void }) => {
              proxy.on('proxyReq', (req: { removeHeader: (h: string) => void }) => req.removeHeader('cookie'))
              proxy.on('proxyRes', (res: { headers: Record<string, unknown> }) => { delete res.headers['set-cookie'] })
            },
          },
        },
      },
    }

Start it from `<worktree>/web` with `PATH=$HOME/.local/node/bin:$PATH npx vite --config <scratch>/vite.nocookie.ts --port 15184 --strictPort`.
Every page there is a fan with no cookie, so it's for the player, not for follows or sign-in.

## The Android app against the stack

- **A busy emulator stays theirs.** If the running emulator has another session's `adb reverse`s
  (`adb -s <serial> reverse --list`), or the app's `debug_http_host` there points at a Metro that isn't
  yours (`adb -s <serial> shell run-as io.slam.app cat shared_prefs/io.slam.app_preferences.xml`), boot
  the other AVD (`emulator -list-avds`; with only one, a second copy of it with `-read-only`) headless
  on a port of its own: `~/.local/android-sdk/emulator/emulator -avd <avd> -port 5560 -no-window`, as a
  background task. With two emulators attached, `adb` without `-s` fails ("more than one device"), so
  pass `-s emulator-5560` to every `adb`.
- **Point the app at the stack**, or it talks to stann.in (`mobile/src/api.ts`). Run a Metro of your own
  from `<worktree>/mobile` on a free port with `EXPO_PUBLIC_API_URL=http://localhost:18421`, reverse both
  ports (`adb -s emulator-5560 reverse tcp:18421 tcp:18421`, and the same for Metro's), and write
  `debug_http_host` = `localhost:<metro port>` into that prefs file. Through `localhost` the app's
  `/watch` pages give Twitch `parent=localhost`; `10.0.2.2` would be a plain-http parent it refuses.
- **The `/watch` page is cached for an hour** (`Cache-Control: public, max-age=3600`), so the app's
  WebView plays a stream it has loaded before even with the API stopped. To see "Couldn't load the
  player", use a stream id it hasn't loaded yet.
- Afterwards uninstall what you installed, remove your reverses (`adb -s emulator-5560 reverse
  --remove-all`), stop your Metro and the emulator (`adb -s emulator-5560 emu kill`).

## When the round ends

Tear it all down; nothing of it should outlive the round:

1. Stop its processes: `preview_stop` for the API and web, the worker's task, a cookie-free Vite, an
   emulator and Metro of yours, and any server a hunter started. Then check nothing still runs from the
   worktree (the worker listens on no port): `lsof -d cwd | grep '.claude/worktrees/<name>'` prints
   nothing. Check the `launch.json` you started from is back as it was.
2. Remove its Redis: `docker rm -f sports-follow-redis-<name>`. Never the main `sports-follow-redis`.
3. Drop the database once nothing is connected to it:
   `docker exec sports-follow-postgres psql -U sports -d sports_follow -c "DROP DATABASE sports_follow_<name>;"`
4. Remove the worktree: `git worktree remove <worktree>` (`--force` only after checking `git status`
   there shows nothing you need).
