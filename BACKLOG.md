# Backlog: direction

Where Sports Follow is going and why. The work itself lives in Linear (team BHA, project Sports Follow):
file new work there, with the `linear` skill. Everything waiting on Bharath carries the `Needs you` label,
and a scheduled brief lists it each morning. The full backlog as it stood on 15 Oct 2026, before the work
moved to Linear, is `git show 433ea3a:BACKLOG.md`.

## The goal

Follow any athlete in any sport: one shared page per player, with their live score and their own numbers
while they play, alerts for the moments that matter, and their whole history. India first; global as well
for esports (decided 13 Oct 2026).

## The launch plan

A lean India-first launch, chosen 30 Sep 2026
([research](https://claude.ai/artifact/AKZ9gdcQ1xCGnVr7b16z8G)): licensed live data for cricket, football
and chess first, about $150–350 a month. The public launch is epic
[BHA-6](https://linear.app/bharathm/issue/BHA-6) and the Linear milestone "Public launch (India)", aimed at
23 Oct 2026. Production has run on a server in Sydney since 14 Oct 2026 (epic
[BHA-169](https://linear.app/bharathm/issue/BHA-169)): nothing requires India, so the region was open.

## Sources and licences

`SPORTS_FOLLOW_SOURCES` in `deploy/settings.env` switches the sources on for the public build, and
`SPORTS_FOLLOW_OPENDOTA` OpenDota.

| Source | For | Public build | Terms |
|---|---|---|---|
| Sportmonks Football | football | on: Starter, 5 leagues (Premier League, La Liga, Serie A, Bundesliga, Ligue 1); more from €4 a league a month | stored for this app, never passed on |
| Sportmonks Cricket | cricket | on: Major (27 competitions; no men's ODIs or Tests, which need Cricket World at €75 a month: BHA-17) | as above |
| Lichess | chess broadcasts | on | CC BY-SA, credited |
| Wikidata | the player registry | on | CC0 |
| FIDE rating list | chess ratings in search | not built: waits for FIDE's written permission (BHA-22, then BHA-60) | |
| ESPN | basketball, tennis; football and cricket in development | development only | unofficial endpoints, no licence |
| PandaScore | esports team titles | off until the free plan is confirmed for a commercial app (BHA-30) | non-betting use |
| Liquipedia | battle royale (BGMI, Free Fire, PUBG, Apex) | off until a commercial plan (BHA-35) | CC BY-SA 3.0, credited; free key non-commercial |
| PUBG API | PUBG PC | off until decided (BHA-207) | commercial apps allowed, with the trademark notice |
| OpenDota | Dota 2 inside each map | off until its terms are checked (BHA-208) | community-run, no data licence |

The esports research behind these rows is in [docs/research/esports.md](docs/research/esports.md).

## Constraints

- A public build runs licensed sources only. A sport without one gets AI-built pages with no live scores
  or alerts (`SPORTS_FOLLOW_AGENT_LIVE` stays off).
- Adding or removing a sport, a player or a source is a change in one place (CLAUDE.md, "Code
  organization").
- Sportmonks' data may be stored for this app but not passed on, so anything published (a registry
  snapshot, BHA-55) leaves its ids out.
- Commons licenses the photograph, not the person: no implied endorsement, no ads or merchandise.
- Hosting may move, so operations stay portable: settings from the environment, logs as JSON on stdout.
- The AI gateway allows Sports Follow $5 a day (about 40 new players' first pages) until BHA-23 decides
  the budget for real traffic.

## Decisions, and why

- **ESPN is for development only.** Its endpoints are unofficial and unlicensed, and it could block us.
- **No AI-generated headshots.** A realistic AI image of a real athlete imitates their likeness without
  consent: Indian courts have granted personality-rights injunctions against AI imitations (Anil Kapoor,
  Delhi HC 2023; Arijit Singh, Bombay HC 2024), and app stores restrict deceptive depictions of real
  people. Licensed Commons photos with credits, or a styled avatar that doesn't depict the person
  (epic [BHA-13](https://linear.app/bharathm/issue/BHA-13)).
- **The Android app is the web app**, wrapped as a Trusted Web Activity. A native app only for what the
  web can't do: lock-screen live scores, widgets, iPhone's limits (BHA-28).
- **Recorded data only as a last resort** (BHA-58): stale scores that look live are worse than an empty
  page.
- **Esports: team titles from PandaScore and battle royale from Liquipedia** (13 Oct 2026): Valorant and
  CS2 have the cleanest data, BGMI the audience in India.

## Before spending more on data

Measure 30-day retention, push opt-in, follows per user and the share of fans outside India first
(BHA-24). Then, as the numbers justify it:

- licensed basketball and tennis feeds (BALLDONTLIE, API-Tennis: BHA-73, BHA-74);
- PandaScore player stats (€400 a game a month) or in-game live (€1,000 a game a month, LoL and CS only:
  BHA-38);
- more Sportmonks football leagues (€4 each a month; Cricket World, €75 a month, is a launch decision:
  BHA-17);
- Liquipedia's paid plans (reported $49 or $199 a data type a game a month, unconfirmed: BHA-35).

## Epics

| Epic | |
|---|---|
| [BHA-6](https://linear.app/bharathm/issue/BHA-6) | India-first public launch |
| [BHA-169](https://linear.app/bharathm/issue/BHA-169) | Production server, off the Mac |
| [BHA-162](https://linear.app/bharathm/issue/BHA-162) | Accounts: sign up, sign in, delete |
| [BHA-8](https://linear.app/bharathm/issue/BHA-8) | Play Store app (Android) |
| [BHA-190](https://linear.app/bharathm/issue/BHA-190) | Observability |
| [BHA-7](https://linear.app/bharathm/issue/BHA-7) | Esports: team titles, battle royale, global coverage |
| [BHA-9](https://linear.app/bharathm/issue/BHA-9) | Alerts and notifications: next |
| [BHA-10](https://linear.app/bharathm/issue/BHA-10) | Live scores at scale |
| [BHA-11](https://linear.app/bharathm/issue/BHA-11) | Data in a fresh environment |
| [BHA-12](https://linear.app/bharathm/issue/BHA-12) | Search and the player registry: next |
| [BHA-13](https://linear.app/bharathm/issue/BHA-13) | Player headshots |
| [BHA-14](https://linear.app/bharathm/issue/BHA-14) | Results and history: next |
| [BHA-15](https://linear.app/bharathm/issue/BHA-15) | More sports and features: team following, compare players |

## Research and designs

- The launch plan: [research](https://claude.ai/artifact/AKZ9gdcQ1xCGnVr7b16z8G) (30 Sep 2026).
- Esports: [docs/research/esports.md](docs/research/esports.md) (12–13 Oct 2026).
- The backend: [docs/backend-design.html](docs/backend-design.html).
- How what's built works: the README's "How it's built".
