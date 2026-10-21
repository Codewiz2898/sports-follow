"""Campaign pages: /<slug> (stann.in/gukesh), the page a launch post links to. A fan lands on it, follows the
event's players, or a whole team, in one tap with key alerts, turns notifications on, and is set for the first
game (web/src/pages/CampaignPage.tsx).

A campaign is a row (models.Campaign), never code: its address, sport, title, the event's name, a few lines
of copy, its players (registry ids), whole teams (as their source lists them) and its games' start times. A
new event is a new row, set with this module's command:

    .venv/bin/python -m sports_follow.campaigns set gukesh --sport chess --title "Gukesh v Sindarov" --event "World Chess Championship" --line "…"
    .venv/bin/python -m sports_follow.campaigns player add gukesh Q1234       # a registry id, player id or system:id
    .venv/bin/python -m sports_follow.campaigns player remove gukesh Q1234
    .venv/bin/python -m sports_follow.campaigns team find T1 --sport lol      # teams by name, with their ids (esports: any game's)
    .venv/bin/python -m sports_follow.campaigns team add worlds pandascore_esports:126061
    .venv/bin/python -m sports_follow.campaigns team remove worlds 126061
    .venv/bin/python -m sports_follow.campaigns game add gukesh "2026-11-24 15:00" "Game 1"   # India time (ZONE), or --utc
    .venv/bin/python -m sports_follow.campaigns game remove gukesh 1          # by its number in `show`
    .venv/bin/python -m sports_follow.campaigns list | show gukesh | remove gukesh | refresh

Its players are registry athletes of its sport whom a running source has, checked when added as a starter is
(players.check_source), so following one never needs AI research. Its teams are a source's (an adapter's
team(), find_teams()), and following one follows every player on its roster: one action over the roster's
players with the ordinary follow, not a new kind of follow. A roster is read from the source when its team is
added and again each day by the worker (refresh), so a change of line-up reaches the page within a day; a
source that doesn't answer leaves the roster last read. A player an operator removes (players remove) leaves
every campaign, and a source that isn't running shows its teams without players.

What a campaign says about its sport (what key alerts tell, its colour) comes from the sport, on the page; its
words for its games ("Game 1", "Final") are the operator's. Times are shown in ZONE, the home audience's.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from . import logs, players, registry, search, sports
from .adapters import ADAPTERS, KNOWN, AdapterError, PlayerRef, SourceTeam
from .db import session
from .models import Athlete, Campaign, Player, PlayerIdentity

log = logging.getLogger(__name__)

SLUG = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?")
# Addresses the app and the server already have: a campaign can't take one. No slug has a dot, so the
# build's files at the root (sw.js, manifest.webmanifest) never clash. tests/test_campaigns.py holds the
# web app's routes (web/src/main.tsx) to this list.
TAKEN = frozenset({
    "api", "admin", "assets", "icons", "welcome", "following", "live", "match", "search", "player", "team", "athlete", "notifications", "credits", "account",
    "privacy", "go",  # BHA-239's short links
    "watch",  # a stream's player alone, for the Android app (watch.page)
    "me", "leagues", "league", "j",  # predictions with friends: a fan's leagues (/me/leagues), one league's page, an invite (/j/<code>)
    "feed",  # the Android app's Feed tab (the web's is /me/feed)
    "clip", "competition",  # clips (BHA-367): one clip (/clip/<id>), a competition's clips (/competition/<name>/clips)
})
NEAR = timedelta(hours=3)  # a player's game this close to one on their campaign's schedule is that game (player_ids)
ZONE = ZoneInfo("Asia/Kolkata")  # the home audience's time, which the page shows each game in
ZONE_LABEL = "IST"
TITLE_MAX, EVENT_MAX, LINE_MAX, LABEL_MAX = 80, 120, 240, 40
MOST_LINES = 4  # a few lines of copy: a phone shows them above the fold
MOST_PLAYERS = 8
MOST_TEAMS = 4
MOST_GAMES = 30  # a world championship's 14 games and tie-breaks
ROSTER_MOST = 12  # a team's players the page offers: a roster with its substitutes


class Refused(ValueError):
    """What an operator asked for can't be done as asked; the message says why."""


