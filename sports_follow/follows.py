"""The players a fan follows (the follow rows): at most MAX at once, as teams (teams.MAX) and games (matches.MAX) have
theirs.

A turned-off sport's follows stay, out of sight (server._following), for when it's back, and take no room: the fan
can't see them to unfollow. So a sport turned back on can leave a fan over MAX. They keep every follow, and follow
another once they're under it again.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import sports
from .models import Follow, Player

# Players a fan follows at once: a few whole squads from campaign pages and their favourites besides, as teams
# (teams.MAX, 30) and games (matches.MAX, 20) have theirs. Each followed player is read every half hour for good.
MAX = 100


def counted(db: Session, fan: str) -> int:
    """The players a fan follows that count toward MAX: those of the sports that run (BUG-291)."""
    rows = db.execute(select(Player.sport, func.count()).join(Follow, Follow.player_id == Player.id).where(Follow.fan_id == fan).group_by(Player.sport))
    return sum(n for sport, n in rows if sports.on(sport))
