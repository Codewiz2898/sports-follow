# Redis keys

Redis holds nothing that isn't somewhere else too: locks, counters, backoffs, caches, the job queue and
the hot copy of each card (Postgres has the real one). Reading is always safe. Changing or deleting a key
has effects, listed below; on production, ask Bharath first.

**Reading safely:** `GET`, `TTL`, `EXISTS`, `TYPE`, `HGETALL`, `ZRANGE … WITHSCORES`, `ZCARD`, and
`--scan --pattern '<glob>'` (never `KEYS`, which blocks the server). Quote patterns and keys: Sportmonks
budget buckets have spaces in them. Some keys and values hold raw fan ids or client addresses
(`research:*`, `sourcesearch:*`, `weberrors:*`, `*-address*`, `session:*`): don't paste them into
issues or PRs.

## Locks: `lock:<name>`

Each is a `SET NX EX` holding a random token, released by its holder (`bus.py`). Deleting one that's
really held lets a second copy of the job run alongside (double polling, double model or source spend).
Deleting one left by a crash is fine, but they all expire on their own.

| Key | TTL | Held by |
|---|---|---|
| `lock:build:<player_id>` | 20 min, renewed while the agent works | an agent card build |
| `lock:structured:<player_id>` | 20 min | a structured refresh |
| `lock:live:<player_id>` | 5 min | an agent live check |
| `lock:bindtry:<player_id>` | 6 h, never released | a rebind attempt (a cooldown) |
| `lock:poll:<event_id>` | 120 s past the next round | a `poll_event` job |
| `lock:lane:<system>` (`sportmonks_football`…) | 120 s past the next round | a `poll_lane` job |
| `lock:engine-lane` | 120 s | the chess engine lane |
| `lock:history-lane` | 10 min | the history lane |
| `lock:registry-import` | 6 h | the weekly import (or one by hand) |
| `lock:registry-<system>` | the source's `roster_every` (PUBG 6 h) | a roster import (a cooldown) |
| `lock:retention` | 3 h | the daily prune |

## Cards and streams

- `card:<player_id>`: the card as JSON, served before Postgres, with no TTL. Unfollowed players' cards
  get a day's TTL from the prune. Deleting it is harmless (the API rebuilds it from `player_card`);
  editing it serves wrong data.
- Channel `player:<player_id>`: card updates, build progress and moments for open apps (SSE). Watch with
  `SUBSCRIBE player:<id>`, which only reads.
- `buildretry:<player_id>`: JSON `{"failures", "retry_at"}`, a ready card's rebuild backoff. Deleting it
  rebuilds at once (model spend).
- `refresh:<player_id>`: the refresh button's minute.

## Polling

- `polled_at:<event_id>`: the epoch second of the last poll, kept 6 h. Compare it with `date +%s`.
- `polled:<event_id>`: present while the last poll is recent. Deleting it makes the next poll a gap
  (live.md), so that poll tells no moments.
- `engine:queue` (zset), `engine:want` (hash), `engine:latest:<game>`, `engine:pos:<hash>`: chess engine work and its cache.
- `history:resting`: a hash of scorecard reads backing off.
- `late:tick`, `late:warm_sources`: skip a backlog of missed cron runs.

## Source budgets: `budget:*` (`adapters/budget.py`)

`<budget>` is the budget's name, not the source's system: `sportmonks` (football and cricket share it),
`pandascore`, `liquipedia`, `pubg`, `pubg-matches`, `opendota`, `lichess`, `lichess-fide`,
`lichess-anonymous`, `espn`. `<bucket>` is empty for every budget but Sportmonks, so most keys have `::`
in them (`budget:pandascore::3600:<n>`). Sportmonks buckets are like `Fixture` or
`Cricket fixtures/{id}` (literally `{id}`).

| Key | Holds |
|---|---|
| `budget:<budget>:paused` | The epoch second a pause after a 429 ends. Paused while that's later than now. Every lane waits, live included. |
| `budget:<budget>:<bucket>:<seconds>:<n>` | Requests in the window number `n` (`now // seconds`) |
| `budget:<budget>:<bucket>:left` | What the source last said was left, until its reset |
| `budget:<budget>:<bucket>:fan:<seconds>:<n>` | Fans' share (Liquipedia, PUBG) |
| `budget:use:<budget>:<lane>:<sent\|refused>:<h\|d><n>` | Counts by lane, for `python -m sports_follow.usage` |

Allowances: Sportmonks 2,000 an hour per entity (1,000 kept for live games and fans), PandaScore 1,000 an
hour (300 kept), Liquipedia 60 an hour, PUBG 10 a minute, Lichess 120 a minute with a token. A
background lane stops at the reserve; live and fans go on to the limit.

**Don't delete these** (and never without Bharath): it resets the counts, and the app can overspend a
paid quota or get banned (Liquipedia). Deleting `paused` sends requests straight back to a source that
just said 429.

`python -m sports_follow.usage` prints each source's sent and refused counts by lane, for the hour and the
day. It only reads. The Sources dashboard shows the same, and the allowance used, left and paused.

## Source cache: `src:*` (`adapters/cache.py`), in the cache Redis

On production these live in a Redis of their own, `c exec -T cache redis-cli`, not the one above; locally
in the main one unless `SPORTS_FOLLOW_CACHE_REDIS_URL` says otherwise.

`src:<budget>:<sha1>`: one response as the source sent it, zlib-compressed JSON `[fetched at, document]`
(binary, so `GET` shows noise). `<budget>` is the budget's name, as above (`sportmonks` for football and
cricket); the sha1 is of the request: for Sportmonks, PandaScore and Liquipedia `<path>?<sorted params>`
(`/football/players/7?include=teams`), for ESPN and Lichess the URL. Each key expires a day after it's
written, or after its ttl if that's longer; `TTL` says how long it has left. Whether a copy is fresh is
each caller's ttl against its fetched-at, so an old key isn't served as new.

Deleting a key, or `FLUSHALL` on the cache, is safe for the data but spends quota: each response is read
again from its source. On production, ask Bharath first. Never `FLUSHALL` the main Redis.

## The job queue (arq)

- `arq:queue`: a zset of job id → due time (ms). A job stays in it while it runs. Ids are random, except:
  - `poll-lane:<system>`, `engine-lane`, `send-moments`, `history-lane`
  - cron runs: `cron:<function>:<ms>`
- `arq:in-progress:<job id>`: present while a job runs.
- `arq:job:<job id>`: the job (pickled, so not readable). While it exists, the same id can't be queued again.
- `arq:retry:<job id>`: counts a job's tries.
- `arq:queue:health-check`: the worker's hourly summary (`j_complete=… j_failed=… j_ongoing=… queued=…`).

```bash
c exec -T redis redis-cli zcard arq:queue
c exec -T redis redis-cli --scan --pattern 'arq:in-progress:*'
c exec -T redis redis-cli zrange arq:queue 0 20 withscores
```

Deleting queue keys loses queued jobs. The tick queues periodic work again within a minute; jobs a request
queued (an `adopt_stragglers` after sign-in) are lost for good.

## Sign-in and limits (API)

`research:<fan_id>:<day>` (3 AI researches a day), `research-address:*`, `research-hour:*`,
`sourcesearch:*`, `weberrors:*`, `auth-*` (sign-in codes and their limits, keyed by a hash of the
email), `session:<hash>` (a signed-in session, cached 10 min; `""` means not signed in, which includes
a session that ended in the last 10 min).

Deleting these gives away spend (model, email, source) or voids a sign-in code. **Never edit
`session:*`**: it could sign a device in as someone else.