def get(db: Session, slug: str) -> Campaign | None:
    return db.scalar(select(Campaign).where(Campaign.slug == slug.strip().lower()))


def player_ids(db: Session, at: datetime) -> set[int]:
    """The players here, by their player ids, of the campaigns (of a sport that's on) with a game on their schedule
    within NEAR of `at`: their own (by their registry ids) and their whole teams' (by their ids in the team's source).
    A game of theirs starting then is a campaign's game, which fans are reminded to call (predictions_push.py); their
    other games, before the event or after it, aren't."""
    qids: set[str] = set()
    refs: set[tuple[str, str]] = set()
    for c in db.scalars(select(Campaign)):
        if not sports.on(c.sport) or not any(abs(datetime.fromisoformat(g["start_utc"]) - at) <= NEAR for g in c.games or [] if g.get("start_utc")):
            continue
        qids.update(c.players or [])
        refs.update((t["system"], p["athlete_id"]) for t in c.teams or [] for p in t.get("players") or [] if t.get("system") and p.get("athlete_id"))
    found = set(db.scalars(select(Player.id).where(Player.wikidata_qid.in_(qids)))) if qids else set()
    for system, athlete_id in refs:
        player_id = db.scalar(select(PlayerIdentity.player_id).where(PlayerIdentity.system == system, PlayerIdentity.external_id == athlete_id))
        if player_id is not None:
            found.add(player_id)
    return found


def _sport(key: str) -> sports.Sport:
    """A campaign's sport: one the app lists, or a group of them ("esports", for an event of several games)."""
    sport = sports.BY_KEY.get(key)
    if sport is None:
        raise Refused(f"unknown sport {key!r}; choose from {', '.join(s.key for s in (*sports.ALL, *sports.GROUPS))}")
    return sport


def _within(key: str) -> list[str]:
    """The sports a campaign's players and teams may be of: its sport, or for a group, any of the group's."""
    return sports.within(key)


def _text(value: str, longest: int, what: str) -> str:
    text = " ".join(value.split())
    if not text or not text.isprintable() or "<" in text or ">" in text:
        raise Refused(f"{what} needs words, with no < or >")
    if len(text) > longest:
        raise Refused(f"{what} is {len(text)} characters; at most {longest}")
    return text


def setup(db: Session, slug: str, *, sport: str | None = None, title: str | None = None, event: str | None = None,
          lines: list[str] | None = None) -> tuple[Campaign, bool]:
    """Make a campaign, or change what's given of one. Returns it and whether it's new."""
    slug = slug.strip().lower()
    if not SLUG.fullmatch(slug):
        raise Refused(f"{slug!r} can't be an address: lower-case letters, digits and hyphens, at most 40")
    if slug in TAKEN:
        raise Refused(f"/{slug} is already a page of the app")
    if sport is not None:
        _sport(sport)
    clean = [_text(line, LINE_MAX, "a line") for line in lines] if lines is not None else None
    if clean is not None and len(clean) > MOST_LINES:
        raise Refused(f"{len(clean)} lines; a campaign has at most {MOST_LINES}")
    c = get(db, slug)
    new = c is None
    if c is None:
        if sport is None or title is None:
            raise Refused("a new campaign needs --sport and --title")
        c = Campaign(slug=slug, sport=sport, title="", event=None, lines=[], players=[], teams=[], games=[])
        db.add(c)
    elif sport is not None and sport != c.sport and (c.players or c.teams):
        raise Refused(f"/{slug} has {c.sport} players or teams; remove them before changing its sport")
    if sport is not None:
        c.sport = sport
    if title is not None:
        c.title = _text(title, TITLE_MAX, "the title")
    if event is not None:
        c.event = _text(event, EVENT_MAX, "the event") if event.strip() else None
    if clean is not None:
        c.lines = clean
    db.flush()
    return c, new


