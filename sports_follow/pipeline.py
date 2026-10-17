"""From an agent report to the registry and a player card.

The research agent says who a player is and writes the profile and news. For sports with a
structured adapter (structured.py), the player is then bound to that source, which owns the
fixtures, results, stats and live score from then on; for every other sport the agent's report
fills those sections too. Both write the same tables (player, event, event_state, player_line,
news_item), so the card and the API don't care where a section came from.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, TypeVar

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import bus, matches, metrics, moments, notify, photos, shutdown, sports, structured
from .agent import AgentError, follow_player, gateway, live_update
from .config import AGENT_LIVE
from .db import session
from .models import (
    Athlete,
    Event,
    EventPlayer,
    EventState,
    Follow,
    NewsItem,
    NewsPlayer,
    Player,
    PlayerAlias,
    PlayerCard,
    PlayerIdentity,
    PlayerLine,
    SourceBinding,
)

log = logging.getLogger("sports_follow.pipeline")

# A ready card whose rebuild failed is tried again after this, and twice as long after each failure in a
# row: a slow model or a spent budget must not turn into an agent run every minute.
BUILD_RETRY = 30 * 60
# How long a build's lock lasts without the agent moving on: longer than the worker's job_timeout (900 s),
# which is longer than an agent run may take (agent.RUN_LIMIT). arq's timeout doesn't stop the build's
# thread, so the lock, not the job, says a build is still running.
BUILD_LOCK = 20 * 60
# Why a build failed with the agent off (config.AGENT): a player no source has (fan_message says so).
AGENT_OFF = "The research agent is off and no live source has this player."

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


_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def _agent_time(value: Any) -> str | None:
    """An agent's date or time as a card keeps it. A day without a time stays a day ("2026-10-20"); a
    time gets its zone, UTC when it has none (as the agent is told and _parse_time reads it), so the
    app and the scheduler mean the same instant. None for anything that isn't ISO 8601."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    dt = _parse_time(value)
    if dt is None:
        return None
    return value if _DAY.fullmatch(value) else dt.astimezone(timezone.utc).isoformat()


def _timed(item: dict[str, Any], key: str) -> dict[str, Any]:
    return {**item, key: _agent_time(item[key])} if item.get(key) else item


