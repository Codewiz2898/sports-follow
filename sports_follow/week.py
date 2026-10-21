"""Today's "This week" and the Wire, from the moments stored for a fan's players (moments.py).

This week is the big moments of the last seven days, as posters. Every moment was found by its sport, and
the sport also says which of them make a poster and words it (Sport.poster): a hundred, a hat-trick, an
upset. A player's game makes one poster, its biggest moment (a hat-trick, not three goals); a moment its
source took back (a goal VAR ruled out) makes none. Moments are found only while a game is polled live, so
a game first seen after it ended has none: its poster is read off the line it left the player instead, the
result stored for them (Sport.finished_poster: "112 (98)" is a hundred, "2 goals" a brace).

The Wire is the news about them, put plainly. First the facts a source gave about the fan's players in the
last FACT_HOURS: who's out injured or suspended, and who's back (sidelined.py), as their sport words them.
Then line-ups for games still to finish, each player's best story of the last NEWS_DAYS from the publishers'
feeds the curator read and served (news.py), and the injuries, transfers and retirements the research agent found
in the news and marked as just happened (pipeline.news_moments), each by the story's own headline with the site
that ran it, a story at one address once. Last, while there's room, the same facts about a few of the best-known players the fan
doesn't follow (the registry's Wikipedia editions), so a fan who follows nobody has a Wire too. Every row
names where it's from: those headlines are web search results, not our facts. It never says "breaking".
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Select, and_, exists, or_, select, tuple_
from sqlalchemy.orm import Session, aliased

from . import rank, registry, sports
from .adapters import ADAPTERS, NEWS, source_name
from .moments import Found
from .models import Event, EventPlayer, FeedCandidate, Follow, Moment, NewsItem, Player, PlayerIdentity, PlayerLine, Sidelined
from .naming import team_key

DAYS = 7
LIMIT = 12  # posters at most, the biggest first

# The two sides in a game's title: "India v Australia", "GenOne vs 6666", "Spurs at Jazz".
_SIDES = re.compile(r"\s+(?:v|vs|at)\.?\s+", re.I)


def versus(title: str | None, mine: list[str]) -> str:
    """The other side of a game from the player's ("v Australia"), from its title and the player's own names
    for their side (their teams, their name), or "" when the title doesn't say which side is theirs."""
    sides = _SIDES.split((title or "").split(" · ", 1)[0].strip())
    ours = {team_key(m) for m in mine if m}
    others = [s for s in sides if team_key(s) not in ours]
    return f"v {others[0]}" if len(sides) == 2 and len(others) == 1 else ""


def posters(db: Session, fan: str, now: datetime | None = None) -> list[dict[str, Any]]:
    """The fan's players' big moments since a week ago, one poster per player and game, the biggest first
    (the priority of the moment it's read from), then the newest. A game of theirs that finished in the week
    with no moment that makes a poster is read off its result instead (_finished)."""
    since = (now or datetime.now(timezone.utc)) - timedelta(days=DAYS)
    rows = db.execute(_highs(Moment.created_at >= since).join(Follow, and_(Follow.player_id == Moment.player_id, Follow.fan_id == fan))).all()
    games: dict[tuple[int, int | str], list[tuple[Moment, Player, str | None]]] = {}
    for m, player, title in rows:
        games.setdefault((player.id, m.event_id if m.event_id is not None else f"m{m.id}"), []).append((m, player, title))
    made = [made for game in games.values() if (made := _of_moments(game)) is not None]
    told = {(p["player_id"], p["event_id"]) for _, _, p in made}
    made += [f for f in _finished(db, fan, since) if (f[2]["player_id"], f[2]["event_id"]) not in told]
    made.sort(key=lambda x: x[:2], reverse=True)
    return [p for _, _, p in made[:LIMIT]]


def game(db: Session, player_id: int, event_id: int) -> dict[str, Any] | None:
    """One player's game as This week makes it a poster, however long ago it was: its biggest moment that
    makes one, or, with none, its result's once it's over. A shared link to the game previews it (share.py).
    The poster also carries the game's title and competition. None for a game without a poster, or one
    the player isn't in."""
    rows = db.execute(_highs(Moment.player_id == player_id, Moment.event_id == event_id)).all()
    made = _of_moments(rows) if rows else None
    if made is None:
        made = next((p for row in db.execute(_results(EventPlayer.player_id == player_id, EventPlayer.event_id == event_id)) if (p := _of_result(*row))), None)
    if made is None:
        return None
    event = db.get(Event, event_id)
    return made[2] | {"title": event.title if event else None, "competition": (event.competition or None) if event else None}


