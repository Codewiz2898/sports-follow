# A live game's score is stale

A game is an `event` row. Its `status` goes `scheduled` → `armed` → `live` → `final` (or `postponed`). A
source polls it only if it has a `live_binding` (`{"adapter": <source>, "locator": {…}, "cadence": <s>}`).
An event with no binding is one the agent guessed: only agent live checks see it, and only with
`SPORTS_FOLLOW_AGENT_LIVE=1`. Each poll that sees a change appends an `event_state` row (the newest is the
score) and the player's `player_line`.

```sql
-- The player's live game, as their card says (while the game is on; a finished one is cleared from the card):
select c.card->'live'->>'event_id' as event_id, c.card->'live'->>'source' as source, c.card->'live'->>'as_of' as as_of,
       c.is_live, c.live_checked_at from player_card c where c.player_id = 0;
-- The game:
select id, key, status, start_utc, updated_at, live_binding from event where id = 0;
select as_of, status, score_label, clock_label from event_state where event_id = 0 order by as_of desc limit 5;
-- Who it's polled for: a followed player in the game with an id in the game's source (player_identity;
-- source_binding says which source a player's fixtures come from).
select ep.player_id, p.name, i.system, i.external_id, (select count(*) from follow f where f.player_id = ep.player_id) as followers
  from event_player ep join player p on p.id = ep.player_id
  left join player_identity i on i.player_id = ep.player_id and i.system = (select live_binding->>'adapter' from event where id = 0)
 where ep.event_id = 0;
```

## How polling runs

- **Arming.** Each minute the tick arms a `scheduled` game once its start is within 30 min
  (`ARM_BEFORE_START`; football 75 min, for team sheets). A game more than 6 h past its start is
  never armed.
- **Polling** an armed or live game with a followed player:
  - A source that reads many games in one request (Sportmonks football, PandaScore, Liquipedia,
    PUBG) gets a **lane**: one `poll_lane <source>` job, lock `lock:lane:<source>`.
  - Any other source gets a `poll_event <event_id>` job per game, lock `lock:poll:<event_id>`.
  - A job polls for up to 10 minutes (`POLL_SESSION`), then the next tick queues another.
  - Locks last 120 s past the next round, so a dead job's lock clears itself.
- **Cadence**, in seconds while live (60 otherwise, and 30 after an error):
  - Sportmonks football and Lichess: 15
  - Sportmonks cricket and ESPN: 10 (ESPN cricket 8)
  - PandaScore: 60
  - PUBG: 120
  - Liquipedia: 300

  The cadence is copied into `live_binding` when the game is stored.
- **Marks in Redis:** `polled_at:<event_id>` is the epoch second of the last poll (6 h TTL).
  `polled:<event_id>` marks that a recent poll ran; when it's gone, the next poll starts after a gap.
- **Closing.** A poll sees `final`; or a refresh does. Unseen games close by themselves: unbound ones
  after 12 h, bound ones 6 days after their start. Nothing is logged when that happens.

## Why it's stale

Work down until one matches.

| Check | How | What it means |
|---|---|---|
| The game isn't armed or live | `event.status` | `scheduled` past its start: the tick hasn't armed it. Over 6 h late, it never will be. `final` or `postponed`: polling has stopped. |
| No `live_binding` | `event.live_binding` is null | No source polls it; only agent live checks would, if on. |
| Nobody to poll for | the "who it's polled for" query: no row with followers and a `system` | `_watching` finds no followed player with an identity in that source, so each poll ends `idle`. |
| Last poll long ago | Redis `GET polled_at:<id>`, compared with `date +%s`; the Jobs dashboard's "games behind"; the `sf-live-behind` alert | A game is behind after 3 rounds (at least 3 minutes). |
| The poll job isn't running | `TTL lock:poll:<id>` or `lock:lane:<source>`; Redis `ZRANGE arq:queue 0 -1 WITHSCORES`; `tick: … polls, … poll lanes` lines | Missing lock and no queued job: the tick isn't queuing it (status, binding, followers). A lock with nothing moving: the job is stuck. Waiting jobs: the worker is full (Jobs dashboard). |
| The source refuses | Redis `GET budget:<source>:paused` (epoch second it ends, if it's later than now); `poll <id>: …` and `poll lane <source>: …` warning lines | A 429 pauses that source for every lane, live included: 60 s, or 180 s for Lichess. A spent allowance refuses background work first and live last. See redis.md, "Source budgets". |
| The source answers but nothing changes | `event_state` has no new rows; `poll lane <source>: no data for event <id> (…)` | The source isn't updating the game, or a lane's batch doesn't include it (outside the plan's leagues, say). |
| It changes but the card doesn't | `poll <id>: <score> \| <clock> -> N card(s)` lines appear, but the card's `live.as_of` is old | Only `ready` cards are updated. Check `player_card.status`, and the Redis copy `card:<player_id>`. |
| The poll crashes | `poll <id> crashed` or `poll lane <source> crashed` with a stack | A bug: the Errors dashboard groups it. |

The trace of a poll is the job's span with a `poll round` or `lane round` span for each round, and the
source's `GET <host>` request inside it (Tempo: `{span.server.address="api.sportmonks.com"}`).

## Missed moments after a gap

A poll that comes after a gap (more than 5 minutes, or 3 of a slow source's rounds), or the first look at
a game already under way, treats what it sees as the baseline (`structured.baseline`). It tells only a
result or a line-up, never the goals that happened meanwhile. A fan who "didn't get a push" for a goal
scored during a worker restart is this.