def _need(db: Session, slug: str) -> Campaign:
    c = get(db, slug)
    if c is None:
        raise Refused(f"no campaign at /{slug.strip().lower()}; make it with `set`")
    return c


def _changed(c: Campaign, *fields: str) -> None:
    for f in fields:
        flag_modified(c, f)


# ---------------------------------------------------------------- its players


def player_athletes(db: Session, c: Campaign, ref: str) -> list[Athlete]:
    """The registry athlete of the campaign's sport a reference means (players.find)."""
    found = players.find(db, ref, c.sport).athletes
    if not found:
        raise Refused(f"no {c.sport} athlete in the registry has the id {ref}")
    return found[:1]


def add_player(db: Session, c: Campaign, athlete: Athlete) -> bool:
    """Put a registry athlete on the campaign, after its others. False if they were on it."""
    if athlete.sport not in _within(c.sport):
        raise Refused(f"{athlete.name} is a {athlete.sport} athlete; /{c.slug} is {c.sport}")
    if athlete.qid in c.players:
        return False
    if len(c.players) >= MOST_PLAYERS:
        raise Refused(f"/{c.slug} has {len(c.players)} players already; at most {MOST_PLAYERS}")
    c.players = [*c.players, athlete.qid]
    _changed(c, "players")
    return True


def remove_player(db: Session, c: Campaign, ref: str) -> list[str]:
    """Take players off the campaign, by registry id or any reference to them. Returns the ids taken off."""
    qids = {ref.strip()} | {a.qid for a in players.find(db, ref, c.sport).athletes}
    gone = [q for q in c.players if q in qids]
    if not gone:
        raise Refused(f"{ref} isn't on /{c.slug}")
    c.players = [q for q in c.players if q not in qids]
    _changed(c, "players")
    return gone


def forget(db: Session, athletes: list[Athlete]) -> int:
    """Take athletes an operator removed (players.remove) off every campaign of their sport, as their
    starter pins go. Returns how many campaigns changed."""
    gone: dict[str, set[str]] = {}
    for a in athletes:
        gone.setdefault(a.sport, set()).add(a.qid)
    changed = 0
    groups = {g for k in gone if (g := sports.of(k).group)}  # an event of several games is the group's
    for c in db.scalars(select(Campaign).where(Campaign.sport.in_([*gone, *groups]))) if gone else []:
        out = set().union(*(gone.get(k, set()) for k in _within(c.sport)))
        kept = [q for q in c.players if q not in out]
        if kept != c.players:
            c.players = kept
            _changed(c, "players")
            changed += 1
    return changed


# ---------------------------------------------------------------- its teams


def team_source(system: str, sport: str) -> Any:
    """The running source a team of this sport is read from, which can list a team's roster."""
    adapter = ADAPTERS.get(system)
    if adapter is None:
        known = KNOWN.get(system)
        raise Refused(f"{system} isn't running in this build" if known else f"no source is called {system!r}")
    if not set(_within(sport)) & set(adapter.sports):
        raise Refused(f"{system} isn't a {sport} source")
    if not callable(getattr(adapter, "team", None)):
        raise Refused(f"{system} doesn't list teams' rosters")
    return adapter


def sources_with_teams(sport: str) -> list[Any]:
    return [a for a in ADAPTERS.values() if set(_within(sport)) & set(a.sports) and callable(getattr(a, "find_teams", None))]


def of_sport(team: SourceTeam, sport: str) -> bool:
    """Whether a team plays the campaign's sport: a source of several games names each team's (T1 plays League of
    Legends and Valorant, a team each)."""
    return not team.game or sports.family(team.game) in _within(sport)


