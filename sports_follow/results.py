"""Reading a player's results history (event_player.result, filled by structured._refresh and
history.py): pages newest first, one game in full, the latest across a fan's players, and form."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from . import form, history, structured
from .adapters import PlayerRef
from .models import Event, EventPlayer, EventState, Follow, Player, PlayerLine

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
MAX_PAGE = 50


def _when():
    return func.coalesce(Event.start_utc, EPOCH)


def _finished(player_id: int):
    return (
        select(Event, EventPlayer.result)
        .join(EventPlayer, EventPlayer.event_id == Event.id)
        .where(EventPlayer.player_id == player_id, Event.status == "final", EventPlayer.result.is_not(None))
        .order_by(_when().desc(), Event.id.desc())
    )


def encode(when: datetime | None, event_id: int) -> str:
    return base64.urlsafe_b64encode(f"{(when or EPOCH).isoformat()}|{event_id}".encode()).decode().rstrip("=")


def decode(cursor: str) -> tuple[datetime, int] | None:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        when, _, event_id = raw.partition("|")
        return datetime.fromisoformat(when), int(event_id)
    except (ValueError, UnicodeDecodeError):
        return None


def _item(event: Event, result: dict[str, Any]) -> dict[str, Any]:
    return {**result, "event_id": event.id, "date": result.get("date") or (event.start_utc.date().isoformat() if event.start_utc else None)}


def page(db: Session, player_id: int, before: str | None = None, limit: int = 20) -> dict[str, Any]:
    """One page of the player's results, newest first, and the cursor for the next."""
    limit = max(1, min(limit, MAX_PAGE))
    query = _finished(player_id)
    after = decode(before) if before else None
    if after:
        when, event_id = after
        query = query.where(or_(_when() < when, and_(_when() == when, Event.id < event_id)))
    rows = db.execute(query.limit(limit + 1)).all()
    items = [_item(event, result) for event, result in rows[:limit]]
    last = rows[limit - 1][0] if len(rows) > limit else None
    return {
        "results": items,
        "next": encode(last.start_utc, last.id) if last else None,
        "total": db.scalar(select(func.count()).select_from(_finished(player_id).subquery())),
        **history.progress(db, player_id),
    }


def _line(db: Session, event_id: int, player_id: int) -> dict[str, Any] | None:
    return db.scalar(select(PlayerLine.stats).where(PlayerLine.event_id == event_id, PlayerLine.player_id == player_id).order_by(PlayerLine.as_of.desc()).limit(1))


def detail(db: Session, player_id: int, event_id: int) -> dict[str, Any] | None:
    """One finished game in full: its final score, the player's whole line, and where it's from."""
    row = db.execute(_finished(player_id).where(Event.id == event_id)).first()
    if row is None:
        return None
    event, result = row
    final = db.scalar(select(EventState).where(EventState.event_id == event_id, EventState.status == "final").order_by(EventState.as_of.desc()).limit(1))
    return {
        **_item(event, result),
        "venue": event.venue,
        "start_utc": event.start_utc.isoformat() if event.start_utc else None,
        "score": final.score_label if final else None,
        "clock": final.clock_label if final else None,
        "line": _line(db, event_id, player_id),
        "source_url": (final.source_url if final else None) or result.get("source_url"),
    }


def latest_for_fan(db: Session, fan: str, limit: int = 8) -> list[dict[str, Any]]:
    """The newest results across every player the fan follows."""
    rows = db.execute(
        select(Event, EventPlayer.result, Player.id, Player.name)
        .join(EventPlayer, EventPlayer.event_id == Event.id)
        .join(Player, Player.id == EventPlayer.player_id)
        .where(EventPlayer.player_id.in_(select(Follow.player_id).where(Follow.fan_id == fan)), Event.status == "final", EventPlayer.result.is_not(None))
        .order_by(_when().desc(), Event.id.desc())
        .limit(max(1, min(limit, MAX_PAGE)))
    ).all()
    return [{**_item(event, result), "player_id": pid, "player_name": name} for event, result, pid, name in rows]


def player_form(db: Session, player_id: int, last: int) -> dict[str, Any]:
    """Form over the player's last `last` results, from their stored lines."""
    player = db.get(Player, player_id)
    if player is None:
        return {"stats": [], "note": ""}
    rows = db.execute(_finished(player_id).limit(max(1, min(last, MAX_PAGE)))).all()
    binding = structured.binding(db, player_id)
    teams = list(PlayerRef(**binding.locator).team_names) if binding and binding.locator else []
    pairs = [(result, _line(db, event.id, player_id)) for event, result in rows]
    stats, note = form.summarize(player.sport or "", pairs, teams)
    return {"stats": stats, "note": note, "count": len(pairs)}
