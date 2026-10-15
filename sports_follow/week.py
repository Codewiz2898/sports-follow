"""Today's "This week": the big moments of a fan's players over the last seven days, as posters.

Every moment was found by its sport (moments.py), and the sport also says which of them make a poster and
words it (Sport.poster): a hundred, a hat-trick, an upset. A player's game makes one poster, its biggest
moment (a hat-trick, not three goals); a moment its source took back (a goal VAR ruled out) makes none.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, exists, select
from sqlalchemy.orm import Session, aliased

from . import sports
from .models import Event, Follow, Moment, Player
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
    """The fan's players' big moments since a week ago, biggest first, then newest: one per player and game."""
    since = (now or datetime.now(timezone.utc)) - timedelta(days=DAYS)
    correction = aliased(Moment)
    rows = db.execute(
        select(Moment, Player, Event.title)
        .join(Follow, and_(Follow.player_id == Moment.player_id, Follow.fan_id == fan))
        .join(Player, Player.id == Moment.player_id)
        .outerjoin(Event, Event.id == Moment.event_id)
        .where(Moment.created_at >= since, Moment.level == "key", Moment.corrects.is_(None))
        .where(~exists().where(correction.corrects == Moment.id))
        .order_by(Moment.priority.desc(), Moment.id.desc())
    )
    found: dict[tuple[int, int | str], dict[str, Any]] = {}
    for m, player, title in rows:
        game = (m.player_id, m.event_id if m.event_id is not None else f"m{m.id}")
        if game in found:
            continue  # a bigger moment of this game is its poster
        poster = sports.of(player.sport).poster(m.kind, m.title, m.body, versus(title, [*(player.teams or []), player.name]))
        if poster is None:
            continue
        found[game] = {
            "id": m.id, "player_id": m.player_id, "player": player.name, "sport": player.sport, "event_id": m.event_id,
            "word": poster.word, "line": poster.line, "url": m.url, "at": m.created_at.isoformat(),
        }
        if len(found) == LIMIT:
            break
    return list(found.values())
