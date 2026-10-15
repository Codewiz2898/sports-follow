# A fan didn't get a push

The chain, in order. A push is lost at the first link that's broken, so walk it from the top and stop
where it breaks. `$PSQL` is the read-only database shell for where you are (SKILL.md, "Where things
are"), and the queries below go in its stdin.

1. **The moment.** A poll compares the game with the last poll and stores what changed as a `moment`
   row (`structured.apply_poll` → `moments.detect` → `notify.record`). A rebuild stores news moments
   the same way (`pipeline.py`). No row means nothing was found to tell, so the cause is upstream: the
   game wasn't polled, or the source didn't say. See live.md.
2. **The sender.** The batch is claimed (`moment.sent_at` set) and planned for every follower by
   `notify.send`. A batch nobody claimed (the worker restarted) is swept by the tick after 60 s, while
   it's under 12 h old.
3. **The fan's own rules** (`alerts.plan`): they follow the player, their level wants this kind of
   moment, they didn't mute the game, it isn't too late, and it wasn't taken back.
4. **Their devices.** One push per device, at most the newest 10 (`push_subscription`). Inside a device's
   quiet hours (in its own time zone) the push arrives silently, whatever the moment.
5. **The push service.** It took the push (`notification.delivered`) or refused it (a warning line).
   After that, it's the phone's to show.

## Who is "fan X"?

Fans are ids (`follow.fan_id` and the rest). A signed-in fan's id is `account.fan_id` (`acct-…`); an
anonymous fan's is the random id in their cookie. Logs name a fan only by a hash: the first 12 hex
characters of the id's SHA-256 (`logs.fan`).

```sql
-- From an email:
select id, fan_id, email, created_at, last_sign_in_at from account where email = lower('x@example.com');
-- From a log line's fan hash:
select distinct fan_id from follow where left(encode(sha256(convert_to(fan_id, 'UTF8')), 'hex'), 12) = '43c0ae82ffd2';
-- From a push endpoint the fan sent (the Alerts page shows none; ask them only as a last resort): push_subscription.endpoint.
```

If the hash matches nothing in `follow`, try `push_subscription` and `notification` the same way. A fan
who follows nobody has no rows to find. Never write a fan's id or email into an issue, PR or commit:
use the hash.

**Player Y:** `select id, name, sport, slug from player where name ilike '%kohli%';`

## Everything at once

Put the fan's 12-character log hash (or their fan id) in `who` and the player's id in `player`, then run
it. It reads only, and finds the fan's id from the hash itself, so the id never has to be typed out.

```sql
with args as (select '43c0ae82ffd2'::text as who, 0 as player, interval '2 days' as back),
fan as (select coalesce((select f.fan_id from (select fan_id from follow union select fan_id from push_subscription union select fan_id from notification) f, args
                          where left(encode(sha256(convert_to(f.fan_id, 'UTF8')), 'hex'), 12) = args.who limit 1), (select who from args)) as id)
select 'follow' as what, f.created_at::text as at, coalesce(f.alert_rules->>'level', 'key (default)') as detail
  from follow f, args, fan where f.fan_id = fan.id and f.player_id = args.player
union all
select 'device ' || d.id, d.created_at::text,
       split_part(d.endpoint, '/', 3) || ' ok:' || coalesce(d.last_ok_at::text, 'never') || ' failures:' || d.failures
       || ' tz:' || d.timezone || ' quiet:' || coalesce(d.quiet_start || '-' || d.quiet_end, 'none')
  from push_subscription d, fan where d.fan_id = fan.id
union all
select 'moment ' || m.id, m.created_at::text,
       m.kind || '/' || m.level || ' p' || m.priority || ' event:' || coalesce(m.event_id || ' ' || e.title, '-') || ' sent:' || coalesce(m.sent_at::text, 'NO')
       || coalesce(' corrects:' || m.corrects, '') || ' | ' || m.title
  from moment m left join event e on e.id = m.event_id, args where m.player_id = args.player and m.created_at > now() - args.back
union all
select 'taken back ' || c.corrects, c.created_at::text, 'by correction ' || c.id || ' | ' || c.title
  from moment c join moment m on m.id = c.corrects, args where m.player_id = args.player and c.created_at > now() - args.back
union all
select 'muted event ' || n.event_id, n.created_at::text, ''
  from notification_mute n, fan where n.fan_id = fan.id
union all
select 'push ' || n.id, n.created_at::text,
       n.kind || ' moments:' || n.moment_ids::text || ' loud:' || n.loud || ' delivered:' || n.delivered || '/' || n.devices
       || ' opened:' || coalesce(n.opened_at::text, 'no') || ' | ' || coalesce(n.lines->>0, '')
       || ' | on devices: ' || coalesce((
            select string_agg(d.id || ' at ' || to_char(n.created_at at time zone d.timezone, 'HH24:MI')
                   || case when d.quiet_start is not null and d.quiet_end is not null and d.quiet_start <> d.quiet_end and (
                        case when d.quiet_start < d.quiet_end
                             then (n.created_at at time zone d.timezone)::time >= d.quiet_start::time and (n.created_at at time zone d.timezone)::time < d.quiet_end::time
                             else (n.created_at at time zone d.timezone)::time >= d.quiet_start::time or (n.created_at at time zone d.timezone)::time < d.quiet_end::time end)
                        then ' QUIET HOURS (silent)' else '' end, ', ')
              from push_subscription d where d.fan_id = n.fan_id), 'none now')
  from notification n, fan, args where n.fan_id = fan.id and n.created_at > now() - args.back
order by at;
```

`on devices` lists the fan's devices as they are now (a device removed since is missing), each with the
push's time on it and whether that fell in its quiet hours.

## Reading it

Find the goal's `moment` line, then match it against the rest:

| What you see | Why there was no push |
|---|---|
| No `moment` row for the goal | Nothing was found. See "No moment" below. |
| `sent:NO`, and the moment is minutes old | The sender didn't claim it. The tick sweeps such moments after 60 s, but only up to `alerts.LATE_TTL` (12 h). A live moment older than 15 minutes by then is dropped as late. Look for `sending moments [...] failed` or a worker restart (`c logs worker`). |
| No `follow` row | The fan doesn't follow the player (under this fan id). A fan who followed while signed out and then signed in has their follows under the account's id now (`accounts.adopt`). |
| `follow` level `results` or `off` | `results` gets only the final result; `off` gets nothing (`moments.wanted`). A goal is a `key` moment, so `key` (the default) and `everything` get it. |
| The moment's level is `minor` | Only `everything` gets minor moments (a yellow card, coming on). |
| No `device` rows | Alerts aren't on for any device of this fan, or every device was removed. A device goes when its push service says it's gone (404 or 410), after 5 failures in a row (`notify.MAX_FAILURES`), when the session that turned it on is signed out, or when the account is deleted. Its removal shows in the logs as `push to https://… failed (410)`. |
| `muted event N` matching the moment's `event:` | The fan tapped "Mute this game". |
| `taken back` naming the goal | A correction took it back (VAR ruled it out). If it went before the correction, the fan got the correction on the same notification; if not, nothing went. |
| No `push` row, though the rules above all pass | Compare the times: a live moment more than 15 minutes old when sent isn't pushed (`alerts.ttl`: 15 min live, 1 h line-up, 12 h result and news). Otherwise check the moment's `player_id`: the fan follows a different player row for the same person (two pages; `player.wikidata_qid`). |
| A `push` row whose `moments:` lists the goal's id but whose first line is another moment's | The goal went, as a line of a notification led by a bigger moment in the same game. Each game is one notification, replaced as it goes (tag `e<event>`). |
| `delivered:0/N` | Every push service refused it. The warning line `push to https://<host>/… failed (<status>)` says why. 404/410 means the device is gone (and was removed); 413 means too big; 401/403 means the VAPID key changed (`notify keys` was run again) and every device must subscribe again; a timeout means the push service didn't answer. |
| `delivered:N/N` and `QUIET HOURS (silent)` on the device | It arrived silently. `loud` is the plan before each device's quiet hours, which then make it quiet on that device whatever the moment's priority (`notify.quiet`). A silent push still shows in the phone's notification tray, replacing that game's earlier notification, but without sound or vibration (the service worker shows it `silent`). Check this before the next two rows. |
| `delivered:N/N`, `loud:false` | It arrived silently, by the noise rules. Sound is rationed: one loud push per game every 3 minutes (`GAME_COOLDOWN`), 6 an hour per fan (`LOUD_PER_HOUR`), unless the moment's priority is 90 or more; a game's start is quiet if the fan already heard about that game. |
| `delivered:N/N`, `loud:true`, outside quiet hours | The push service has it, with sound. If the phone didn't show it: the phone's notification settings for the site or app, battery saver holding it, or the browser profile it was turned on in. `opened:` says whether they tapped it. |

### No moment

First, check it isn't under another player row (two pages for the same person; `player.wikidata_qid`):

```sql
select m.id, m.player_id, p.name, m.kind, m.created_at, m.title from moment m join player p on p.id = m.player_id where m.event_id = 0 order by m.id;
```

Then the game has to have been polled, and the poll has to have seen the change:

- **Was it polled?** See live.md: the event's `status`, its `live_binding`, and `polled_at:<event_id>` in Redis.
- **A gap in polling.** A poll after a gap of more than 5 minutes (`POLL_GAP`), or 3 of a slow source's
  rounds, takes what it sees as the new baseline and tells nothing that happened meanwhile
  (`structured.baseline`). That covers a worker restart, an hour of a source's errors, or the first look
  at a game already under way. In that case a goal scored during the gap is never a moment, by design.
- **The source didn't say.** The player's line (`player_line`, newest `as_of` first) never showed the
  goal, or the source credits it to an athlete id the player isn't bound to.

```sql
select id, status, title, start_utc, live_binding from event where id = 0;
-- Each change the polls stored, and the time since the one before:
select as_of, status, score_label, clock_label, as_of - lag(as_of) over (order by as_of) as since_last
  from event_state where event_id = 0 order by as_of;
select as_of, stats from player_line where event_id = 0 and player_id = 0 order by as_of;
```

A poll stores a row only when the status, score, clock or state changed. A live football game's clock
moves every minute, so its rows come about a minute apart. A jump in the clock across one row (11', then
41' with a new score) means the polls stopped in between: the goal fell in a gap, and the poll after it
took 1-0 as its baseline. A long time between rows with the clock barely moving (half-time) is only
nothing changing.

Why polling stopped is in the logs, not the database: worker restarts (`stopping: …` lines, a deploy),
the game's `poll_event` or `poll_lane` job lines, and the source's errors (live.md). `polled_at:<event_id>`
in Redis expires after 6 h, so for an older game only production's logs (Grafana keeps them) can say.
Locally, they were in a terminal.

## In the logs

- The send: `moments [<ids>]: <n> push(es) to <delivered> of <devices> device(s)` on `sports_follow.notify`,
  in the trace of the poll that found them. In Grafana, Loki:
  `{service_name="sports-follow-worker"} | scope_name="sports_follow.notify" |= "<moment id>"`.
  No such line for the moment means no push was planned for anyone: every follower was filtered out,
  or no follower has a device.
- Refusals: `push to https://<host>… failed (<status>)` on `sports_follow.notify`, with `push_service` and `status`.
- The **Notifications** dashboard has moments found, pushes by result and push service, opens, and how
  long a moment waited for its push.
- `python -m sports_follow.notify stats [days]` gives pushes delivered, loud and opened by lead moment kind.
  It only reads.

Don't send a test push (`POST /api/push/test`, `notify.send_test`) to a fan's devices to check: it
reaches a real person's phone. Ask Bharath first.