def _highs(*where: Any) -> Select[Moment, Player, str]:
    """Key moments with their player and their game's title, oldest first: never a correction, nor a moment
    its source took back (a goal VAR ruled out)."""
    correction = aliased(Moment)
    return (
        select(Moment, Player, Event.title)
        .join(Player, Player.id == Moment.player_id)
        .outerjoin(Event, Event.id == Moment.event_id)
        .where(Moment.level == "key", Moment.corrects.is_(None), ~exists().where(correction.corrects == Moment.id), *where)
        .order_by(Moment.id)
    )


def _of_moments(game: Sequence[Any]) -> tuple[int, datetime, dict[str, Any]] | None:
    """A player's game as a poster from its moments, oldest first (_highs), as their sport words it, with its
    priority and when; None when no moment makes one."""
    _, player, title = game[0]
    found = [(Found(m.kind, m.level, m.title, m.body, priority=m.priority), m) for m, _, _ in game]
    poster = sports.of(player.sport).poster([f for f, _ in found], versus(title, [*(player.teams or []), player.name]))
    if poster is None:
        return None
    m = next((m for f, m in found if f is poster.of), game[-1][0])
    return (m.priority, m.created_at, {
        "id": m.id, "player_id": m.player_id, "player": player.name, "sport": player.sport, "event_id": m.event_id,
        "word": poster.word, "line": poster.line, "url": m.url, "at": m.created_at.isoformat(),
    })


def _results(*where: Any) -> Select[Event, dict | None, Player, Any]:
    """Finished games' results from the player's side, each with the stats of the last line stored for them in it."""
    stored = (
        select(PlayerLine.stats)
        .where(PlayerLine.event_id == EventPlayer.event_id, PlayerLine.player_id == EventPlayer.player_id)
        .order_by(PlayerLine.as_of.desc())
        .limit(1)
        .correlate(EventPlayer)
        .scalar_subquery()
    )
    return (
        select(Event, EventPlayer.result, Player, stored)
        .join(EventPlayer, EventPlayer.event_id == Event.id)
        .join(Player, Player.id == EventPlayer.player_id)
        .where(Event.status == "final", EventPlayer.result.is_not(None), *where)
    )


def _of_result(event: Event, result: dict[str, Any] | None, player: Player, last: dict[str, Any] | None) -> tuple[int, datetime, dict[str, Any]] | None:
    """A finished game as a poster, as the player's sport words their line in it (Sport.finished_poster): the
    line from their stored result ("112 (98)"), with the stats of the last line stored for them, and the
    result from their side ("Won 2–1"). With its priority and the game's start, which dates it; it has no
    moment (id None). None for a game without a high, or without a start."""
    result, last = result or {}, last or {}
    line = {"headline": result.get("player_contribution") or last.get("headline") or "", "stats": last.get("stats") or []}
    poster = sports.of(player.sport).finished_poster(line, result.get("result") or "", versus(event.title, [*(player.teams or []), player.name]))
    if poster is None or event.start_utc is None:
        return None
    return (poster.priority, event.start_utc, {
        "id": None, "player_id": player.id, "player": player.name, "sport": player.sport, "event_id": event.id,
        "word": poster.word, "line": poster.line, "url": f"/player/{player.id}", "at": event.start_utc.isoformat(),
    })


def _finished(db: Session, fan: str, since: datetime) -> list[tuple[int, datetime, dict[str, Any]]]:
    """Posters for the fan's players' games that started since `since` and are over, read off their results
    (_of_result)."""
    rows = db.execute(_results(Event.start_utc >= since).join(Follow, and_(Follow.player_id == EventPlayer.player_id, Follow.fan_id == fan))).all()
    return [made for row in rows if (made := _of_result(*row)) is not None]


# ---------------------------------------------------------------- the Wire