def _newest_first(news: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The report's stories newest first; undated ones last, in the agent's order."""

    def newest(item: dict[str, Any]) -> tuple[int, float]:
        published = _parse_time(item.get("published"))
        return (0, -published.timestamp()) if published else (1, 0.0)

    return sorted((_timed(n, "published") for n in news), key=newest)


def _agent_result(item: dict[str, Any]) -> dict[str, Any]:
    """A result the agent dated: a bare day stays a day; with a time, start_utc carries the instant (the
    app shows the fan's local day of it) and date its UTC day, as a source's results do."""
    item = _timed(item, "date")
    when = item.get("date")
    return {**item, "date": when[:10], "start_utc": when} if when and "T" in when else item


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
    named = select(Player).join(PlayerAlias, PlayerAlias.player_id == Player.id).where(PlayerAlias.alias == alias)
    player = db.scalar(named)
    if player:
        return player
    # Two follows of a new name at once: the second's insert waits for the first's transaction, does
    # nothing, and the second reads the first's player back instead of failing on the slug.
    created = db.scalar(insert(Player).values(slug=alias, name=query.strip(), sport="", status="unknown", teams=[]).on_conflict_do_nothing().returning(Player.id))
    if created is None:
        return db.scalar(named) or db.scalar(select(Player).where(Player.slug == alias))
    player = db.get(Player, created)
    db.execute(insert(PlayerAlias).values(alias=alias, player_id=player.id).on_conflict_do_nothing())
    db.add(PlayerCard(player_id=player.id, status="building", card=placeholder_card(player), version=0))
    db.flush()
    return player


def _free_slug(db: Session, name: str, sport: str, athlete_id: str) -> str:
    """The player's slug: their name, unless a namesake has it ("nikola-jokic-football")."""
    base = slugify(name) or f"athlete-{athlete_id}"
    for slug in (base, f"{base}-{slugify(sport)}", f"{base}-{slugify(sport)}-{athlete_id}"):
        if db.scalar(select(Player.id).where(Player.slug == slug)) is None:
            return slug
    return f"{base}-{athlete_id}"[:120]


T = TypeVar("T")


def _create(make: Callable[[Session], T]) -> T:
    """make(db) in a transaction of its own, run again if another request created the same athlete (or
    took the same slug) a moment before: the next run finds what that request made."""
    tries = 3
    while True:
        try:
            with session() as db:
                return make(db)
        except IntegrityError:
            tries -= 1
            if not tries:
                raise


def _claim(db: Session, player_id: int, system: str, external_id: str, evidence_url: str | None) -> None:
    """The athlete's identity, written with their new player: of two fans following them at once, the
    second's insert fails here and the whole creation is rolled back for _create to retry."""
    db.add(PlayerIdentity(player_id=player_id, system=system, external_id=external_id, confidence=1.0, evidence_url=evidence_url))
    db.flush()


def registry_sport(sport: str | None) -> str:
    """A sport as the registry names it: "football" for "Football" or "Soccer", "cs2" for "CS2"."""
    return sports.family(sport)


def _qid_holder(db: Session, qid: str) -> Any:
    """(player_id, sport) of the player holding this registry athlete's qid, their "wikidata" identity."""
    return db.execute(
        select(PlayerIdentity.player_id, Player.sport).join(Player, Player.id == PlayerIdentity.player_id).where(PlayerIdentity.system == "wikidata", PlayerIdentity.external_id == qid)
    ).first()


def _in_other_sport(db: Session, qid: str, player_sport: str | None, sport: str) -> bool:
    """Whether a player of this registry athlete is their player in another of their sports. Someone in
    two sports (a cricketer who plays chess) is a registry row in each under one qid, and only one player
    can hold the qid. A sport the registry doesn't have for them is a renamed one, not another sport."""
    theirs = registry_sport(player_sport)
    return theirs != registry_sport(sport) and theirs in db.scalars(select(Athlete.sport).where(Athlete.qid == qid)).all()


def registry_holder(db: Session, qid: str, sport: str | None) -> int | None:
    """The player already here for this registry athlete: whoever holds the qid, unless, given the sport
    picked, they're the athlete's player in another sport."""
    held = _qid_holder(db, qid)
    if held is None or (sport and _in_other_sport(db, qid, held.sport, sport)):
        return None
    return held.player_id


def start_from_registry(athlete: Any) -> tuple[int, bool]:
    """A registry athlete no live source could confirm: a player the research agent builds, told
    exactly who it is (registry.describe). Returns (player_id, created)."""

    def create(db: Session) -> tuple[int, bool]:
        held = _qid_holder(db, athlete.qid)
        other = held is not None and _in_other_sport(db, athlete.qid, held.sport, athlete.sport)
        if held is not None and not other:
            return held.player_id, False
        if other:
            # Their other sport's player holds the qid: this sport's player, if made before, has it as
            # wikidata_qid. A new one goes without the identity.
            for player_id, sport in db.execute(select(Player.id, Player.sport).where(Player.wikidata_qid == athlete.qid, Player.id != held.player_id)):
                if not _in_other_sport(db, athlete.qid, sport, athlete.sport):
                    return player_id, False
        sport = sports.of(athlete.sport).result_sport(athlete)
        slug = _free_slug(db, athlete.name, sport, athlete.qid.lower())
        player = Player(slug=slug, name=athlete.name, sport=sport, status="active", teams=list(athlete.teams or []), wikidata_qid=athlete.qid)
        db.add(player)
        db.flush()
        if not other:
            _claim(db, player.id, "wikidata", athlete.qid, f"https://www.wikidata.org/wiki/{athlete.qid}")  # as registry.link writes it
        db.execute(insert(PlayerAlias).values(alias=slug, player_id=player.id).on_conflict_do_nothing())
        db.add(PlayerCard(player_id=player.id, status="building", card=placeholder_card(player), version=0))
        return player.id, True

    return _create(create)


def start_from_pick(adapter: Any, ref: Any) -> tuple[int, bool]:
    """A fan picked this exact athlete in search: their player, created and bound if new.

    Returns (player_id, created). A new player's card is published at once from the source with
    profile and news pending (none with the agent off: the source's page is the whole page); the caller
    queues the fixtures refresh and the agent build.
    """
    from .config import AGENT

    sport = structured.sport_name(adapter, ref)

    def create(db: Session) -> tuple[int, dict[str, Any] | None]:
        holder = db.scalar(select(PlayerIdentity).where(PlayerIdentity.system == ref.system, PlayerIdentity.external_id == ref.athlete_id))
        if holder is not None:
            return holder.player_id, None
        slug = _free_slug(db, ref.name, sport, ref.athlete_id)
        individual = ref.team_ids == [ref.athlete_id]
        player = Player(slug=slug, name=ref.name, sport=sport, status="active", teams=[] if individual else [t for t in ref.team_names if t])
        db.add(player)
        db.flush()
        _claim(db, player.id, ref.system, ref.athlete_id, ref.profile_url)
        # The plain name becomes an alias only if it's free: typing it keeps finding whoever had it first.
        db.execute(insert(PlayerAlias).values(alias=slug, player_id=player.id).on_conflict_do_nothing())
        card = {**placeholder_card(player), "status": "ready", "version": 1} | ({"pending": ["profile", "news"]} if AGENT else {})
        db.add(PlayerCard(player_id=player.id, status="ready", card=card, version=1))
        return player.id, card

    player_id, card = _create(create)
    if card is None:
        return player_id, False
    try:
        structured.record(player_id, ref)  # the identity is this player's already: this writes the bindings
    except Exception:
        # Unbound, the player would never get fixtures, and the next pick would find them: start again.
        with session() as db:
            db.execute(delete(Player).where(Player.id == player_id))
        raise
    bus.store_card(player_id, card)
    return player_id, True


def placeholder_card(player: Player) -> dict[str, Any]:
    return {
        "player_id": player.id,
        "slug": player.slug,
        "status": "building",
        "version": 0,
        "player": {"name": player.name, "sport": player.sport or "", "status": player.status, "teams": player.teams or [], **photos.for_card(player)},
        "live": {"is_live": False, "player_stats": []},
        "news": [],
        "upcoming": [],
        "recent_results": [],
        "season_stats": [],
        "sources": [],
        "freshness": {},
    }


def _fold(db: Session, player: Player, into: Player) -> int:
    """Fold a player into the one who holds their athlete: move the aliases and follows over and delete
    this row. Returns the id folded away."""
    for alias in db.scalars(select(PlayerAlias).where(PlayerAlias.player_id == player.id)):
        alias.player_id = into.id
    for follow in db.scalars(select(Follow).where(Follow.player_id == player.id)):
        already = db.scalar(select(Follow).where(Follow.fan_id == follow.fan_id, Follow.player_id == into.id))
        if already:
            db.delete(follow)
        else:
            follow.player_id = into.id
    db.flush()
    moved_from = player.id
    db.delete(player)
    db.flush()
    return moved_from


def _canonicalize(db: Session, player: Player, report_name: str) -> tuple[Player, int | None]:
    """After a build, converge on one player per athlete.

    If the report names an athlete another player row already holds, fold this row into it (_fold).
    Otherwise take the canonical slug. Only a typed name converges like this: a player with an identity
    (picked in search, so the agent was told exactly who they are) is never folded into another by name,
    because sharing a name doesn't make them the same athlete (Al Ahly's Mohamed Salah isn't Liverpool's).
    Returns the player the report belongs to and, after a merge, the id that was folded away.
    """
    canonical = slugify(report_name)
    if not canonical or canonical == player.slug:
        return player, None
    existing = db.scalar(select(Player).join(PlayerAlias, PlayerAlias.player_id == Player.id).where(PlayerAlias.alias == canonical))
    if existing and existing.id != player.id:
        if db.scalar(select(PlayerIdentity.id).where(PlayerIdentity.player_id == player.id).limit(1)) is not None:
            return player, None
        return existing, _fold(db, player, existing)
    player.slug = canonical
    db.execute(insert(PlayerAlias).values(alias=canonical, player_id=player.id).on_conflict_do_nothing())
    return player, None


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


def _write_news(db: Session, player: Player, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Store the report's news; returns the items new to this player."""
    fresh = []
    for item in items:
        url = item.get("url") or ""
        digest = hashlib.sha256((url or item.get("headline", "")).strip().lower().encode()).hexdigest()
        stmt = (
            insert(NewsItem)
            .values(dedupe_hash=digest, headline=item.get("headline", ""), summary=item.get("summary", ""), source=item.get("source", ""), url=url, published=_parse_time(item.get("published")), kind=moments.news_kind(item.get("kind")))
            .on_conflict_do_nothing()
            .returning(NewsItem.id)
        )
        news_id = db.scalar(stmt) or db.scalar(select(NewsItem.id).where(NewsItem.dedupe_hash == digest))
        if news_id and db.scalar(insert(NewsPlayer).values(news_id=news_id, player_id=player.id).on_conflict_do_nothing().returning(NewsPlayer.news_id)):
            fresh.append({**item, "news_id": news_id})
    return fresh


def news_moments(name: str, items: list[dict[str, Any]], now: datetime) -> list[moments.Found]:
    """Injuries, transfers, retirements and milestones in a rebuild's new stories, published in the last
    three days.

    Only a story the agent marked breaking counts: news of this player doing it now, not an opinion piece
    on a retirement or someone else's record. The alert claims no more than the story: the player and the
    kind of news, with the story's own headline underneath.
    """
    found = []
    for item in items:
        kind = moments.news_kind(item.get("kind"))
        published = _parse_time(item.get("published"))
        if kind and item.get("breaking") is True and published and now - published <= timedelta(days=3):
            found.append(moments.Found("news", moments.NEWS_KINDS[kind], f"{name}: {kind} news", item.get("headline", ""), f"n{item['news_id']}", moments.NEWS_PRIORITY[kind]))
    return found


def _card_from_report(player: Player, report: dict[str, Any], version: int, built_at: datetime) -> dict[str, Any]:
    stamp = built_at.isoformat()
    return {
        "player_id": player.id,
        "slug": player.slug,
        "status": "ready",
        "version": version,
        "built_at": stamp,
        # The agent's report knows nothing of photos: the player's own (photos.py) stays on the card.
        "player": {**report["player"], **photos.for_card(player)},
        "live": _timed(report["live"], "as_of"),
        "news": _newest_first(report["news"]),
        "upcoming": [_timed(u, "start_utc") for u in report["upcoming"]],
        "recent_results": [_agent_result(r) for r in report["recent_results"]],
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
        moved_from = None
        if structured.binding(db, player.id) is None:
            player, moved_from = _canonicalize(db, player, p["name"])
        # A bound player's games come from their source; the agent's guesses would only duplicate them.
        # That holds for the player a typed name was just folded into, too ("Kohli" into Virat Kohli).
        bound = structured.binding(db, player.id) is not None
        player.nationality = p.get("nationality")
        player.role = p.get("role")
        player.disambiguation = p.get("disambiguation")
        if bound:
            # The source already said exactly who this is; the agent adds to that and never renames or
            # merges them (a namesake's report must not fold the footballer Nikola Jokić into the NBA one).
            report = {**report, "player": {**p, "name": player.name, "sport": player.sport, "teams": player.teams or p.get("teams") or []}}
        else:
            player.name = p["name"]
            player.sport = p["sport"]
            player.status = p["status"]
            player.teams = p.get("teams") or []

        if not bound:
            for u in report["upcoming"]:
                _upsert_event(db, player, title=u["title"], competition=u["competition"], start=_parse_time(u.get("start_utc")), venue=u.get("venue"), notes=u.get("notes"), status="scheduled")
            if AGENT_LIVE:
                _write_live(db, player, report["live"], now)
        fresh = _write_news(db, player, report["news"])

        card_row = db.get(PlayerCard, player.id)
        if card_row is None:
            card_row = PlayerCard(player_id=player.id, version=0)
            db.add(card_row)
        # A first build's news is all "new"; only a rebuild's new stories are worth a notification.
        new_moments = notify.record(db, player.id, None, news_moments(player.name, fresh, now)) if card_row.built_at else []
        version = (card_row.version or 0) + 1
        card = _card_from_report(player, report, version, now)
        if not bound and not AGENT_LIVE:
            # No source covers this sport and the agent doesn't watch games: say so rather than show a
            # score from the moment the page was built.
            card["live"] = {"is_live": False, "player_stats": [], "available": False}
        if bound and card_row.status == "ready":
            previous = card_row.card or {}
            for section in structured.OWNED:
                if section in previous:
                    card[section] = previous[section]
            card["freshness"] = {**card["freshness"], **{k: v for k, v in (previous.get("freshness") or {}).items() if k in ("live", "fixtures", "stats")}}
            source_urls = [v.get("url") for v in (previous.get("provenance") or {}).values() if v.get("url")]
            card["sources"] = list(dict.fromkeys([*source_urls, *card["sources"]]))
        card_row.version = version
        card_row.status = "ready"
        card_row.error = None
        card_row.card = card
        card_row.freshness = card["freshness"]
        card_row.is_live = bool(card["live"].get("is_live"))
        card_row.built_at = now
        card_row.live_checked_at = now
        landed = player.id
    # Only after the commit: subscribers must never hear about a merge that rolled back.
    if moved_from is not None:
        bus.sync_redis().delete(bus.card_key(moved_from))
        bus.publish(moved_from, {"type": "moved", "from": moved_from, "to": landed})
    bus.store_card(landed, card)
    notify.dispatch(new_moments)
    return landed


def apply_live(player_id: int, update_: dict[str, Any]) -> None:
    """Merge a live check into the existing card and publish it."""
    now = _now()
    with session() as db:
        player = db.get(Player, player_id)
        card_row = db.get(PlayerCard, player_id)
        if player is None or card_row is None or card_row.status != "ready":
            return
        live = _timed(update_["live"], "as_of")
        _write_live(db, player, live, now)
        card = dict(card_row.card)
        card["live"] = live
        nxt = update_.get("next_event")
        if nxt:
            nxt = _timed(nxt, "start_utc")
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
    if raw == AGENT_OFF:
        return "No live source has this player, so there's no page to show yet."
    text = raw.lower()
    if "budget_exceeded" in text:
        # The gateway's per-app daily budget (llm-providers 0.4); it resets at local midnight.
        return "Today's AI budget for this app is used up, so new pages can't be built until midnight. Scores from live sources keep updating."
    if "http 402" in text or "credits" in text:
        # Out of credit with the model provider: the app's account is ours to know, not the fan's.
        return "AI research isn't available right now. Try again later."
    if "rate" in text and "limit" in text:
        return "The AI service is busy right now. Try again in a minute."
    if "unreachable" in text or "network" in text:
        return "The app couldn't reach its AI service. Try again shortly."
    if "declined" in text or "refusal" in text:
        return "The AI service declined to research this name."
    return "The research agent couldn't finish this player's page. Try again."


def _retry_key(player_id: int) -> str:
    return f"buildretry:{player_id}"


def back_off(player_id: int, now: datetime) -> datetime:
    """Note a failed rebuild. Returns when the scheduler may try again: BUILD_RETRY after the first
    failure, twice as long after each one in a row, never longer than CARD_MAX_AGE."""
    from .config import CARD_MAX_AGE

    r = bus.sync_redis()
    raw = r.get(_retry_key(player_id))
    failures = (json.loads(raw)["failures"] if raw else 0) + 1
    wait = min(BUILD_RETRY * 2 ** (failures - 1), CARD_MAX_AGE)
    retry_at = now + timedelta(seconds=wait)
    # Kept past retry_at, so the next failure in a row finds the count; a build that succeeds clears it.
    r.set(_retry_key(player_id), json.dumps({"failures": failures, "retry_at": retry_at.isoformat()}), ex=2 * wait)
    return retry_at


def _backing_off(player_id: int, now: datetime) -> bool:
    raw = bus.sync_redis().get(_retry_key(player_id))
    retry_at = _parse_time(json.loads(raw).get("retry_at")) if raw else None
    return retry_at is not None and now < retry_at


def mark_failed(player_id: int, message: str) -> None:
    metrics.card("build", "failed")
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
        else:
            # The scheduler rebuilds a ready card: it waits before trying again, not one tick later.
            retry_at = back_off(player_id, _now())
            log.info("build %s: next try after %s", player_id, retry_at.isoformat(timespec="minutes"))
            if card.get("pending"):
                # Published from a source before the agent ran: scores stay, the missing part says why.
                card.pop("pending")
                card["pending_error"] = message
                card_row.card = card
    bus.store_card(player_id, card)


# ---------------------------------------------------------------- jobs (run in a worker thread)


def build_card(player_id: int) -> None:
    """Run the research agent for one player and publish the result. Progress goes to the player's channel."""
    lock = f"build:{player_id}"
    token = bus.try_lock(lock, BUILD_LOCK)
    if not token:
        return
    try:
        from .config import AGENT, CARD_MAX_AGE

        with session() as db:
            player = db.get(Player, player_id)
            card_row = db.get(PlayerCard, player_id)
            if player is None:
                return
            # A duplicate job that starts after another build finished has nothing to do.
            if card_row and card_row.status == "ready" and card_row.built_at and card_row.built_at > _now() - timedelta(seconds=CARD_MAX_AGE // 2):
                return
            name = player.name
            first_build = card_row is not None and card_row.status == "building" and structured.binding(db, player_id) is None
            # A registry athlete was already looked for in the live source when followed; a name search
            # now could only find a namesake.
            from_registry = db.scalar(select(PlayerIdentity.id).where(PlayerIdentity.player_id == player_id, PlayerIdentity.system == "wikidata")) is not None
        if first_build and not from_registry:
            # A name one source recognizes gets its page from that source in seconds (no model needed); the
            # agent then adds the profile and news. Anything else waits for the agent to say who it is.
            bus.publish(player_id, {"type": "progress", "player_id": player_id, "message": "Looking the name up in live sports sources…"})
            found = structured.identify(name)
            if found:
                landed = _start_from_source(player_id, *found)
                if landed is None or landed != player_id:
                    return  # the athlete was already followed under another name: that page serves this fan
                name = found[1].name
                if AGENT:
                    bus.publish(player_id, {"type": "progress", "player_id": player_id, "message": "Scores and fixtures are in. Researching news and background…"})
        if not AGENT:
            _without_agent(player_id)
            return
        for event in follow_player(llm(), name, structured.describe(player_id)):
            if shutdown.stopping.is_set():
                return  # the worker is stopping; the build starts over on the next one (arq queues it again)
            bus.extend(lock, BUILD_LOCK, token)  # held while the agent is working, however long that takes
            if event["type"] == "progress":
                bus.publish(player_id, {"type": "progress", "player_id": player_id, "message": event["message"]})
            elif event["type"] == "result":
                landed = apply_report(player_id, event["data"])
                metrics.card("build", "ok")
                bus.sync_redis().delete(_retry_key(player_id))
                bus.publish(landed, {"type": "progress", "player_id": landed, "message": "Linking live scores and fixtures…"})
                if structured.bind(landed):
                    structured.refresh(landed)
    except AgentError as exc:
        log.warning("build %s failed: %s", player_id, exc)
        mark_failed(player_id, str(exc))
    except Exception as exc:  # the job must always leave the card in a final state
        log.exception("build %s crashed", player_id)
        mark_failed(player_id, f"Unexpected error: {exc}")
    finally:
        bus.release(lock, token)


def _start_from_source(player_id: int, adapter: Any, ref: Any) -> int | None:
    """Publish a usable card from the source alone, before the agent has run.

    Returns the player the name landed on: this one, or an existing player who already holds the
    athlete (followed earlier under another spelling), into which this one is folded.
    """
    from .config import AGENT

    sport = structured.sport_name(adapter, ref)  # "Football", "Cricket", "Chess"; an esports pro's game: "CS2", "BGMI"
    individual = ref.team_ids == [ref.athlete_id]
    moved_from = None
    with session() as db:
        player = db.get(Player, player_id)
        if player is None:
            return None
        holder = db.scalar(select(PlayerIdentity).where(PlayerIdentity.system == ref.system, PlayerIdentity.external_id == ref.athlete_id))
        if holder and holder.player_id != player_id:
            # Folded into the holder by id: looking their name up again could find a namesake who has the alias.
            existing = db.get(Player, holder.player_id)
            moved_from = _fold(db, player, existing)
            player = existing
        else:
            player.name, player.sport, player.status = ref.name, sport, "active"
            player.teams = [] if individual else [t for t in ref.team_names if t]
            player, moved_from = _canonicalize(db, player, ref.name)
            card_row = db.get(PlayerCard, player.id)
            if card_row is not None and card_row.status != "ready":
                version = (card_row.version or 0) + 1
                card = {**placeholder_card(player), "status": "ready", "version": version} | ({"pending": ["profile", "news"]} if AGENT else {})
                card_row.card, card_row.status, card_row.version, card_row.error = card, "ready", version, None
        landed = player.id
    if moved_from is not None:
        bus.sync_redis().delete(bus.card_key(moved_from))
        bus.publish(moved_from, {"type": "moved", "from": moved_from, "to": landed})
        return landed
    if structured.bind(landed, ref):
        structured.refresh(landed)
    else:
        with session() as db:
            # Not bindable after all: back to a plain first build.
            card_row = db.get(PlayerCard, landed)
            if card_row is not None and (card_row.card or {}).get("pending"):
                card_row.status = "building"
                card_row.card = {**card_row.card, "status": "building"}
    return landed


def _without_agent(player_id: int) -> None:
    """A build with the research agent off (config.AGENT), once a typed name has been looked up in the
    sources (build_card). A page that exists stays as it is: one from a source that was waiting for the
    profile and news stops waiting. A first build no source took can't be made."""
    with session() as db:
        card_row = db.get(PlayerCard, player_id)
        if card_row is None or (card_row.status == "ready" and not (card_row.card or {}).get("pending")):
            return
        if card_row.status == "ready" or structured.binding(db, player_id) is not None:
            # A bound card that isn't ready (none should be) gets the source's sections from the scheduler's refresh.
            card = {k: v for k, v in (card_row.card or {}).items() if k != "pending"} | {"status": "ready", "version": (card_row.version or 0) + 1}
            card_row.card, card_row.status, card_row.version, card_row.error = card, "ready", card["version"], None
        else:
            card = None
    if card is None:
        mark_failed(player_id, AGENT_OFF)
        return
    bus.store_card(player_id, card)


def refresh_live(player_id: int) -> None:
    lock = f"live:{player_id}"
    token = bus.try_lock(lock, 300)  # longer than a live check may take (agent.LIVE_LIMIT)
    if not token:
        return
    try:
        with session() as db:
            player = db.get(Player, player_id)
            if player is None or not player.sport:
                return
            name, sport, teams = player.name, player.sport, list(player.teams or [])
        bus.publish(player_id, {"type": "progress", "player_id": player_id, "message": "Checking the live score…"})
        for event in live_update(llm(), name, sport, teams):
            if shutdown.stopping.is_set():
                return
            if event["type"] == "result":
                apply_live(player_id, event["data"])
    except Exception as exc:
        log.warning("live check %s failed: %s", player_id, exc)
        bus.publish(player_id, {"type": "live_error", "player_id": player_id, "message": str(exc)})
    finally:
        bus.release(lock, token)


# ---------------------------------------------------------------- scheduling (design section 3)


@dataclass
class Due:
    builds: list[int] = field(default_factory=list)  # players whose card needs the research agent
    lives: list[int] = field(default_factory=list)  # unbound players who need an agent live check
    refreshes: list[int] = field(default_factory=list)  # bound players whose fixtures and stats are stale
    polls: list[int] = field(default_factory=list)  # events with a live binding that are armed or live
    lanes: list[str] = field(default_factory=list)  # sources whose armed and live events are polled together


def rebuild_due(card: PlayerCard, now: datetime) -> bool:
    """Whether the research agent should build this card now: a first build nobody is running, or a ready
    card that is stale or never got the agent's profile and news (published from a source).

    A failed card is left alone: it waits for a fan to retry. A ready card whose rebuild failed waits
    for its back_off, and none is queued again while its build is still running, so nothing loops.
    With the agent off (config.AGENT) a ready card has nothing to gain from one: only a first build and
    a source's page still waiting for the agent are settled (_without_agent).
    """
    from .config import AGENT, CARD_MAX_AGE

    stale = card.built_at is None or card.built_at < now - timedelta(seconds=CARD_MAX_AGE)
    waiting = stale if AGENT else bool((card.card or {}).get("pending"))
    wanted = card.status == "building" or (card.status == "ready" and waiting)
    return wanted and not bus.is_locked(f"build:{card.player_id}") and not _backing_off(card.player_id, now)


def to_arm(games: Iterable[tuple[int, str | None, datetime | None, bool]], now: datetime) -> list[int]:
    """Of scheduled games (id, sport, start, whether a source polls it), those to arm now: each within its
    sport's lead. A game no source polls gets agent live checks once armed, which can't see a team sheet,
    so it keeps the usual lead."""
    return [game for game, sport, start, polled in games if start is not None and start <= now + structured.arm_before(sport if polled else None)]


def due_work(now: datetime | None = None) -> Due:
    """What the scheduler should enqueue now. Only followed players get work, and games fans follow by
    themselves (matches.py): those are polled as a followed player's are, and their follows lapse once
    they're over.

    Events move scheduled -> armed at T-30 min, or earlier for a sport whose team sheets come out sooner
    (football's, at T-75 min: structured.arm_before). An armed or live event with a live binding gets one
    poll job, shared by every player in it, or, from a source that fetches many events in one
    request, a place in that source's lane; players without a binding get agent live checks.
    Events nobody closed are closed: after 12 hours from the agent, after 6 days from a source
    (a Test match runs five), and a card still showing one live stops. A bound player whose refreshes
    keep failing waits longer each time (structured.retry_at), and none is refreshed while their
    source is down to what its live games need (structured.spare).
    """
    from .config import FIXTURES_MAX_AGE, LIVE_CHECK_INTERVAL

    now = now or _now()
    due = Due()
    lives: set[int] = set()
    with session() as db:
        followed = select(Follow.player_id).distinct()
        window = (Event.status == "scheduled", Event.start_utc.is_not(None), Event.start_utc <= now + structured.arm_before_any(), Event.start_utc >= now - timedelta(hours=6))
        soon = db.execute(select(Event.id, Event.sport, Event.start_utc, Event.live_binding.is_not(None)).where(*window)).all()
        arm = to_arm(soon, now)
        if arm:
            db.execute(update(Event).where(Event.id.in_(arm), *window).values(status="armed"))
        db.execute(
            update(Event)
            .where(Event.status.in_(("armed", "live")), Event.live_binding.is_(None), Event.start_utc.is_not(None), Event.start_utc < now - timedelta(hours=12))
            .values(status="final")
        )
        closed = db.scalars(
            update(Event)
            .where(Event.status.in_(("armed", "live")), Event.live_binding.is_not(None), Event.start_utc.is_not(None), Event.start_utc < now - timedelta(days=6))
            .values(status="final")
            .returning(Event.id)
        ).all()
        # Closed without a final poll (the source stopped returning the game): its cards stop saying LIVE.
        unstuck = structured.close_live(db, list(closed))
        # Games followed by themselves that are over: nothing more of them is sent. The rest settle on the event
        # of a player their fan follows, where the game's here as several.
        matches.lapse(db, now)
        matches.settle(db)
        # Each followed player's fixtures binding (structured.binding()'s pick: highest priority, newest),
        # of the sources running as this tick began: the admin panel may turn one off while it runs.
        running = dict(structured.ADAPTERS.items())
        bindings: dict[int, tuple[str, dict[str, Any] | None]] = {}
        for pid, adapter, health in db.execute(
            select(SourceBinding.player_id, SourceBinding.adapter, SourceBinding.health)
            .where(SourceBinding.purpose == "fixtures", SourceBinding.adapter.in_(list(running)), SourceBinding.player_id.in_(followed))
            .order_by(SourceBinding.priority.desc(), SourceBinding.id.desc())
        ).all():
            bindings.setdefault(pid, (adapter, health))
        room = {name: structured.spare(running[name]) for name, _ in bindings.values()}
        fixtures_stale = now - timedelta(seconds=FIXTURES_MAX_AGE)
        sport_of = dict(db.execute(select(Player.id, Player.sport).where(Player.id.in_(followed))).all())
        for card in db.scalars(select(PlayerCard).where(PlayerCard.player_id.in_(followed))):
            pid = card.player_id
            if not sports.on(sport_of.get(pid)):
                continue  # a sport this build doesn't run: the page stays as it is
            if rebuild_due(card, now):
                due.builds.append(pid)
            if card.status != "ready":
                continue
            if pid in bindings:
                adapter, health = bindings[pid]
                checked = _parse_time((card.freshness or {}).get("fixtures"))
                retry = structured.retry_at(health)
                if (not checked or checked < fixtures_stale) and not (retry and now < retry) and room[adapter] and not bus.is_locked(f"structured:{pid}"):
                    due.refreshes.append(pid)
                continue
            # Followed before their sport had an adapter: the refresh binds them first.
            preferred = structured.for_sport(sport_of.get(pid))
            if preferred and structured.spare(preferred) and not bus.is_locked(f"bindtry:{pid}") and not bus.is_locked(f"structured:{pid}"):
                due.refreshes.append(pid)
            if card.is_live:
                lives.add(pid)
        armed = db.execute(
            select(Event.id, Event.live_binding, EventPlayer.player_id)
            .join(EventPlayer, EventPlayer.event_id == Event.id)
            .where(Event.status.in_(("armed", "live")), EventPlayer.player_id.in_(followed))
        ).all()
        polls: set[int] = set()
        lanes: set[str] = set()
        for event_id, live_binding, pid in armed:
            if not sports.on(sport_of.get(pid)):
                continue  # a sport turned off isn't polled, though its source runs for another (an esports game)
            if live_binding and structured.batched(live_binding.get("adapter")):
                lanes.add(live_binding["adapter"])
            elif live_binding:
                polls.add(event_id)
            elif pid not in bindings:
                lives.add(pid)
        # Games followed by themselves, nobody's player in them or not: polled the same way, by their source. One
        # with nobody's player in it waits while its source is held back (structured.holds_back): followed
        # players' games keep the reserve. A follow of a game here as two events (a chess board) moves first to
        # the one a player its fan follows is in (matches.settle).
        theirs = {event_id for event_id, _, _ in armed}
        itself = db.execute(select(Event.id, Event.live_binding, Event.sport).where(Event.status.in_(("armed", "live")), Event.live_binding.is_not(None), Event.id.in_(matches.watched()))).all()
        for event_id, live_binding, sport in itself:
            source = (live_binding or {}).get("adapter")
            if not sports.on(sport) or source not in running:
                continue
            if structured.batched(source):
                lanes.add(source)
            elif event_id in theirs or not structured.holds_back(running[source]):
                polls.add(event_id)
        due.polls = [e for e in sorted(polls) if not bus.is_locked(f"poll:{e}")]
        due.lanes = [s for s in sorted(lanes) if not bus.is_locked(f"lane:{s}")]
        stale_before = now - timedelta(seconds=LIVE_CHECK_INTERVAL)
        for pid in lives if AGENT_LIVE else ():  # off by default: no model calls spent watching games
            card = db.get(PlayerCard, pid)
            if card and card.status == "ready" and (card.live_checked_at is None or card.live_checked_at < stale_before) and not bus.is_locked(f"live:{pid}"):
                due.lives.append(pid)
    for pid, card in unstuck:
        bus.store_card(pid, card)
    return due


def unfollow(db: Session, fan_id: str, player_id: int) -> None:
    db.execute(delete(Follow).where(Follow.fan_id == fan_id, Follow.player_id == player_id))
