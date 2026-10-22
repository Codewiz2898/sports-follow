"""Reading a player's results history (event_player.result, filled by structured._refresh and
history.py): pages newest first, one game in full, the latest across a fan's players, and form. Each result
says its game's match page (match), where a source has the game."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from . import form, history, sports, structured
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
    """A stored result as the app reads it. start_utc dates it: the app shows the fan's local day of the
    game, which isn't the UTC day for a US night game or a European evening seen from India."""
    start = event.start_utc.isoformat() if event.start_utc else result.get("start_utc")
    return {**result, "event_id": event.id, "date": result.get("date") or (event.start_utc.date().isoformat() if event.start_utc else None), "start_utc": start,
            "match": structured.match_path(event.key)}  # its match page's address, kept or not


def _teams(db: Session, player_id: int) -> tuple[list[str], list[str]]:
    """The player's teams as their source has them (ids, and names with the other sides they turn out for), else as
    their page names them."""
    row = structured.binding(db, player_id)
    if row is not None and row.locator:
        ref = PlayerRef(**row.locator)
        return list(ref.team_ids), [*ref.team_names, *ref.other_teams]
    player = db.get(Player, player_id)
    return [], list(player.teams or []) if player else []


def _judged(db: Session, rows: Sequence[tuple[Event, dict[str, Any] | None, int]]) -> list[dict[str, Any]]:
    """Each (game, result, player)'s result with its win, draw or loss from the player's side: as it was kept
    (structured.result_item), else, for one kept before results said it, told now by the sport from the game's final
    board (the side its source marks the winner) and the player's teams as their source has them."""
    kept = [(event, result or {}, player_id) for event, result, player_id in rows]
    missing = [(event, player_id) for event, result, player_id in kept if "outcome" not in result]
    if not missing:
        return [result for _, result, _ in kept]
    boards = dict(db.execute(select(EventState.event_id, EventState.state).where(EventState.event_id.in_(sorted({e.id for e, _ in missing})), EventState.status == "final")
                             .ext(distinct_on(EventState.event_id)).order_by(EventState.event_id, EventState.as_of.desc())).all())
    teams = {player_id: _teams(db, player_id) for player_id in {p for _, p in missing}}
    out = []
    for event, result, player_id in kept:
        if "outcome" not in result:
            ids, names = teams[player_id]
            ours = [str(t) for t in event.participants or [] if str(t) in ids]
            told = sports.of(event.sport).outcome(result.get("result") or "", names, boards.get(event.id), ours[0] if len(ours) == 1 else None)
            result = {**result, "outcome": told}
        out.append(result)
    return out


def page(db: Session, player_id: int, before: str | None = None, limit: int = 20) -> dict[str, Any]:
    """One page of the player's results, newest first, and the cursor for the next."""
    limit = max(1, min(limit, MAX_PAGE))
    query = _finished(player_id)
    after = decode(before) if before else None
    if after:
        when, event_id = after
        query = query.where(or_(_when() < when, and_(_when() == when, Event.id < event_id)))
    rows = db.execute(query.limit(limit + 1)).all()
    items = [_item(event, result) for (event, _), result in zip(rows, _judged(db, [(event, result, player_id) for event, result in rows[:limit]]))]
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
    result = _judged(db, [(event, result, player_id)])[0]
    final = db.scalar(select(EventState).where(EventState.event_id == event_id, EventState.status == "final").order_by(EventState.as_of.desc()).limit(1))
    return {
        **_item(event, result),
        "venue": event.venue,
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
    judged = _judged(db, [(event, result, pid) for event, result, pid, _ in rows])
    return [{**_item(event, result), "player_id": pid, "player_name": name} for (event, _, pid, name), result in zip(rows, judged)]


def player_form(db: Session, player_id: int, last: int) -> dict[str, Any]:
    """Form over the player's last `last` results, from their stored lines; its record counts the outcomes their
    results show (_judged)."""
    player = db.get(Player, player_id)
    if player is None:
        return {"stats": [], "note": ""}
    rows = db.execute(_finished(player_id).limit(max(1, min(last, MAX_PAGE)))).all()
    judged = _judged(db, [(event, result, player_id) for event, result in rows])
    pairs = [(result, _line(db, event.id, player_id)) for (event, _), result in zip(rows, judged)]
    stats, note = form.summarize(player.sport or "", pairs, _teams(db, player_id)[1])
    return {"stats": stats, "note": note, "count": len(pairs)}
