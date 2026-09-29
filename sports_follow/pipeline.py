"""From an agent report to the registry and a player card.

Milestone 1: the research agent is the only source, so every card is built from its report. The
report is still written into the design's tables (player, event, event_state, player_line,
news_item) so that when real adapters arrive they fill the same rows and the card builder and API
do not change.
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from . import bus
from .agent import AgentError, follow_player, gateway, live_update
from .db import session
from .models import (
    Event,
    EventPlayer,
    EventState,
    Follow,
    NewsItem,
    NewsPlayer,
    Player,
    PlayerAlias,
    PlayerCard,
    PlayerLine,
)

log = logging.getLogger("sports_follow.pipeline")

_llm = None


def llm():
    global _llm
    if _llm is None:
        _llm = gateway()
    return _llm


# ---------------------------------------------------------------- names and keys


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:120]


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _event_key(sport: str, title: str, start: datetime | None) -> str:
    day = start.date().isoformat() if start else "undated"
    raw = f"{slugify(sport)}|{slugify(title)}|{day}"
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


# ---------------------------------------------------------------- follow and resolve


def resolve_or_create(db: Session, query: str) -> Player:
    """Find the player a query already points at, or create a placeholder the build will fill."""
    alias = slugify(query)
    if not alias:
        raise ValueError("empty player name")
    player = db.scalar(select(Player).join(PlayerAlias, PlayerAlias.player_id == Player.id).where(PlayerAlias.alias == alias))
    if player:
        return player
    player = Player(slug=alias, name=query.strip(), sport="", status="unknown", teams=[])
    db.add(player)
    db.flush()
    db.add(PlayerAlias(alias=alias, player_id=player.id))
    db.add(PlayerCard(player_id=player.id, status="building", card=placeholder_card(player), version=0))
    db.flush()
    return player


def placeholder_card(player: Player) -> dict[str, Any]:
    return {
        "player_id": player.id,
        "slug": player.slug,
        "status": "building",
        "version": 0,
        "player": {"name": player.name, "sport": player.sport or "", "status": player.status, "teams": player.teams or []},
        "live": {"is_live": False, "player_stats": []},
        "news": [],
        "upcoming": [],
        "recent_results": [],
        "season_stats": [],
        "sources": [],
        "freshness": {},
    }


def _canonicalize(db: Session, player: Player, report_name: str) -> Player:
    """After a build, converge on one player per athlete.

    If the report names an athlete another player row already holds, fold this row into it:
    move the aliases and follows over and delete this one. Otherwise take the canonical slug.
    """
    canonical = slugify(report_name)
    if not canonical or canonical == player.slug:
        return player
    existing = db.scalar(select(Player).join(PlayerAlias, PlayerAlias.player_id == Player.id).where(PlayerAlias.alias == canonical))
    if existing and existing.id != player.id:
        for alias in db.scalars(select(PlayerAlias).where(PlayerAlias.player_id == player.id)):
            alias.player_id = existing.id
        for follow in db.scalars(select(Follow).where(Follow.player_id == player.id)):
            already = db.scalar(select(Follow).where(Follow.fan_id == follow.fan_id, Follow.player_id == existing.id))
            if already:
                db.delete(follow)
            else:
                follow.player_id = existing.id
        db.flush()
        moved_from = player.id
        db.delete(player)
        db.flush()
        bus.publish(moved_from, {"type": "moved", "from": moved_from, "to": existing.id})
        return existing
    player.slug = canonical
    db.execute(insert(PlayerAlias).values(alias=canonical, player_id=player.id).on_conflict_do_nothing())
    return player


# ---------------------------------------------------------------- writing a report


def _upsert_event(db: Session, player: Player, *, title: str, competition: str, start: datetime | None, venue: str | None, notes: str | None, status: str) -> Event:
    key = _event_key(player.sport, title, start)
    event = db.scalar(select(Event).where(Event.key == key))
    if event is None:
        event = Event(key=key, sport=player.sport, competition=competition or "", title=title, start_utc=start, venue=venue, notes=notes, status=status, participants=[])
        db.add(event)
        db.flush()
    else:
        event.competition = competition or event.competition
        event.venue = venue or event.venue
        event.notes = notes or event.notes
        if status == "live" or event.status in ("scheduled", "postponed"):
            event.status = status if status != "scheduled" else event.status
    db.execute(insert(EventPlayer).values(event_id=event.id, player_id=player.id).on_conflict_do_nothing())
    return event


def _write_live(db: Session, player: Player, live: dict[str, Any], as_of: datetime) -> None:
    if not live.get("is_live"):
        # A game this player was in has stopped being live: close it.
        live_events = db.scalars(select(Event).join(EventPlayer, EventPlayer.event_id == Event.id).where(EventPlayer.player_id == player.id, Event.status == "live"))
        for event in live_events:
            event.status = "final"
        return
    title = live.get("event") or "Live game"
    event = _upsert_event(db, player, title=title, competition="", start=None, venue=None, notes=None, status="live")
    db.add(EventState(event_id=event.id, as_of=as_of, status="live", score_label=live.get("score"), clock_label=live.get("clock"), state={}, source_url=live.get("source_url")))
    db.add(PlayerLine(event_id=event.id, player_id=player.id, as_of=as_of, stats={s["label"]: s["value"] for s in live.get("player_stats", [])}))


def _write_news(db: Session, player: Player, items: list[dict[str, Any]]) -> None:
    for item in items:
        url = item.get("url") or ""
        digest = hashlib.sha256((url or item.get("headline", "")).strip().lower().encode()).hexdigest()
        stmt = (
            insert(NewsItem)
            .values(dedupe_hash=digest, headline=item.get("headline", ""), summary=item.get("summary", ""), source=item.get("source", ""), url=url, published=_parse_time(item.get("published")))
            .on_conflict_do_nothing()
            .returning(NewsItem.id)
        )
        news_id = db.scalar(stmt) or db.scalar(select(NewsItem.id).where(NewsItem.dedupe_hash == digest))
        if news_id:
            db.execute(insert(NewsPlayer).values(news_id=news_id, player_id=player.id).on_conflict_do_nothing())


def _card_from_report(player: Player, report: dict[str, Any], version: int, built_at: datetime) -> dict[str, Any]:
    stamp = built_at.isoformat()
    return {
        "player_id": player.id,
        "slug": player.slug,
        "status": "ready",
        "version": version,
        "built_at": stamp,
        "player": report["player"],
        "live": report["live"],
        "news": report["news"],
        "upcoming": report["upcoming"],
        "recent_results": report["recent_results"],
        "season_stats": report["season_stats"],
        "sources": report["sources"],
        "freshness": {"live": stamp, "fixtures": stamp, "stats": stamp, "news": stamp},
    }


def apply_report(player_id: int, report: dict[str, Any]) -> int:
    """Write a full report and publish the new card. Returns the id of the player it landed on."""
    now = _now()
    with session() as db:
        player = db.get(Player, player_id)
        if player is None:
            raise AgentError("player was removed while the report was being built")
        p = report["player"]
        player.name = p["name"]
        player.sport = p["sport"]
        player.status = p["status"]
        player.nationality = p.get("nationality")
        player.role = p.get("role")
        player.teams = p.get("teams") or []
        player.disambiguation = p.get("disambiguation")
        player = _canonicalize(db, player, p["name"])

        for u in report["upcoming"]:
            _upsert_event(db, player, title=u["title"], competition=u["competition"], start=_parse_time(u.get("start_utc")), venue=u.get("venue"), notes=u.get("notes"), status="scheduled")
        _write_live(db, player, report["live"], now)
        _write_news(db, player, report["news"])

        card_row = db.get(PlayerCard, player.id)
        if card_row is None:
            card_row = PlayerCard(player_id=player.id, version=0)
            db.add(card_row)
        version = (card_row.version or 0) + 1
        card = _card_from_report(player, report, version, now)
        card_row.version = version
        card_row.status = "ready"
        card_row.error = None
        card_row.card = card
        card_row.freshness = card["freshness"]
        card_row.is_live = bool(report["live"].get("is_live"))
        card_row.built_at = now
        card_row.live_checked_at = now
        landed = player.id
    bus.store_card(landed, card)
    return landed


def apply_live(player_id: int, update_: dict[str, Any]) -> None:
    """Merge a live check into the existing card and publish it."""
    now = _now()
    with session() as db:
        player = db.get(Player, player_id)
        card_row = db.get(PlayerCard, player_id)
        if player is None or card_row is None or card_row.status != "ready":
            return
        live = update_["live"]
        _write_live(db, player, live, now)
        card = dict(card_row.card)
        card["live"] = live
        nxt = update_.get("next_event")
        if nxt:
            upcoming = [u for u in card.get("upcoming", []) if u.get("title") != nxt.get("title")]
            card["upcoming"] = [nxt, *upcoming]
            _upsert_event(db, player, title=nxt["title"], competition=nxt["competition"], start=_parse_time(nxt.get("start_utc")), venue=nxt.get("venue"), notes=nxt.get("notes"), status="scheduled")
        card["version"] = (card_row.version or 0) + 1
        card["freshness"] = {**card.get("freshness", {}), "live": now.isoformat()}
        card_row.card = card
        card_row.version = card["version"]
        card_row.freshness = card["freshness"]
        card_row.is_live = bool(live.get("is_live"))
        card_row.live_checked_at = now
    bus.store_card(player_id, card)


def fan_message(raw: str) -> str:
    """What a fan sees when a build fails. The raw provider text stays in the worker log."""
    text = raw.lower()
    if "http 402" in text or "credits" in text:
        return "The AI service behind this app is out of credit, so the page couldn't be built. Try again once credit is added."
    if "rate" in text and "limit" in text:
        return "The AI service is busy right now. Try again in a minute."
    if "unreachable" in text or "network" in text:
        return "The app couldn't reach its AI service. Try again shortly."
    if "declined" in text or "refusal" in text:
        return "The AI service declined to research this name."
    return "The research agent couldn't finish this player's page. Try again."


def mark_failed(player_id: int, message: str) -> None:
    message = fan_message(message)
    with session() as db:
        card_row = db.get(PlayerCard, player_id)
        if card_row is None:
            return
        card_row.error = message
        card = dict(card_row.card)
        # A ready card whose scheduled rebuild failed keeps serving; only a card with nothing to show fails.
        if card_row.status == "building":
            card_row.status = "failed"
        if card_row.status == "failed":
            card["status"] = "failed"
            card["error"] = message
            card_row.card = card
    bus.store_card(player_id, card)


# ---------------------------------------------------------------- jobs (run in a worker thread)


def build_card(player_id: int) -> None:
    """Run the research agent for one player and publish the result. Progress goes to the player's channel."""
    lock = f"build:{player_id}"
    if not bus.try_lock(lock, 900):
        return
    try:
        from .config import CARD_MAX_AGE

        with session() as db:
            player = db.get(Player, player_id)
            card_row = db.get(PlayerCard, player_id)
            if player is None:
                return
            # A duplicate job that starts after another build finished has nothing to do.
            if card_row and card_row.status == "ready" and card_row.built_at and card_row.built_at > _now() - timedelta(seconds=CARD_MAX_AGE // 2):
                return
            name = player.name
        for event in follow_player(llm(), name):
            if event["type"] == "progress":
                bus.publish(player_id, {"type": "progress", "player_id": player_id, "message": event["message"]})
            elif event["type"] == "result":
                apply_report(player_id, event["data"])
    except AgentError as exc:
        log.warning("build %s failed: %s", player_id, exc)
        mark_failed(player_id, str(exc))
    except Exception as exc:  # the job must always leave the card in a final state
        log.exception("build %s crashed", player_id)
        mark_failed(player_id, f"Unexpected error: {exc}")
    finally:
        bus.release(lock)


def refresh_live(player_id: int) -> None:
    lock = f"live:{player_id}"
    if not bus.try_lock(lock, 300):
        return
    try:
        with session() as db:
            player = db.get(Player, player_id)
            if player is None or not player.sport:
                return
            name, sport, teams = player.name, player.sport, list(player.teams or [])
        bus.publish(player_id, {"type": "progress", "player_id": player_id, "message": "Checking the live score…"})
        for event in live_update(llm(), name, sport, teams):
            if event["type"] == "result":
                apply_live(player_id, event["data"])
    except Exception as exc:
        log.warning("live check %s failed: %s", player_id, exc)
        bus.publish(player_id, {"type": "live_error", "player_id": player_id, "message": str(exc)})
    finally:
        bus.release(lock)


# ---------------------------------------------------------------- scheduling (design section 3)


def due_work(now: datetime | None = None) -> tuple[list[int], list[int]]:
    """What the scheduler should enqueue now: (players needing a full build, players needing a live check).

    Only followed players get work. Events move scheduled -> armed at T-30 min; armed and live
    events put every followed player in them on the live-check list; a game that has not
    reported for 12 hours is closed.
    """
    from .config import ARM_BEFORE_START, CARD_MAX_AGE, LIVE_CHECK_INTERVAL

    now = now or _now()
    builds: list[int] = []
    lives: set[int] = set()
    with session() as db:
        followed = select(Follow.player_id).distinct()
        db.execute(
            update(Event)
            .where(Event.status == "scheduled", Event.start_utc.is_not(None), Event.start_utc <= now + timedelta(seconds=ARM_BEFORE_START), Event.start_utc >= now - timedelta(hours=6))
            .values(status="armed")
        )
        db.execute(
            update(Event)
            .where(Event.status.in_(("armed", "live")), Event.start_utc.is_not(None), Event.start_utc < now - timedelta(hours=12))
            .values(status="final")
        )
        cards = db.scalars(select(PlayerCard).where(PlayerCard.player_id.in_(followed)))
        for card in cards:
            # A failed card is left alone: it waits for a fan to retry, never loops.
            if card.status == "building" and not bus.is_locked(f"build:{card.player_id}"):
                builds.append(card.player_id)
            elif card.status == "ready" and card.built_at and card.built_at < now - timedelta(seconds=CARD_MAX_AGE):
                builds.append(card.player_id)
            elif card.status == "ready" and card.is_live:
                lives.add(card.player_id)
        armed_players = db.scalars(
            select(EventPlayer.player_id)
            .join(Event, Event.id == EventPlayer.event_id)
            .where(Event.status.in_(("armed", "live")), EventPlayer.player_id.in_(followed))
        )
        lives.update(armed_players)
        stale_before = now - timedelta(seconds=LIVE_CHECK_INTERVAL)
        due_lives = []
        for pid in lives:
            card = db.get(PlayerCard, pid)
            if card and card.status == "ready" and (card.live_checked_at is None or card.live_checked_at < stale_before) and not bus.is_locked(f"live:{pid}"):
                due_lives.append(pid)
    return builds, due_lives


def unfollow(db: Session, fan_id: str, player_id: int) -> None:
    db.execute(delete(Follow).where(Follow.fan_id == fan_id, Follow.player_id == player_id))