WIRE_LIMIT = 6
LINEUP_HOURS = 24  # a line-up is news until its game is over, and no longer than this
NEWS_DAYS = 3  # as long as a story counts as just happened (pipeline.news_moments)
FACT_HOURS = 48  # as long as a fact is news after the read that found it (sidelined.py)
LEAGUE_FACTS = 3  # the most facts about players the fan doesn't follow
KNOWN = 30  # and only about players this well known (Wikipedia editions): most current footballers have under 12
# Each row's tag: a line-up, or the kind of story (a milestone is a minor moment, not on the Wire). A fact's
# tag is its sport's (Sport.out_fact).
LINEUP_TAG = "Line-up"
NEWS_TAGS = {"injury": "Injury", "transfer": "Transfer", "retirement": "Retired"}
_NEWS_KEY = re.compile(r":news:n(\d+)$")  # notify._key of a story's moment: "-:<player>:news:n<news id>"
_NEWS_KIND = re.compile(r": (\w+) news$")  # its title, pipeline.news_moments: "Bukayo Saka: injury news"
NOT_ON_THE_WIRE = ("rumour", "promo")  # a served story of these kinds (the judge's) stays in the Feed
# A site's label in front of its headline: "BREAKING:", "Breaking news -", or "BREAKING" alone in capitals.
_BREAKING = re.compile(r"^\W*(?:(?i:breaking(?:\s+news)?)\W*[:|–—-]|BREAKING(?:\s+NEWS)?\b|Breaking\s+news\b)\s*")

Row = tuple[datetime, dict[str, Any]]  # when it reached us, and the row


def _plain(headline: str) -> str:
    """A headline without a site's "BREAKING:" in front: the Wire never says it."""
    return _BREAKING.sub("", headline).strip()


def wire(db: Session, fan: str, now: datetime | None = None) -> list[dict[str, Any]]:
    """The Wire's rows, at most WIRE_LIMIT: each one's tag, its line, the source it's from (a story's site, a
    fact's source), where it opens (the story, the player's page), whether it's about one of the fan's
    players, and when it reached us, as the Alerts list dates them. The fan's players' facts first, then
    their line-ups and stories, each newest first; then while there's room facts about the best-known
    players they don't follow, at most LEAGUE_FACTS, the best known first."""
    now = now or datetime.now(timezone.utc)
    mine, others = _facts(db, fan, now)
    rows = [*mine, *_news(db, fan, now)][:WIRE_LIMIT]
    rows += others[:max(0, min(LEAGUE_FACTS, WIRE_LIMIT - len(rows)))]
    return [row | {"at": at.isoformat()} for at, row in rows]


def _news(db: Session, fan: str, now: datetime) -> list[Row]:
    """The fan's players' line-ups and stories, newest first. A player's line-up for a game is its latest (a
    late change: "now on the bench"), and a story about two of the fan's players is one row."""
    rows = db.execute(
        select(Moment, Player, Event.title)
        .join(Follow, and_(Follow.player_id == Moment.player_id, Follow.fan_id == fan))
        .join(Player, Player.id == Moment.player_id)
        .outerjoin(Event, Event.id == Moment.event_id)
        .where(or_(
            and_(Moment.kind == "lineup", Moment.created_at >= now - timedelta(hours=LINEUP_HOURS), Event.status != "final"),
            and_(Moment.kind == "news", Moment.level == "key", Moment.created_at >= now - timedelta(days=NEWS_DAYS)),
        ))
        .order_by(Moment.id.desc())
    ).all()
    found, linked = _stories(db, fan, now)
    ids = {int(m[1]) for moment, _, _ in rows if (m := _NEWS_KEY.search(moment.key))}
    stories = {n.id: n for n in db.scalars(select(NewsItem).where(NewsItem.id.in_(ids)))} if ids else {}
    told: set[tuple[str, int | None, int | None]] = set()  # line-ups by player and game, stories by id: newest first
    for moment, player, title in rows:
        row = {"id": f"m{moment.id}", "player_id": moment.player_id, "player": player.name, "sport": player.sport, "yours": True}
        if moment.kind == "lineup":
            if ("lineup", moment.player_id, moment.event_id) in told:
                continue  # an earlier team sheet, since changed
            told.add(("lineup", moment.player_id, moment.event_id))
            line = " ".join(x for x in (moment.title, versus(title, [*(player.teams or []), player.name])) if x)
            found.append((moment.created_at, moment.id, row | {"tag": LINEUP_TAG, "text": line, "source": None, "url": moment.url}))
            continue
        m = _NEWS_KEY.search(moment.key)
        story = stories.get(int(m[1])) if m else None
        # The kind the moment was told as: the story's row keeps the kind of whichever report stored it first.
        said = _NEWS_KIND.search(moment.title)
        kind = said[1] if said else story.kind if story else None
        if story is None or kind not in NEWS_TAGS or not story.url or ("news", story.id, None) in told or story.url in linked:
            continue  # gone, not a story the Wire tells, or on it already
        told.add(("news", story.id, None))
        found.append((moment.created_at, moment.id, row | {"tag": NEWS_TAGS[kind], "text": _plain(story.headline), "source": story.source or None, "url": story.url}))
    # When each reached us, as the Alerts list dates them: a story's own date is often a day with no time.
    found.sort(key=lambda f: f[:2], reverse=True)
    return [(at, row) for at, _, row in found]