def read_team(system: str, team_id: str, sport: str) -> SourceTeam:
    """A team and its roster now, from its source (outside a transaction: it asks the source)."""
    adapter = team_source(system, sport)
    if not getattr(adapter, "team_id_pattern", re.compile(r"[0-9]{1,12}")).fullmatch(team_id):
        raise Refused(f"{team_id!r} isn't one of {system}'s team ids")
    try:
        team = adapter.team(team_id)
    except AdapterError as exc:
        raise Refused(f"couldn't reach {system} to read team {team_id}: {exc}") from exc
    if team is None:
        raise Refused(f"{system} has no team {team_id}")
    if not of_sport(team, sport):
        raise Refused(f"{team.name} ({team_id}) is a {team.game} team, not {sport}")
    return team


def _stored(team: SourceTeam, now: datetime) -> dict[str, Any]:
    return {
        "system": team.system, "team_id": team.team_id, "name": team.name, "game": team.game,
        "players": [{"athlete_id": p.athlete_id, "name": p.name, "league": p.league} for p in team.players[:ROSTER_MOST]],
        "read_at": now.isoformat(),
    }


def add_team(db: Session, c: Campaign, team: SourceTeam, now: datetime | None = None) -> bool:
    """Put a team on the campaign with its roster as just read, or bring its roster up to date. True if new."""
    now = now or datetime.now(timezone.utc)
    stored = _stored(team, now)
    same = [i for i, t in enumerate(c.teams) if (t["system"], t["team_id"]) == (team.system, team.team_id)]
    if same:
        c.teams = [stored if i == same[0] else t for i, t in enumerate(c.teams)]
    elif len(c.teams) >= MOST_TEAMS:
        raise Refused(f"/{c.slug} has {len(c.teams)} teams already; at most {MOST_TEAMS}")
    else:
        c.teams = [*c.teams, stored]
    _changed(c, "teams")
    return not same


def remove_team(c: Campaign, team_id: str) -> list[str]:
    """Take a team off the campaign, by its id in its source. Returns the names taken off."""
    gone = [t["name"] for t in c.teams if t["team_id"] == team_id.strip()]
    if not gone:
        raise Refused(f"no team {team_id} is on /{c.slug}")
    c.teams = [t for t in c.teams if t["team_id"] != team_id.strip()]
    _changed(c, "teams")
    return gone


def refresh(now: datetime | None = None) -> dict[str, int]:
    """Read every campaign's teams' rosters again from their sources (worker.refresh_campaigns, daily; the
    command's `refresh`). A team whose source doesn't answer, or isn't running, keeps the roster last read.
    Returns the rosters read, by campaign."""
    now = now or datetime.now(timezone.utc)
    with session() as db:
        wanted = [(c.slug, c.sport, [(t["system"], t["team_id"]) for t in c.teams]) for c in db.scalars(select(Campaign)) if c.teams]
    read: dict[str, int] = {}
    for slug, sport, teams in wanted:
        fresh = []
        for system, team_id in teams:
            try:
                fresh.append(read_team(system, team_id, sport))
            except Refused as exc:
                log.warning("campaigns: /%s keeps %s:%s's roster: %s", slug, system, team_id, exc)
        with session() as db:
            c = get(db, slug)
            if c is None:
                continue  # taken down meanwhile
            for team in fresh:
                if any((t["system"], t["team_id"]) == (team.system, team.team_id) for t in c.teams):  # not taken off meanwhile
                    add_team(db, c, team, now)
        read[slug] = len(fresh)
    return read


# ---------------------------------------------------------------- its games


def when(text: str, utc: bool = False) -> datetime:
    """A game's start as typed ("2026-11-24 15:00", in ZONE unless utc) as a time in UTC."""
    try:
        start = datetime.fromisoformat(text.strip())
    except ValueError as exc:
        raise Refused(f"{text!r} isn't a date and time, like 2026-11-24 15:00") from exc
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc if utc else ZONE)
    return start.astimezone(timezone.utc)


