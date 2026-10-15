"""Today's "This week" and the Wire, from the moments stored for a fan's players (moments.py).

This week is the big moments of the last seven days, as posters. Every moment was found by its sport, and
the sport also says which of them make a poster and words it (Sport.poster): a hundred, a hat-trick, an
upset. A player's game makes one poster, its biggest moment (a hat-trick, not three goals); a moment its
source took back (a goal VAR ruled out) makes none.

The Wire is the news about them, put plainly: line-ups for games still to finish, and the injuries,
transfers and retirements the research agent found in the news and marked as just happened
(pipeline.news_moments), each by the story's own headline with the site that ran it. Those headlines are
web search results, not our facts, so the Wire names where each is from. It never says "breaking".
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session, aliased

from . import sports
from .moments import Found
from .models import Event, Follow, Moment, NewsItem, Player
from .naming import team_key

DAYS = 7
LIMIT = 20  # posters at most, the biggest first: the app shows 12 once it has left out those on its Results rail

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
    (the priority of the moment it's read from), then the newest."""
    since = (now or datetime.now(timezone.utc)) - timedelta(days=DAYS)
    correction = aliased(Moment)
    rows = db.execute(
        select(Moment, Player, Event.title)
        .join(Follow, and_(Follow.player_id == Moment.player_id, Follow.fan_id == fan))
        .join(Player, Player.id == Moment.player_id)
        .outerjoin(Event, Event.id == Moment.event_id)
        .where(Moment.created_at >= since, Moment.level == "key", Moment.corrects.is_(None))
        .where(~exists().where(correction.corrects == Moment.id))
        .order_by(Moment.id)
    ).all()
    games: dict[tuple[int, int | str], list[tuple[Moment, Player, str | None]]] = {}
    for m, player, title in rows:
        games.setdefault((m.player_id, m.event_id if m.event_id is not None else f"m{m.id}"), []).append((m, player, title))
    made: list[tuple[int, int, dict[str, Any]]] = []
    for game in games.values():
        _, player, title = game[0]
        found = [(Found(m.kind, m.level, m.title, m.body, priority=m.priority), m) for m, _, _ in game]
        poster = sports.of(player.sport).poster([f for f, _ in found], versus(title, [*(player.teams or []), player.name]))
        if poster is None:
            continue
        m = next((m for f, m in found if f is poster.of), game[-1][0])
        made.append((m.priority, m.id, {
            "id": m.id, "player_id": m.player_id, "player": player.name, "sport": player.sport, "event_id": m.event_id,
            "word": poster.word, "line": poster.line, "url": m.url, "at": m.created_at.isoformat(),
        }))
    made.sort(key=lambda x: x[:2], reverse=True)
    return [p for _, _, p in made[:LIMIT]]


# ---------------------------------------------------------------- the Wire

WIRE_LIMIT = 6
LINEUP_HOURS = 24  # a line-up is news until its game is over, and no longer than this
NEWS_DAYS = 3  # as long as a story counts as just happened (pipeline.news_moments)
# Each row's tag: a line-up, or the kind of story (a milestone is a minor moment, not on the Wire).
LINEUP_TAG = "Line-up"
NEWS_TAGS = {"injury": "Injury", "transfer": "Transfer", "retirement": "Retired"}
_NEWS_KEY = re.compile(r":news:n(\d+)$")  # notify._key of a story's moment: "-:<player>:news:n<news id>"
_BREAKING = re.compile(r"^\W*breaking(?:\s+news)?\W*[:|–—-]\s*", re.I)


def _plain(headline: str) -> str:
    """A headline without a site's "BREAKING:" in front: the Wire never says it."""
    return _BREAKING.sub("", headline).strip()


def wire(db: Session, fan: str, now: datetime | None = None) -> list[dict[str, Any]]:
    """The Wire's rows for the fan's players, newest first: each one's tag, its line, the site it's from (a
    story's) and where it opens (the story, or the player's page)."""
    now = now or datetime.now(timezone.utc)
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
    ids = {int(m[1]) for moment, _, _ in rows if (m := _NEWS_KEY.search(moment.key))}
    stories = {n.id: n for n in db.scalars(select(NewsItem).where(NewsItem.id.in_(ids)))} if ids else {}
    found: list[tuple[datetime, dict[str, Any]]] = []
    for moment, player, title in rows:
        row = {"id": moment.id, "player_id": moment.player_id, "player": player.name, "sport": player.sport}
        if moment.kind == "lineup":
            line = " ".join(x for x in (moment.title, versus(title, [*(player.teams or []), player.name])) if x)
            found.append((moment.created_at, row | {"tag": LINEUP_TAG, "text": line, "source": None, "url": moment.url}))
            continue
        m = _NEWS_KEY.search(moment.key)
        story = stories.get(int(m[1])) if m else None
        if story is None or story.kind not in NEWS_TAGS or not story.url:
            continue  # gone, or not a story the Wire tells
        found.append((moment.created_at, row | {"tag": NEWS_TAGS[story.kind], "text": _plain(story.headline), "source": story.source or None, "url": story.url}))
    # When each reached us, as the Alerts list dates them: a story's own date is often a day with no time.
    found.sort(key=lambda f: (f[0], f[1]["id"]), reverse=True)
    return [row | {"at": at.isoformat()} for at, row in found[:WIRE_LIMIT]]
