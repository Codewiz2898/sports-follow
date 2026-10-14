# Esports: research and source notes

What was found and decided about esports for Sports Follow, 12–13 Oct 2026: what fans watch, which sources
exist and what they cost, and how each source the app reads works. It moved here from BACKLOG.md item 8
when the work moved to Linear: epic [BHA-7](https://linear.app/bharathm/issue/BHA-7) holds what's left to do,
and the public-build decisions are BHA-30 (PandaScore), BHA-35 (Liquipedia), BHA-207 (PUBG) and BHA-208
(OpenDota).

## What fans watch, and the sources

**What fans in India watch** (researched 12 Oct 2026). BGMI by far: KRAFTON India reports 930M+ esports
views in 2025, and the BMPS 2026 final peaked at about 729,000 concurrent viewers. Free Fire is coming
back after its ban; Valorant is the biggest PC title but losing viewers. Globally: LoL, CS2, Dota 2,
Valorant.

| Source | What | Cost | Notes |
|---|---|---|---|
| PandaScore | Schedules, results, teams, players for 13+ titles (LoL, CS2, Dota 2, Valorant, Mobile Legends, R6, Rocket League, CoD…) | Free plan, 1,000 requests an hour; player stats from €400 a game a month; live from €1,000 a game a month (LoL and CS only) | Licensed; stats plans are for non-betting use. No BGMI. |
| GRID | Official CS2 and Dota 2 data from the game servers | Free Open Access is historical only, for pre-revenue projects; live is a commercial deal | Most authoritative, two games |
| Liquipedia API | 70+ titles, including PUBG Mobile (where BGMI events are kept) | Reported $49 or $199 per data type per game a month (unconfirmed) | Community-edited, results after the fact, no live; CC BY-SA credit on every page |
| Betting-odds feeds, HLTV/VLR scrapers | Live scores | Varies | Unlicensed or betting-oriented: the ESPN problem again |

Sources: [PandaScore pricing](https://pandascore.co/pricing), [GRID Open Access](https://grid.gg/open-access/),
[Liquipedia API](https://liquipedia.net/api) and [its terms](https://liquipedia.net/api-terms-of-use),
[BMPS 2026 viewership](https://www.tribuneindia.com/news/business/godlike-esports-rises-from-last-place-to-claim-bmps-2026-title-as-jaipur-bmps-finals-reach-729k-peak-viewers-and-12000-attendance/),
[BGMI 930M views](https://respawn.outlookindia.com/gaming/gaming-news/bgmi-esports-crosses-930m-views-amid-indian-esports-market-growth).

## Global as well as India

**Global as well as India** (decided 13 Oct 2026). The registry imports every PandaScore team title
(Mobile Legends, King of Glory, Rainbow Six, Overwatch, Rocket League, Call of Duty and Wild Rift beside
Valorant, CS2, LoL and Dota 2), and the Liquipedia adapter reads PUBG and Apex Legends beside PUBG Mobile
and Free Fire (their wikis store lobbies the same way). 2025 hours watched, worldwide: LoL, CS2, Mobile
Legends, Valorant, Dota 2 (Esports Charts). Activity on PandaScore's free plan, 13 Oct 2026, series in
the last 30 days: CS2 1,567, LoL 309, Dota 2 291, R6 206, Mobile Legends 179, King of Glory 141,
Valorant 76 (off-season), Overwatch 70, Rocket League 47; Call of Duty (between CDL seasons), Wild Rift,
EA FC and PUBG none.

## Where it started

**Before step 1.** No esports source. A pro gamer typed into "Research with AI" got an AI-built page
with no scores or alerts, as in any sport without a source. Search barely found them: only 2,844 people
on Wikidata have a Liquipedia id (P10918), 3 of them Indian. Every adapter assumed head-to-head events.

## Team titles from PandaScore (built)

**Built (step 1).** `adapters/pandascore_esports.py` follows a pro's
team in PandaScore's team titles (Valorant, CS2, LoL, Dota 2, Mobile Legends, Rainbow Six, Overwatch,
Rocket League, Call of Duty, Wild Rift, King of Glory): upcoming series with stage and format ("Upper
bracket final · Bo3"), results from the team's side ("Won 2–1", "Lost by forfeit"), a year of history
through the history lane, and the team's series and maps won on the Stats tab. Running series are
polled once a minute, all in one request (`poll_lane`), so map results arrive as each map ends; the
app shows a series board with one chip a map. Moments: the series starting and the result (key),
each map won or lost (everything), all told from the player's team's side. The registry imports the
active pros of `SPORTS_FOLLOW_ESPORTS_GAMES` (Valorant, CS2, LoL and Dota 2 by default) from PandaScore
weekly, by handle and full name, so search finds them under an Esports filter: 33,370 "active"
players for those four titles, 334 requests and about 50 minutes, paced. PandaScore's `active` flag
includes long-retired players (most have no team), so a pro on a team ranks first. Credits name
PandaScore. Background reads keep 300 of the 1,000 requests an hour for live series.

Checked against real responses on 13 Oct 2026 (free plan): series scores (`results`), the winner and
each map's winner come with every match, and a running series shows which map is on and who won the
earlier ones, so map moments work without a paid plan. The remaining count is `x-rate-limit-remaining`.
`/matches` rejects `filter[videogame_id]` (it's `filter[videogame]` there). Birthdays are null.
Real ESL Pro League and VCT results read correctly ("Grand Final · Bo5", "Won 3–0"). The tests use
made-up responses in PandaScore's shapes, as for Sportmonks, rather than committing theirs.
An armed series the schedule moves hours later is now disarmed (any sport), so it isn't polled all
that time or closed unplayed after six days. A pro's sport is their game ("Valorant", "CS2", "BGMI",
"Free Fire"), shown wherever other players show their sport; "esports" is the family the rules, the
search filter and the colour share (`sports_follow/sports/esports.py`), and pros followed as "Esports" move to
their game on their next refresh.

## The plan, cheapest first

**Done when** a fan can find and follow a Valorant or CS2 pro, see their team's upcoming series and
results ("Won 2–1"), and hear when a series ends, from a licensed source named on the Credits page.

**Suggested path**, cheapest first:
1. **Team esports from PandaScore's free plan: built** (above). Player numbers need the €400 stats
   plan; until then results are the team's.
2. **Live scores** once the launch metrics show demand: €1,000 a game a month for LoL or CS (Valorant
   isn't on the live tier), three to six times the launch's whole data budget.
3. **BGMI and Free Fire: built** on Liquipedia (`adapters/liquipedia.py`, `liquipedia_esports.py`),
   not yet run: it needs a LiquipediaDB key (requested at liquipedia.net/api, saved as
   `~/.config/sports-follow/liquipedia-token`). Battle royale is its own event shape: one lobby (a
   Liquipedia match2: a day of a group, 12 to 24 teams playing several games) is one event, with the
   standings (points, placement, finishes, games won) and each game's winner. Liquipedia stores each
   team's placement and finishes in every game (Lua-Modules: `MatchGroup/Input/Util.lua`), which is
   what the parsers read. The app shows the top of the standings with the player's team marked, one
   chip a game, and results like "3rd of 16 · 87 pts". Alerts, from the player's team's side: the
   lobby starting and a chicken dinner (Booyah in Free Fire) and going top (key); each game's
   placement (everything); where the team finished (key). The line-ups name the four who played, so
   a benched player isn't told. The registry imports the active players of each battle royale wiki
   (`SPORTS_FOLLOW_LIQUIPEDIA_WIKIS`), keyed "lp:<wiki>:<page id>"; PandaScore's and Liquipedia's
   players never move between the two (each covers its own games), and an esports name alone never
   binds a player: their team must match.
   - **The free key's limits:** 60 requests an hour, counted in Redis across the API and the worker,
     never spent past 56; background reads stop at 26. Lobbies are polled every 5 minutes, all of them
     in one request; fixtures every 30 minutes a team (cached). That suits a few teams; more need a
     paid plan.
   - **To check once the key works:** the opponent condition, which asks for each spelling of a team
     page as Liquipedia's MatchTicker does (`([[opponent::Team SouL]] OR [[opponent::Team_SouL]] OR …)`):
     that the API takes the brackets and which spelling the rows hold; how `dateexact` comes (a lobby
     without a time waits for its first result); the date format, whether `match2games[].opponents`
     comes parsed or as a string, and the section and bracket header used for the lobby's name
     ("Semi Finals · Day 3").
   - **For a public build:** a commercial Liquipedia plan (reported $49 or $199 a data type a month;
     their portal says sign-ups are paused), then add `liquipedia_esports` to `SPORTS_FOLLOW_SOURCES`.
     Data is CC BY-SA 3.0: credited on the Credits page and on every score as "Liquipedia".
   - **PUBG PC: built on PUBG's own API** (`adapters/pubg.py`, `pubg_esports.py`; key in
     `~/.config/sports-follow/pubg-token`). A lobby is a session of a tournament's matches played by
     the same teams (a tournament day, or a group of it); standings use the standard PUBG esports
     points (10, 6, 5, 4, 3, 2, 1, 1 and one a finish). Every player has their own line (finishes,
     damage, assists, knocks). Teams are the tags in player names ("TWIS_Lu"); tournaments have no
     names in the API, so they're named from known codes (PWS, PAS, PEC…) or the region. There's no
     schedule: a lobby appears when its first match ends, then is read every 2 minutes. The worker
     indexes recent tournaments every 10 minutes (Redis, shared; matches downloaded once; tournament
     reads at most 9 a minute) and loads their players into search every 6 hours. The tournament
     shard also holds exhibition matches (3 to 20 minutes, players on 40+ finishes): only matches of
     22+ minutes, 8+ teams and at most 25 finishes for anyone count. A tournament listed under two ids
     (`kr-26pws2f`, `kr_26pws2f`) counts once. Checked on 13 Oct 2026: 28 lobbies, 630 players.
     PUBG's terms allow commercial apps (ads, subscriptions, no paywalled data) with the trademark
     notice, on the Credits page.
   - **CODM next:** Call of Duty: Mobile is head-to-head (5v5 series), so it fits the team model on
     Liquipedia's Call of Duty wiki. PandaScore's Call of Duty is the PC and console league (CDL), not
     CODM. Neither Activision nor Garena has a public API.

## Live scores, researched 13 Oct 2026

**Live scores, researched 13 Oct 2026.** PandaScore's free plan is live only by the map (which map is on,
each map's winner), and small events can lag an hour; in-game live is €1,000 a game a month (LoL, CS).
Elsewhere:
- **Dota 2: built on OpenDota** (`adapters/opendota.py`, `dota.py`), laid over PandaScore's series. While a
  map is on: kills a side, the clock ("draft" before the horn) and the gold lead, and the heroes of both
  rosters' players; after it, each player's hero, kills, deaths, assists, GPM and last hits, with a
  minor alert ("Yatoro: 12/2/9 on Faceless Void"). Teams are joined by name without filler words
  ("Spirit Academy" is "Team Spirit Academy", "Power Rangers" is "_PowerRangers"); players by Steam
  account (OpenDota's 5,659 pros, by name and team). Each map's OpenDota match is remembered while
  it's live (Redis, per series, first sighting wins), since OpenDota's finished list keeps only the
  last 100 pro matches; that list is the fallback, and a game in it isn't shown live (OpenDota's live
  list lags a minute). OpenDota's start times run 17 minutes ahead of PandaScore's. Checked 13 Oct
  2026 on five live series (DreamLeague qualifiers and others): every series matched its game. No
  key; 60 requests a minute and 3,000 a day, counted in Redis. Community-run with no data licence:
  `SPORTS_FOLLOW_OPENDOTA=0` in the public build until its terms are checked.
- **LoL:** Riot's own site backend (esports-api.lolesports.com, feed.lolesports.com/livestats) gives full
  live stats, but it's undocumented and Riot licenses official live data only to GRID: development only.
- **Valorant:** the valorantesports.com backend has results, no live; nothing official.
- **CS2:** GRID only (commercial, priced per deal); its free Open Access programme looks discontinued.
- **PUBG PC:** PUBG's own API (a key is saved as `~/.config/sports-follow/pubg-token`): 1,200 official
  tournaments across 14 regions, every player's numbers after each match (not during), 10 requests a
  minute, matches kept 14 days. No BGMI or other mobile events.
- **Fighting games (Tekken, Street Fighter 6):** start.gg's official API, free (80 requests a minute),
  player brackets reported as they're played, commercial use allowed with attribution and no
  gambling or resale; hosts Indian events (TEKKEN Nexus 2025, New Delhi). A 1v1 shape, like tennis.
- **Dead ends:** Supercell's APIs (no pro data; no fees allowed), octane.gg (shut down), EA FC,
  Fortnite and Apex (no esports API; Liquipedia only).

## Open questions (BHA-30 and BHA-35)

- PandaScore's free plan: confirm in writing that a commercial, non-betting app may use it. (Its
  results do include series scores and map winners, for every team title: checked 13 Oct 2026.)
- Liquipedia's commercial plans: free access is only for "educational, non-commercial public-interest
  and community feature projects" (liquipedia.net/api); the paid plans' price per data type, and
  whether a battle royale app needs one data type (matches) or several (matches, players).
