# A card stuck in `building`, or `failed`

A player's page is the `player_card` row: `status` (`building`, `ready` or `failed`), `error` (the
message fans see, not the raw cause), `card` (the page as JSON), `built_at` (the last successful agent
build; NULL for a page a source published) and `version`. The API serves the Redis copy `card:<id>`
first, and Postgres only when that key is missing, so the two can disagree for a moment.

```sql
select c.player_id, p.name, p.sport, c.status, c.error, c.version, c.built_at, c.live_checked_at,
       c.card->'pending' as pending, c.card->>'pending_error' as pending_error,
       (select count(*) from follow f where f.player_id = c.player_id) as followers
  from player_card c join player p on p.id = c.player_id where c.player_id = 0;
select purpose, adapter, locator, health, priority from source_binding where player_id = 0;
select system, external_id from player_identity where player_id = 0;
```

## Two kinds of work

- **The agent build** (`build_card` job → `pipeline.build_card`) researches the player with the model, via
  the LLM gateway. It writes the profile, news and everything a source doesn't have. Only it moves a card
  out of `building` (or into `failed`).
- **The structured refresh** (`refresh_structured` job → `structured.refresh`) re-reads fixtures, recent
  results and season stats from the player's bound source (`source_binding`). No model is involved. It
  never changes `status`; it only fills the sections the source owns.

## Stuck in `building`

The tick (every minute) queues `build_card` for every `building` card that is followed and not locked
(`pipeline.rebuild_due`). So a card that stays `building` is one of:

| Check | How | Why |
|---|---|---|
| Nobody follows it | `followers` is 0 above | The tick only builds followed players. |
| Its sport is off | `SPORTS_FOLLOW_SPORTS` / `sports.on()` | A disabled sport isn't built. |
| A build is running | Redis `TTL lock:build:<id>` | The lock lasts 20 min (`BUILD_LOCK`) after the agent's last step, and is renewed while the agent works. A build thread can outlive its job's 15-minute timeout. |
| No worker, or the queue is backed up | `c ps worker`; the Jobs dashboard; Redis `ZCARD arq:queue` | Nothing runs the job. |
| The build fails every time without marking it | `build %s crashed` or `build %s failed: …` lines for the player, with no `failed` status after | `mark_failed` itself failed (Redis or the database down). |
| The worker stopped mid-build | `stopping: freed %d lock(s) jobs still held: …` | arq queues it again; the next worker picks it up. |

A failing agent shows in the worker's lines for the player (`player_id` field):
- `build <id> failed: <AgentError>`: `Model call failed (<kind>): …`, `The agent did not finish within its
  time limit.`, `…within its turn limit.`, `The model didn't answer.`, `The model declined this request.`,
  `The model's reply was cut off (max tokens).`
- `build <id> crashed` with a stack (an exception of ours; on the Errors dashboard).
- `player-report: … rejected, N errors, first: …` (the model's report didn't fit the schema).
- The job's own closing line still says `build_card done`: the build catches its failures.

The gateway: `LLM_GATEWAY_URL` (production: `http://gateway:8787`, the `gateway` container; locally the
launchd agent on :8787). Its daily budget for this app is `budgetUsdDaily` in
`deploy/gateway/gateway.json`; a spent budget fails builds with `budget_exceeded` (fans see "Today's AI
budget for this app is used up"). On production, `c logs gateway`.

## `failed`

A card becomes `failed` only when its first build failed (`mark_failed` while `building`). The fan sees
`player_card.error`; the raw reason is in that build's log line. **The tick never retries a failed
card.** It waits for a fan to follow again (`POST /api/follows`), which costs one of their 3 AI
researches a day. `SPORTS_FOLLOW_AGENT=0` (agent off) fails an unbound player's first build with "No
live source has this player".

A **ready** card whose rebuild fails stays ready: `error` is set, and the next try waits in
`buildretry:<id>` (Redis JSON `{"failures": n, "retry_at": …}`): 30 min, then 1 h, 2 h, 4 h, then 6 h
(`BUILD_RETRY`, `CARD_MAX_AGE`). A source-published card (`pending` set) whose agent build fails gets
`pending_error`.

## A card that's ready but old

- Fixtures and results come from the structured refresh, due when `freshness.fixtures` is older than 30
  min (`FIXTURES_MAX_AGE`) and the source has room (`structured.spare`). It backs off after failures:
  `source_binding.health` `{checked_at, last_ok, failures, error}`, 2 min doubling to 2 h.
- Its lock: `lock:structured:<id>` (20 min). A rebind is tried at most every 6 h (`lock:bindtry:<id>`).
- Its lines: `refresh <id> failed: …`, `refresh <id> crashed`, `refresh <id>: <source> is down to what
  live games need; next tick`, and `bind <id>: …` lines for binding.
- The refresh button (`POST /api/players/<id>/refresh`) never builds: it queues a structured refresh, at
  most once a minute per player (`refresh:<id>`).