def add_game(c: Campaign, start: datetime, label: str) -> int:
    """Put a game on the schedule, in time order. Returns its number in the schedule."""
    label = _text(label, LABEL_MAX, "a game's label")
    if len(c.games) >= MOST_GAMES:
        raise Refused(f"/{c.slug} has {len(c.games)} games already; at most {MOST_GAMES}")
    games = sorted([*c.games, {"start_utc": start.isoformat(), "label": label}], key=lambda g: g["start_utc"])
    c.games = games
    _changed(c, "games")
    return next(i for i, g in enumerate(games, 1) if g["start_utc"] == start.isoformat() and g["label"] == label)


def remove_game(c: Campaign, number: int) -> dict[str, Any]:
    if not 1 <= number <= len(c.games):
        raise Refused(f"/{c.slug} has {len(c.games)} games; say which by its number in `show`")
    gone = c.games[number - 1]
    c.games = [g for i, g in enumerate(c.games, 1) if i != number]
    _changed(c, "games")
    return gone


def said(start_utc: str) -> str:
    """A game's start as the page says it: "Tue 24 Nov, 3:00 pm IST"."""
    at = datetime.fromisoformat(start_utc).astimezone(ZONE)
    return f"{at:%a} {at.day} {at:%b}, {at.hour % 12 or 12}:{at:%M} {'am' if at.hour < 12 else 'pm'} {ZONE_LABEL}"


def next_game(c: Campaign | dict[str, Any], now: datetime | None = None) -> dict[str, Any] | None:
    """The first game not yet started, or None."""
    now = now or datetime.now(timezone.utc)
    games = c.games if isinstance(c, Campaign) else c["games"]
    return next((g for g in games if datetime.fromisoformat(g["start_utc"]) > now), None)


# ---------------------------------------------------------------- the page


def _team_rows(db: Session, sport: sports.Sport, team: dict[str, Any], blocked: set[tuple[str, str]], fan: str | None) -> list[dict[str, Any]]:
    """A team's players as the app's result rows, followed by the source's id; none while its source, or its game, isn't running."""
    system = team["system"]
    if system not in ADAPTERS or not sports.on(team.get("game")):
        return []  # a game turned off (a group campaign's team of it) offers no one, as its source's off
    refs = [PlayerRef(system=system, athlete_id=p["athlete_id"], name=p["name"], team_ids=[team["team_id"]], team_names=[team["name"]], league=p.get("league"))
            for p in team["players"] if (system, p["athlete_id"]) not in blocked]
    return [search.public(r) for r in search.offered_refs(db, team.get("game") or sport.label, refs, fan)]


def page(db: Session, slug: str, fan: str | None) -> dict[str, Any] | None:
    """A campaign as its page shows it (GET /api/campaigns/{slug}): its words, its players and teams' players as
    the app's result rows (filled in for those on Sports Follow and whether this fan follows them, so following
    one is the same call as from search), and its games. None for no such campaign, or one of a sport turned off."""
    c = get(db, slug)
    if c is None or not SLUG.fullmatch(c.slug) or not sports.on(c.sport):
        return None
    sport = _sport(c.sport)
    athletes = {a.qid: a for a in db.scalars(select(Athlete).where(Athlete.sport.in_(_within(c.sport)), Athlete.qid.in_(c.players)))} if c.players else {}
    rows = search.offered(db, [athletes[q] for q in c.players if q in athletes], fan)
    blocked = registry.blocked(db) if c.teams else set()
    return {
        "slug": c.slug,
        "sport": sport.key,
        "title": c.title,
        "event": c.event,
        "lines": list(c.lines),
        "players": [search.public(r) for r in rows],
        "teams": [{"team_id": t["team_id"], "name": t["name"], "game": t.get("game"), "players": _team_rows(db, sport, t, blocked, fan)} for t in c.teams],
        "games": [{"start_utc": g["start_utc"], "label": g["label"]} for g in c.games],
        "zone": {"name": ZONE.key, "label": ZONE_LABEL},
    }


