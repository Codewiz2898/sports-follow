# Runbook: what to check

What to look at to know production (stann.in) is working for fans, how often, what's normal, and what to do when
an alert emails. Everything here is in the stack's Grafana (Grafana Cloud), in the **Sports Follow** folder:
every dashboard has a **Sports Follow** menu to the others, and Health and Performance a **Runbook** link back here.

Reading is always safe: dashboards, logs, traces, `c ps`, `c logs`. Anything that changes production (a restart,
a deploy or rollback, a write to the database or Redis, spending a source's or the model's quota, a test push) is
Bharath's call: the `debug` skill (`.claude/skills/debug/SKILL.md`) lists what's safe and how to get in.
On the server, `c` is `docker compose -f deploy/compose.yaml` in `/opt/sports-follow` (deploy/README.md, "Operating").

## Every day (5 minutes)

Open **Health**, last 24 hours. Green everywhere means done.

1. **Top row.** Reachable from outside at 99.5% or more (a probe's blip or two a day is normal); API answers
   without a server error at 100% (or 99.9%); Server errors 0. Errors logged: open the **Errors** dashboard if it's more than yesterday; a group
   first seen today is new.
2. **Alerts firing now.** Empty, or each one is in [When an alert emails](#when-an-alert-emails).
3. **Fans' journeys.** Every journey's Answered is green. Read it with its Requests: with 2 sign-ins a day, one
   failure is 50%. A journey with no requests has no row.
4. **Sign in.** Codes used near 100% on a day codes were sent. Any **Code limits hit** or **Send failed** is
   worth a look ([Sign-in](#sign-in)).
5. **Bottom row.** Games behind 0, Not pushed 0, Delivered 99% or more, Feed (24 h) above 0, Cards failed 0.

Then **Performance**, last 24 hours:

6. **API p95** under about 250 ms. **Time taken by journey**: no journey's p95 over 1 s, except Search and
   follow, where following builds or loads the player's page (1 to 2.5 s is normal).
7. **Outside p95** around 1 s: that's the trip from India to the server in Sydney, not our work (see
   [What's normal](#whats-normal)).

## Every week (15 minutes)

- **Performance, last 7 days.** Each journey's p95 against the week before (the time picker's **‹** moves the
  range back). Slower with the same traffic: the slowest routes table says where, and its traces say why.
- **Sources, last 7 days.** Allowance used by source and bucket: one at 90% most hours needs a bigger plan or
  a smaller budget (`/admin`, Sources). 429s and errors by source and status: a source answering 401 or 403
  has refused our key, and no alert fires on that ("A source keeps failing" counts only 5xx and no answer).
- **Feed.** Coverage by sport (followed players with something to watch this week), and where the funnel
  drops.
- **Errors, last 7 days.** A group that keeps coming back is a Linear issue.
- **The server.** Performance's CPU, memory and load: memory free has stayed above 40%. Off-site backups:
  `deploy/offsite-backup.sh list` on the server.
- **Grafana Cloud's usage** (Cost Management and Billing in Grafana): logs, traces and metrics inside the free
  tier, and Synthetic Monitoring under its 100,000 checks a month (the uptime check takes about 89,000).

## After a deploy

About 10 minutes after the Deploy run ends:

- `curl -s https://stann.in/api/health`: its `commit` is the one deployed.
- **Health, last 30 minutes:** Server errors 0, every journey green, nothing new on **Errors** (the web app's
  errors carry the build's `version`).
- **Performance, last 1 hour:** API p95 hasn't jumped from the hour before.
- **Jobs and live polls:** the job queue chart is moving (the worker is back) and Games behind is 0.

If it's bad, rolling back is Bharath's (deploy/README.md, "Deploying").

## Before and during a big game

A launch, a final, anything many fans follow live.

- **An hour before:** on **Jobs and live polls**, the game is there (armed games are polled once a minute). On
  **Sources**, its source's allowance has room in the `live` lane this hour.
- **During:** **Health** over the last 15 minutes, refreshing every minute: Games behind 0, Oldest score under 3
  minutes, Not pushed 0, Delivered. **Performance**: Push wait p95 (normal is under a second).
- **After:** **Notifications**: pushes delivered, failed and opened.

## The dashboards

| Dashboard | Open it for | Shows |
|---|---|---|
| **Health** | the daily look; "is it working?" | reachable from outside, alerts firing, server errors and errors logged; each fan journey's requests, server errors, refusals and share answered; sign-in codes sent, used, wrong, held back by limits, failed to send; late games, old scores, unsent moments, pushes delivered, the Feed's last day, failed cards |
| **Performance** | "is it fast?" | API time taken (p50, p95, p99) overall, by journey and by route; from outside, by phase; moment to push; worker jobs' time and queue wait; the sources' time; the server's CPU, memory and load |
| **API** | a failing or slow route | requests, errors and time by route, from each request's log line; the failed requests (each opens its trace) |
| **Errors** | an exception | errors from the API, worker and web app, grouped by what failed and where, with their stacks |
| **Jobs and live polls** | late scores, a stuck queue | live games and since each was polled; the job queue; runs, failures and times by job; live poll rounds; cards |
| **Notifications** | alerts fans did or didn't get | moments found; pushes delivered, failed (by push service and status) and opened; moment to push |
| **Sources** | a source failing or slowing us | requests, errors, 429s and time by source; requests by lane; allowance used and left; pauses |
| **Feed** | the Feed going stale | the funnel by sport (read, matched, kept, judged, passed, served); refusals by reason; coverage; the judge; moment searches; YouTube's quota |

Health's counts come from each API request's log line, so they're exact. Times come from the request metric's
buckets (Performance), which are approximate, and which miss each process's first request on a route: a rare
route can be missing from the slowest routes until it's been asked a few times.

Also in Grafana: **Testing & synthetics** (the uptime check's results by probe, with each failure's reason) and
**Explore** (any log line or trace: the `debug` skill has the queries).

## What's normal

Production, the week to 23 October 2026:

| | Normal | Note |
|---|---|---|
| Requests | 3,000 a day over the week, 5,700 on its last day | Live and match pages are half; Today a quarter |
| Server errors (5xx) | none in the week | so one is news |
| Reachable from outside | 99.89% | 23 of about 20,000 checks failed, one probe at a time: blips, not outages |
| API time taken | p50 20 ms, p95 100 to 170 ms, p99 250 ms | the slowest routes are `/api/follows` (p95 2.3 s), `/api/starters` and `/api/auth/email/start` (about 0.5 s) |
| From outside | p95 about 1 s | from Mumbai: DNS, then about 250 ms each to connect, for TLS, and for the answer: the distance to Sydney, three times |
| Moment to push | p95 0.1 s | |
| Job queue wait | p95 about 2 s | lane jobs (`engine_lane`, `poll_lane`) run for minutes by design |
| Server | CPU at most 35% busy, memory at least 40% free | |

At today's traffic a share swings on a single request: always read it with the count beside it. Look at these
again after launch, when there's more to read.

## When an alert emails

Grafana emails when a rule starts firing, every 6 hours while it lasts, and once when it clears. The email's
summary says what it saw and where to look; this says what to check and what to do. **Bharath** marks a step
that's his to take or approve.

### The server (`deploy/grafana/rules.json`)

| Alert | Check | Do |
|---|---|---|
| The server stopped sending metrics | Health's Reachable from outside: if the probes still get answers, only Alloy stopped (`sudo systemctl status alloy`); if not, the server is down | Restart Alloy, or the instance in Lightsail's console (**Bharath**) |
| Fans can't reach the app | Grafana's **Testing & synthetics**: each probe's error (DNS, certificate, timeout, status). `curl -sv https://stann.in/api/health`. If a container alert fires too, it's inside | DNS, the certificate (Caddy), Lightsail's firewall, or the app: the matching alert's row (**Bharath** for each) |
| The uptime check stopped reporting | Synthetic Monitoring is off, the check was removed, or the free checks ran out | `deploy/grafana/apply-alerts.sh` puts the check back |
| The server checks stopped running | `sudo systemctl status sports-follow-check.timer` | Start the timer (**Bharath**) |
| A disk is filling up | `sudo du -xh --max-depth=1 <mountpoint> \| sort -h`: old Docker images, `/var/backups/sports-follow` (14 days), logs | Prune old images or move backups (**Bharath**) |
| Memory is short; free memory is almost gone; the out-of-memory killer stopped a process | Performance's memory chart; `free -m`, `docker stats --no-stream`; for the killer, `sudo journalctl -k \| grep -i oom` | Restart the container holding it, or a bigger plan (**Bharath**) |
| AWS is holding back the server's CPU | Performance's Server CPU, "held back": Lightsail's burst allowance is spent | A bigger plan if it lasts (**Bharath**, it costs) |
| The database backup is late; off-site backups aren't working | `c logs backup`; `sudo journalctl -u sports-follow-offsite` | Once the cause is fixed, off-site again now: `sudo systemctl start sports-follow-offsite` (**Bharath**; deploy/README.md, "Off-site backups") |
| Docker isn't answering; an app container is down; a container keeps restarting | `c ps`; `c logs <service>` | Restart it (**Bharath**) once its logs say why |
| No trusted HTTPS certificate; the certificate hasn't renewed | `c logs caddy` | DNS must point at the server for Caddy to renew |

### The app (`deploy/grafana/app-rules.json`)

| Alert | Check | Do |
|---|---|---|
| The worker stopped reporting | `c ps worker`, `c logs --since 30m worker`. Until it's back the other app alerts see nothing | Restart the worker (**Bharath**); if it runs, its metrics can't reach Grafana (`otlp.env`) |
| The API is failing requests | Health: which journey. API dashboard: Failed requests, each with its trace. Errors: the group (`error`, `place`) | Fix the cause; if a deploy started it, roll back (**Bharath**) |
| The API is slow | Performance: which journey and route. Its traces: `{span.http.route="<route>" && duration>1s}` in Explore (Tempo): a slow query, or a source called while the fan waits. Server CPU and memory | Fix the slow part |
| A source keeps failing | Sources: errors by status. The provider's status page. It counts 5xx and no answer only: a refused key (401, 403) never fires it, so the weekly look checks those | Wait for it: live scores from it are late |
| A source keeps telling us to slow down | Sources: requests by lane; `c exec worker python -m sports_follow.usage` | Lower its budget in `/admin` or raise the plan (**Bharath**) |
| A source's allowance is nearly used; a source says its allowance is nearly gone | Sources: used and left by bucket. "Nearly gone" while we've used little: another key or app shares it | Background refreshes stop by themselves; live games and fans keep the rest. If it's often: the plan (**Bharath**) |
| A live game isn't being polled | Jobs and live polls: the queue and the worker's warnings; the `debug` skill's `live.md` | A game its source stopped returning (a fixture outside the plan) stays behind until it's closed 6 days after its start: known, nothing to do |
| Jobs are waiting for the worker | Jobs and live polls: what's running; Performance: queue wait by job | A job that runs long holds a slot: find out why |
| Pushes aren't going out | `c logs worker`; the `debug` skill's `push.md` | Fans aren't getting alerts: fix first |
| Pushes are failing | Notifications: by push service and status. A 401 or 403 from every device of one service is usually our credentials (FCM's service account, Web Push's VAPID keys) | Credentials are **Bharath**'s |
| Player pages keep failing to build | The worker's `build <id> failed:` lines; the `debug` skill's `cards.md`. OpenRouter's HTTP 402 is its credit or weekly limit | Top up or raise the limit (**Bharath**); `SPORTS_FOLLOW_AGENT=0` runs without the agent |
| Player refreshes keep failing | Sources: which source | As "A source keeps failing" |
| Someone signed in to the admin panel | Was it you? | If not: a new password and code at once (deploy/README.md, "The admin panel"); that signs every admin out |
| Someone is guessing at the admin sign-in | The same | At 30 wrong tries in an hour it stops taking tries; the same section says how to clear that |
| The feed has kept nothing for a day | Feed: where the funnel stops. No uploads read: the YouTube key or its quota (Feed: YouTube quota used today); nothing matched; everything refused (by reason). `c logs curator` | Depends on the stop |
| The feed's judge is failing | Feed: judge errors by kind | `out_of_credit`: top up OpenRouter (**Bharath**); `timeout` or `unavailable`: the llm-providers gateway; `invalid_output`: the model's answers |
| A sport's followed players have no videos | The admin panel's Feed tab: each channel's yield. Feed: the funnel by sport | Add a trusted channel to the sport's `video_channels` |

### Sign-in

No alert watches sign-in yet: Health's sign-in row does. **Send failed** is email/start answering 502 (Resend
refused or didn't answer: `api.resend.com` on Sources and Performance) or 503 (email isn't set up on the
server). **Code limits hit** is the 429s: an address or network asking too often, too many wrong codes, or the
day's codes all sent (fans read "has sent all the sign-in codes it can today"); a few are guessers, many in a day
are fans shut out. Continue with Google always answers 303: its failures are `google sign-in:` warnings on the
API dashboard.

## The journeys

What Health and Performance call a journey, and the API routes in it. Other is the rest: the web app's error
reports (`/api/errors`), an `/api/` address no route has, and requests turned away before routing (a write from
another site). Health checks and live streams (`/stream`) aren't counted. A journey goes by route, not method:
deleting an account (`DELETE /api/me/account`) counts in App start.

| Journey | Routes |
|---|---|
| Sign in | `/api/auth/email/start`, `/email/verify`, `/google/start`, `/google/callback`, `/signout` |
| App start | `/api/config`, `/api/sports`, `/api/sports.css`, `/api/looks`, `/api/credits`, `/api/me/account`, `/api/me/start`, `/api/me/source` |
| Today | `/api/me/activity`, `/week`, `/following`, `/matches`, `/results`, `/research`, `/sports`; `/api/league`, `/api/starters` |
| Live and match pages | `/api/live`, `/api/match/{system}/{game}` |
| Player and team pages | `/api/players/{player_id}/card`, `/form`, `/results`, `/results/{event_id}`, `/refresh`; `/api/teams/{team_id}` |
| Feed | `/api/me/feed`, `/api/me/feed/items`, `/api/players/{player_id}/feed`, its `/feedback` and `/unplayable` |
| Search and follow | `/api/search`, `/api/athletes/…`, `/api/follows`, `/api/follows/{player_id}`, `/api/teams/{team_id}/follow`, `/api/match/{system}/{game}/follow`, `/api/me/matches/{event_id}`, `/api/campaigns/{slug}` and its `/follow` |
| Alerts settings | `/api/push…`, `/api/notifications/{notification_id}/opened`, `/api/me/mutes…`, `/api/follows/{player_id}/alerts`, `/api/teams/{team_id}/alerts`, `/api/me/matches/{event_id}/alerts` |
| Call it and leagues | `/api/predictions/{question_id}/pick`, `/api/me/predictions`, `/api/me/leagues`, `/api/leagues…` |
| Clips | `/api/clips`, `/api/clips/{slug}`, its `/done` and `/report` |
| Pictures | `/api/photos/{name}`, `/api/logos/{name}`, every `share.png` (link previews) |
| Admin panel | `/api/admin/…` |

## Keeping it up to date

- **A new API route:** add it to its journey in `JOURNEYS`, `deploy/grafana/ops_dashboards.py`; run
  `.venv/bin/python deploy/grafana/ops_dashboards.py` and `deploy/grafana/apply-dashboards.sh`.
  `tests/test_ops_dashboards.py` fails until the route is in a journey and the JSON matches.
- **A new alert rule:** a row in [When an alert emails](#when-an-alert-emails).
- **A new dashboard:** a row in [The dashboards](#the-dashboards) and in deploy/README.md, "Dashboards".
- **[What's normal](#whats-normal):** read again after launch, and whenever traffic has grown a lot.