def _stories(db: Session, fan: str, now: datetime) -> tuple[list[tuple[datetime, int, dict[str, Any]]], set[str]]:
    """Each of the fan's players' best story of the last NEWS_DAYS that the curator served (news.py), and their
    addresses. Rows are dated when the story reached us, as the agent's are."""
    rows = db.execute(
        select(FeedCandidate, Player)
        .join(Follow, and_(Follow.player_id == FeedCandidate.player_id, Follow.fan_id == fan))
        .join(Player, Player.id == FeedCandidate.player_id)
        .where(FeedCandidate.source.in_(list(NEWS)), FeedCandidate.served(), FeedCandidate.fetched_at >= now - timedelta(days=NEWS_DAYS))
        .order_by(FeedCandidate.score.desc(), FeedCandidate.id.desc())
    ).all()  # fmt: skip
    found: list[tuple[datetime, int, dict[str, Any]]] = []
    told: set[int] = set()
    for row, player in rows:
        kept, verdict = row.candidate or {}, row.verdict or {}
        if player.id in told or verdict.get("kind") in NOT_ON_THE_WIRE or not kept.get("title"):
            continue
        told.add(player.id)
        found.append((row.fetched_at, row.id, {
            "id": f"s{row.id}", "player_id": player.id, "player": player.name, "sport": player.sport, "yours": True,
            "tag": rank.tag(verdict, story=True), "text": _plain(str(kept["title"])), "source": str(kept.get("channel") or "") or None, "url": row.url,
        }))  # fmt: skip
    return found, {str(row["url"]) for _, _, row in found}


def _facts(db: Session, fan: str, now: datetime) -> tuple[list[Row], list[Row]]:
    """The facts of the last FACT_HOURS (sidelined.py) as their sports word them, a player's newest only: the
    fan's players', newest first, and those about players they don't follow who are at least KNOWN well
    known, the best known first. A player is the fan's by the identity they're bound by, never by name, and
    is named as their page names them. A fact opens their page here, or the source's athlete page if nobody
    follows them yet. Only the running sources' facts, and none about a player an operator removed."""
    since = now - timedelta(hours=FACT_HOURS)
    spells = db.scalars(select(Sidelined).where(Sidelined.system.in_(list(ADAPTERS)), or_(Sidelined.out_at >= since, Sidelined.back_at >= since))).all()
    if not spells:
        return [], []
    pairs = list({(s.system, s.athlete_id) for s in spells})
    held = {(system, xid): (player, followed) for system, xid, player, followed in db.execute(
        select(PlayerIdentity.system, PlayerIdentity.external_id, Player, Follow.id.is_not(None))
        .join(Player, Player.id == PlayerIdentity.player_id)
        .outerjoin(Follow, and_(Follow.player_id == Player.id, Follow.fan_id == fan))
        .where(tuple_(PlayerIdentity.system, PlayerIdentity.external_id).in_(pairs))
    )}
    blocked = registry.blocked(db)
    newest: dict[tuple[str, str], tuple[datetime, int, dict[str, Any]]] = {}
    for s in spells:
        key = (s.system, s.athlete_id)
        if key in blocked:
            continue
        sport = sports.of(s.sport)
        player, followed = held.get(key, (None, False))
        who = (player.name if player else s.name).strip()  # a name stored before the source's padding was cut
        for at, back in ((s.out_at, False), (s.back_at, True)):
            if at is None or at < since or (key in newest and newest[key][0] >= at):
                continue
            fact = sport.back_fact(who, s.kind) if back else sport.out_fact(who, s.kind, s.what, s.until)
            if fact is None:
                continue
            newest[key] = (at, s.fame, {
                "id": f"{'back' if back else 'out'}:{s.id}", "player_id": player.id if player else None, "player": who,
                "sport": player.sport if player else sport.label, "tag": fact.tag, "text": fact.line, "source": source_name(s.system),
                "url": f"/player/{player.id}" if player else f"/athlete/{s.system}/{s.athlete_id}", "yours": bool(followed),
            })
    mine = sorted((f for f in newest.values() if f[2]["yours"]), key=lambda f: f[0], reverse=True)
    others = sorted((f for f in newest.values() if not f[2]["yours"] and f[1] >= KNOWN), key=lambda f: (f[1], f[0]), reverse=True)
    return [(at, row) for at, _, row in mine], [(at, row) for at, _, row in others]