def to_follow(row: dict[str, Any]) -> dict[str, Any] | None:
    """What following a page's row sends (web/src/follow.ts's followTarget), never a name: a campaign's rows
    are all followable without AI research. None for a row followed some other way."""
    if row.get("player_id"):
        return {"player_id": row["player_id"]}
    if row.get("system") and row.get("athlete_id"):
        return {"system": row["system"], "athlete_id": row["athlete_id"], "league": row.get("league"), "qid": row.get("qid")}
    return None


# ---------------------------------------------------------------- the command


def describe(c: Campaign, db: Session) -> list[str]:
    athletes = {a.qid: a for a in db.scalars(select(Athlete).where(Athlete.sport.in_(_within(c.sport)), Athlete.qid.in_(c.players)))} if c.players else {}
    out = [f"/{c.slug}  {c.title}  ({c.sport}{f', {c.event}' if c.event else ''})"]
    out += [f"  line   {line}" for line in c.lines]
    out += [f"  player {q}  {athletes[q].name if q in athletes else '? (no longer in the registry)'}" for q in c.players]
    for t in c.teams:
        out.append(f"  team   {t['system']}:{t['team_id']}  {t['name']}  ({len(t['players'])} players, read {t['read_at'][:16]}): {', '.join(p['name'] for p in t['players'])}")
    out += [f"  game {i}  {said(g['start_utc'])}  {g['label']}" for i, g in enumerate(c.games, 1)]
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m sports_follow.campaigns", description="Campaign pages: /<slug>, a big event's players and teams to follow.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="every campaign")
    sh = sub.add_parser("show", help="one campaign, in full")
    sh.add_argument("slug")
    st = sub.add_parser("set", help="make a campaign, or change its sport, title, event or lines")
    st.add_argument("slug", help="its address: gukesh is /gukesh")
    st.add_argument("--sport")
    st.add_argument("--title", help=f"in big type, at most {TITLE_MAX} characters")
    st.add_argument("--event", help="the event's name, above it ('' takes it off)")
    st.add_argument("--line", action="append", help=f"a line of copy (repeat for each, at most {MOST_LINES}); replaces the lines")
    st.add_argument("--no-lines", action="store_true", help="take the lines off")
    rm = sub.add_parser("remove", help="take a campaign's page down")
    rm.add_argument("slug")
    sub.add_parser("refresh", help="read every campaign's teams' rosters now (the worker does it daily)")
    p = sub.add_parser("player", help="its players").add_subparsers(dest="action", required=True)
    for action in ("add", "remove"):
        x = p.add_parser(action)
        x.add_argument("slug")
        x.add_argument("ref", help="a registry id (Q1234), a player id or system:id")
    t = sub.add_parser("team", help="whole teams, from their source").add_subparsers(dest="action", required=True)
    f = t.add_parser("find", help="teams by name, with the ids to add them by")
    f.add_argument("name")
    f.add_argument("--sport", required=True)
    ta = t.add_parser("add", help="put a team on, with its roster as its source lists it now")
    ta.add_argument("slug")
    ta.add_argument("team", help="system:team id, as `team find` shows it")
    tr = t.add_parser("remove")
    tr.add_argument("slug")
    tr.add_argument("team_id")
    g = sub.add_parser("game", help="its schedule").add_subparsers(dest="action", required=True)
    ga = g.add_parser("add", help=f"a game: its start ({ZONE_LABEL} unless --utc) and its label")
    ga.add_argument("slug")
    ga.add_argument("start", help="2026-11-24 15:00")
    ga.add_argument("label", help='"Game 1", "Final"')
    ga.add_argument("--utc", action="store_true", help="the start is in UTC")
    gr = g.add_parser("remove", help="a game, by its number in `show`")
    gr.add_argument("slug")
    gr.add_argument("number", type=int)
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            with session() as db:
                rows = list(db.scalars(select(Campaign).order_by(Campaign.created_at)))
                for c in rows:
                    nxt = next_game(c)
                    print(f"/{c.slug:16}  {c.sport:10}  {c.title}  · {len(c.players)} players, {len(c.teams)} teams, {len(c.games)} games"
                          f"{f'; next: {nxt['label']}, {said(nxt['start_utc'])}' if nxt else ''}")
                if not rows:
                    print("No campaigns yet: make one with `set`.")
        elif args.command == "show":
            with session() as db:
                print("\n".join(describe(_need(db, args.slug), db)))
        elif args.command == "set":
            lines = [] if args.no_lines else args.line
            with session() as db:
                c, new = setup(db, args.slug, sport=args.sport, title=args.title, event=args.event, lines=lines)
                print(f"{'Made' if new else 'Changed'} /{c.slug}: {c.title} ({c.sport}).")
        elif args.command == "remove":
            with session() as db:
                db.delete(_need(db, args.slug))
            print(f"Took /{args.slug} down.")
        elif args.command == "refresh":
            for slug, n in refresh().items():
                print(f"/{slug}: {n} roster(s) read")
        elif args.command == "player" and args.action == "add":
            with session() as db:
                c = _need(db, args.slug)
                person = registry.Person.of(player_athletes(db, c, args.ref)[0])
            through = players.check_source(person)  # outside a transaction: it asks the source
            with session() as db:
                c = _need(db, args.slug)
                athlete = player_athletes(db, c, args.ref)[0]
                added = add_player(db, c, athlete)
                players.pin(db, athlete.qid, athlete.sport, note=f"on /{c.slug}")
                print(f"{'Added' if added else 'Already on it'}: {athlete.name} ({athlete.qid}) on /{c.slug}, followed live through {through}.")
        elif args.command == "player":
            with session() as db:
                c = _need(db, args.slug)
                print(f"Took {', '.join(remove_player(db, c, args.ref))} off /{c.slug}.")
        elif args.command == "team" and args.action == "find":
            _sport(args.sport)
            found = [team for a in sources_with_teams(args.sport) for team in a.find_teams(args.name) if of_sport(team, args.sport)]
            for team in found:
                print(f"{team.system}:{team.team_id:10}  {team.name}  ({team.game or args.sport}): {', '.join(p.name for p in team.players) or 'no players listed'}")
            if not found:
                print(f"No {args.sport} team called {args.name!r} in a running source that lists rosters.")
        elif args.command == "team" and args.action == "add":
            system, _, team_id = args.team.partition(":")
            with session() as db:
                sport = _need(db, args.slug).sport
            team = read_team(system, team_id.strip(), sport)  # outside a transaction: it asks the source
            with session() as db:
                c = _need(db, args.slug)
                new = add_team(db, c, team)
                print(f"{'Added' if new else 'Read again'}: {team.name} on /{c.slug}, {len(team.players)} players: {', '.join(p.name for p in team.players)}.")
        elif args.command == "team":
            with session() as db:
                c = _need(db, args.slug)
                print(f"Took {', '.join(remove_team(c, args.team_id))} off /{c.slug}.")
        elif args.command == "game" and args.action == "add":
            with session() as db:
                c = _need(db, args.slug)
                start = when(args.start, args.utc)
                n = add_game(c, start, args.label)
                print(f"Game {n} on /{c.slug}: {args.label}, {said(start.isoformat())}.")
        elif args.command == "game":
            with session() as db:
                gone = remove_game(_need(db, args.slug), args.number)
                print(f"Took {gone['label']} ({said(gone['start_utc'])}) off /{args.slug}.")
    except (Refused, players.Refused) as exc:
        print(f"Can't: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    logs.setup()
    sys.exit(main(sys.argv[1:]))
