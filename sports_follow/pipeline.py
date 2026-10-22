"""From a source, Wikidata and Wikipedia, or an agent report, to the registry and a player card.

A player with a Wikidata person gets the profile on their page (birth date, country, teams, role,
honours) from Wikidata and the About paragraph from their English Wikipedia article (wiki.py), with no
model. For sports with a structured adapter (structured.py), the player is bound to that source, which
owns their name, teams, fixtures, results, stats and live score. The research agent writes only what
neither covers (needs_agent), when a fan opens the page: the background of a player with a live source and
no article, monthly, and, with research on (config.RESEARCH), the whole page of a player no source has,
saying who they are. A player no live source has gets no page otherwise. All of them write the same tables (player, event, event_state,
player_line, news_item), so the card and the API don't care where a section came from.
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
from typing import Any, TypeVar, cast

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import bus, livecards, matches, metrics, moments, naming, notify, photos, predictions, shutdown, sports, structured, wiki
from .agent import AgentError, follow_player, gateway, live_update
from .config import AGENT_LIVE
from .db import session
from .models import (
    Athlete,
    Clip,
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
# Why a build failed without research (config.RESEARCH, or the agent off): a player no source has (fan_message says so).
AGENT_OFF = "No live source has this player, and research is off."
# Why a first build failed for a typed name only athletes nobody here follows have (structured.identify's
# NOT_FOUND): no page, and no research. Its fan's text, which asking again answers at once for NOT_FOUND_FOR
# (server.follow); after that the sources are asked again, as a college player may have turned pro.
NOT_FOLLOWED = "Only athletes nobody here follows have this name."
NOT_FOUND_TEXT = "No player here goes by this name. Check the spelling, or pick them from the search results."
NOT_FOUND_FOR = 24 * 3600

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
    """A fan picked this exact athlete in search: their player, created and bound if new, and if not, bound to the
    teams the pick names where those have changed (a transfer).

    Returns (player_id, created). A new player's card is published at once from the source with its
    profile pending; the caller queues the fixtures refresh and the build, which reads the profile.
    """
    sport = structured.sport_name(adapter, ref)

    def create(db: Session) -> tuple[int, dict[str, Any] | None, bool]:
        holder = db.scalar(select(PlayerIdentity).where(PlayerIdentity.system == ref.system, PlayerIdentity.external_id == ref.athlete_id))
        if holder is not None:
            bound = db.scalar(select(SourceBinding.locator).where(SourceBinding.player_id == holder.player_id, SourceBinding.purpose == "fixtures", SourceBinding.adapter == ref.system))
            return holder.player_id, None, isinstance(bound, dict) and set(bound.get("team_ids") or []) != set(ref.team_ids)
        slug = _free_slug(db, ref.name, sport, ref.athlete_id)
        individual = ref.team_ids == [ref.athlete_id]
        player = Player(slug=slug, name=ref.name, sport=sport, status="active", teams=[] if individual else [t for t in ref.team_names if t])
        db.add(player)
        db.flush()
        _claim(db, player.id, ref.system, ref.athlete_id, ref.profile_url)
        # The plain name becomes an alias only if it's free: typing it keeps finding whoever had it first.
        db.execute(insert(PlayerAlias).values(alias=slug, player_id=player.id).on_conflict_do_nothing())
        card = {**placeholder_card(player), "status": "ready", "version": 1, "pending": ["profile"]}
        db.add(PlayerCard(player_id=player.id, status="ready", card=card, version=1))
        return player.id, card, False

    player_id, card, moved = _create(create)
    if card is None:
        # Here already. The pick is the source's latest word on them: one bound to other teams (they've moved since)
        # moves to the new team's games now (structured.record_move), and a move that fails never stops the follow.
        if moved:
            try:
                structured.record_move(player_id, adapter, ref)
            except Exception:
                log.exception("pick %s: couldn't move them to the teams %s names", player_id, ref.system)
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
    """Fold a player into the one who holds their athlete: move the aliases, follows and clips over and delete
    this row. Returns the id folded away."""
    for alias in db.scalars(select(PlayerAlias).where(PlayerAlias.player_id == player.id)):
        alias.player_id = into.id
    for follow in db.scalars(select(Follow).where(Follow.player_id == player.id)):
        already = db.scalar(select(Follow).where(Follow.fan_id == follow.fan_id, Follow.player_id == into.id))
        if already:
            db.delete(follow)
        else:
            follow.player_id = into.id
    for clip in db.scalars(select(Clip).where(Clip.players.contains([player.id]))):  # tags are ids in a list: nothing else moves them
        clip.players = list(dict.fromkeys(into.id if p == player.id else p for p in clip.players))
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
        card_row = db.get(PlayerCard, player.id)
        previous = (card_row.card or {}) if card_row is not None else {}
        # What Wikidata and Wikipedia gave the page wins over the agent's (keep_profile).
        theirs = set((previous.get("player") or {}).get("profile_from") or {})
        if "nationality" not in theirs:
            player.nationality = p.get("nationality")
        if "role" not in theirs:
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
            if "teams" not in theirs:
                player.teams = p.get("teams") or []

        if not bound:
            for u in report["upcoming"]:
                _upsert_event(db, player, title=u["title"], competition=u["competition"], start=_parse_time(u.get("start_utc")), venue=u.get("venue"), notes=u.get("notes"), status="scheduled")
            if AGENT_LIVE:
                _write_live(db, player, report["live"], now)
        # A bound player's news is the publishers' (news.py): the agent writes only their background (BHA-334).
        fresh = [] if bound else _write_news(db, player, report["news"])

        if card_row is None:
            card_row = PlayerCard(player_id=player.id, version=0)
            db.add(card_row)
        # A first build's news is all "new"; only a rebuild's new stories are worth a notification.
        new_moments = notify.record(db, player.id, None, news_moments(player.name, fresh, now)) if card_row.built_at else []
        version = (card_row.version or 0) + 1
        card = _card_from_report(player, report, version, now)
        card["player"] = keep_profile(previous.get("player") or {}, card["player"])
        if bound:
            # Nor what an earlier agent wrote: the page's news is the publishers' stories alone (BHA-334).
            card["news"] = []
            card["freshness"].pop("news", None)
        if (previous.get("freshness") or {}).get("profile"):
            card["freshness"]["profile"] = previous["freshness"]["profile"]
        if not bound and not AGENT_LIVE:
            # No source covers this sport and the agent doesn't watch games: say so rather than show a
            # score from the moment the page was built.
            card["live"] = {"is_live": False, "player_stats": [], "available": False}
        if (card_row.card or {}).get("stories"):
            card["stories"] = card_row.card["stories"]  # the publishers' stories the curator put there (rank.cards) stay
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
    if raw == NOT_FOLLOWED:
        return NOT_FOUND_TEXT
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


def not_found_key(player_id: int) -> str:
    """Held for NOT_FOUND_FOR once a player's typed name was found to be nobody's here (NOT_FOLLOWED)."""
    return f"notfound:{player_id}"


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


def mark_failed(player_id: int, message: str, result: str = "failed") -> None:
    """Fail a build: a card with nothing to show says why. Counted as the build's result (a typed name that
    isn't found isn't a failure the alerts hear of)."""
    metrics.card("build", result)
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


# ---------------------------------------------------------------- the profile, from Wikidata and Wikipedia (wiki.py)

# A profile Wikidata or Wikipedia couldn't give (they didn't answer, or asked us to wait) is asked for again after this.
PROFILE_RETRY = 3600
PROFILES_PER_TICK = 5  # monthly re-reads queued a minute at most: three requests each, one at a time
WIKIPEDIA = {"source": "Wikipedia", "licence": wiki.LICENCE, "licence_url": wiki.LICENCE_URL}


def player_qid(db: Session, player_id: int) -> str | None:
    """The player's Wikidata person: their "wikidata" identity, or the qid a player in an athlete's second
    sport carries (start_from_registry). An esports pro's registry id is their source's ("pandascore:20401"):
    their person is the one their registry row was matched to (roster_wikidata.py), if any."""
    held = db.scalar(select(PlayerIdentity.external_id).where(PlayerIdentity.player_id == player_id, PlayerIdentity.system == "wikidata"))
    qid = held or db.scalar(select(Player.wikidata_qid).where(Player.id == player_id))
    if qid and not wiki.QID.fullmatch(qid):
        ids = db.scalar(select(Athlete.ids).where(Athlete.qid == qid).limit(1)) or {}
        qid = ids.get("wikidata")
    return qid if isinstance(qid, str) and wiki.QID.fullmatch(qid) else None


def merge_profile(player: dict[str, Any], found: wiki.Profile | None, bound: bool) -> dict[str, Any]:
    """A card's player section with what Wikidata and Wikipedia say in it, and in profile_from which they
    gave ({"born": "wikidata", "summary": "wikipedia"}). A live source's facts win (a bound player's teams
    are their source's: the ones Wikidata gave before they were bound stay, no longer Wikidata's); Wikidata's
    and Wikipedia's win over the research agent's. What they gave before and no longer say (an award taken
    back, an article deleted) goes. Pure, so it's tested without the network."""
    out = dict(player)
    for key in (player.get("profile_from") or {}):
        if key == "teams" and bound:
            continue
        out.pop(key, None)
        if key == "summary":
            out.pop("about", None)
    given: dict[str, str] = {}
    if found is not None:
        facts = {"born": found.born, "nationality": found.nationality, "role": found.role, "honours": found.honours, "teams": None if bound else found.teams}
        for key, value in facts.items():
            if value:
                out[key], given[key] = value, "wikidata"
        if found.summary:
            out["summary"], out["about"], given["summary"] = found.summary, {**WIKIPEDIA, "url": found.article}, "wikipedia"
    out["profile_from"] = given
    return out


def known_name(name: str, label: str | None) -> str:
    """The name a player goes by: Wikidata's label (wiki.Profile.name) where it's a shorter form of the one their source
    gave them, every word of it theirs in fewer words ("Jasprit Bumrah" for ESPNcricinfo's "Jasprit Jasbirsingh
    Bumrah", "Rohit Sharma" for "Rohit Gurunath Sharma"), and not a surname alone; else the source's. A label in other
    words (another spelling, a nickname, initials) isn't theirs to take."""
    short, full = naming.words(label or ""), naming.words(name)
    if 1 < len(short) < len(full) and all(w in full for w in short) and naming.names_match(label or "", name):
        return label or name
    return name


def keep_profile(previous: dict[str, Any], player: dict[str, Any]) -> dict[str, Any]:
    """An agent report's player section, with what Wikidata and Wikipedia gave the page before kept over it."""
    theirs = previous.get("profile_from") or {}
    kept = {key: previous[key] for key in (*theirs, *(["about"] if "summary" in theirs else [])) if key in previous}
    return {**player, **kept, "profile_from": theirs}


def _has_profile(found: wiki.Profile | None) -> bool:
    return found is not None and any((found.summary, found.born, found.nationality, found.role, found.teams, found.honours))


def apply_profile(player_id: int, found: wiki.Profile | None, now: datetime) -> dict[str, Any] | None:
    """Put a profile read from Wikidata and Wikipedia on the player and their card, and publish it. A first
    build with something to show is ready with it: the profile is a page, the research agent's part can follow.
    A player no live source has gets one only with research (config.RESEARCH). Returns the card, or None if
    the player is gone."""
    from .config import AGENT_LIVE, RESEARCH

    with session() as db:
        player = db.get(Player, player_id)
        card_row = db.get(PlayerCard, player_id)
        if player is None or card_row is None:
            return None
        bound = structured.binding(db, player_id) is not None
        card = dict(card_row.card or placeholder_card(player))
        before = (card.get("player") or {}).get("profile_from") or {}
        merged = merge_profile(card.get("player") or {}, found, bound)
        given = merged["profile_from"]
        if found is not None and (name := known_name(player.name, found.name)) != player.name:
            log.info("profile %s: %r goes by %r, as Wikidata has them", player_id, player.name, name)
            player.name = merged["name"] = name
        if "nationality" in given:
            player.nationality = merged["nationality"]
        if "role" in given:
            player.role = merged["role"]
        if "teams" in given:
            player.teams = merged["teams"]
        elif "teams" in before and not bound:  # Wikidata's, and it lists none now: they're nobody's player
            player.teams = merged["teams"] = []
        elif "teams" not in merged:
            merged["teams"] = list(player.teams or [])
        version = (card_row.version or 0) + 1
        card |= {"player": merged, "version": version, "freshness": {**(card.get("freshness") or {}), "profile": now.isoformat()}}
        if card_row.status != "ready" and _has_profile(found) and (bound or RESEARCH):
            card |= {"status": "ready"}
            card.pop("error", None)
            if not bound:
                if not AGENT_LIVE:
                    card["live"] = {"is_live": False, "player_stats": [], "available": False}
                # The agent's games and news follow: the page says they're coming, and why not if it fails (mark_failed).
                card["pending"] = ["news"]
            card_row.status, card_row.error = "ready", None
        card_row.card, card_row.version, card_row.freshness = card, version, card["freshness"]
    bus.store_card(player_id, card)
    return card


def refresh_profile(player_id: int, ttl: float = wiki.MAX_AGE) -> None:
    """Read the player's profile from Wikidata and Wikipedia (copies younger than ttl seconds will do) and put
    it on their page. A player with no Wikidata person has none (and loses one they had: their page says when
    that was found, so the scheduler doesn't ask every minute); one Wikimedia didn't answer for keeps what
    their page has, and is asked about again after PROFILE_RETRY."""
    with session() as db:
        qid = player_qid(db, player_id)
        sport = db.scalar(select(Player.sport).where(Player.id == player_id))
    try:
        found = wiki.read(qid, sport, ttl) if qid else None
    except wiki.Unavailable as exc:
        log.warning("profile %s (%s): %s", player_id, qid, exc)
        bus.cooldown(f"profileretry:{player_id}", PROFILE_RETRY)
        return
    apply_profile(player_id, found, _now())


def reread_profile(player_id: int) -> None:
    """The scheduler's monthly read of a player's profile (due_work): the month's copy is too old."""
    lock = f"profile:{player_id}"
    token = bus.try_lock(lock, 300)
    if not token:
        return
    try:
        refresh_profile(player_id, wiki.REREAD)
    finally:
        bus.release(lock, token)


def profile_due(card: PlayerCard, has_qid: bool, now: datetime) -> bool:
    """Whether a ready page's profile should be read again: never read, or read a month ago."""
    if card.status != "ready" or not has_qid:
        return False
    read_at = _parse_time((card.freshness or {}).get("profile"))
    stale = read_at is None or read_at < now - timedelta(seconds=wiki.MAX_AGE)
    return stale and not bus.is_locked(f"profile:{card.player_id}") and not bus.is_locked(f"profileretry:{card.player_id}")


# How often the research agent writes a background again (a player with a live source and no article): a
# biography changes about as often as the profile beside it (Bharath, 21 Oct).
BACKGROUND_EVERY = wiki.MAX_AGE


def _has_about(card: dict[str, Any] | None) -> bool:
    return bool(((card or {}).get("player") or {}).get("about"))


def needs_agent(db: Session, player_id: int, card: dict[str, Any] | None) -> bool:
    """Whether the research agent has a part on this page: the background of a player with a live source
    and no English Wikipedia article, or, with research (config.RESEARCH), the whole page of a player no
    live source has. What Wikidata and Wikipedia gave stays over what it writes (keep_profile)."""
    from .config import RESEARCH

    if structured.binding(db, player_id) is None:
        return RESEARCH
    return not _has_about(card)


def background_hour_key(now: datetime) -> str:
    """The count of backgrounds the research agent wrote in this UTC hour, across all players."""
    return f"research-background-hour:{now:%Y-%m-%dT%H}"


def take_background(now: datetime | None = None) -> bool:
    """Count one background against the hour's allowance (config.BACKGROUNDS_PER_HOUR), or, past it, refuse it,
    counted nowhere. Whoever made the player (a fan's pick, a campaign, a script), it's the same model budget."""
    from .config import BACKGROUNDS_PER_HOUR

    redis, key = bus.sync_redis(), background_hour_key(now or _now())
    used = cast(int, redis.incr(key))
    redis.expire(key, 2 * 3600)
    if used <= BACKGROUNDS_PER_HOUR:
        return True
    redis.decr(key)
    return False


def agent_due(player_id: int, card: dict[str, Any], now: datetime | None = None) -> bool:
    """Whether a fan opening this page should send the research agent: it's on (config.AGENT), the page has a
    part it writes (needs_agent), of a sport this build runs and a player someone follows, ready, with no
    build running and none backing off after a failure, and its words are old: a background over
    BACKGROUND_EVERY, a page no source has over CARD_MAX_AGE. Only a page fans open is researched again,
    never on a clock."""
    from .config import AGENT, CARD_MAX_AGE

    now = now or _now()
    if not AGENT or card.get("status") != "ready" or (_has_about(card) and card.get("provenance")):
        return False
    built = _parse_time(card.get("built_at"))
    if built is not None and built > now - timedelta(seconds=min(CARD_MAX_AGE, BACKGROUND_EVERY)):
        return False
    if bus.is_locked(f"build:{player_id}") or _backing_off(player_id, now):
        return False
    with session() as db:
        sport = db.scalar(select(Player.sport).where(Player.id == player_id))
        followed = db.scalar(select(Follow.id).where(Follow.player_id == player_id).limit(1)) is not None
        every = BACKGROUND_EVERY if structured.binding(db, player_id) is not None else CARD_MAX_AGE
        fresh = built is not None and built > now - timedelta(seconds=every)
        return followed and not fresh and sports.on(sport) and needs_agent(db, player_id, card)


# ---------------------------------------------------------------- jobs (run in a worker thread)


def build_card(player_id: int) -> None:
    """Build or rebuild one player's page and publish it: from a live source a typed name is found in, then
    the profile from Wikidata and Wikipedia, then the research agent, for a page neither has anything for
    (needs_agent). Progress goes to the player's channel."""
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
            # A name one source recognizes gets its page from that source in seconds (no model needed). Anything
            # else waits for the agent to say who it is.
            bus.publish(player_id, {"type": "progress", "player_id": player_id, "message": "Looking the name up in live sports sources…"})
            found = structured.identify(name)
            if found == structured.NOT_FOUND:
                bus.sync_redis().set(not_found_key(player_id), "1", ex=NOT_FOUND_FOR)
                mark_failed(player_id, NOT_FOLLOWED, "not_found")
                return
            if found:
                landed = _start_from_source(player_id, *found)
                if landed is None or landed != player_id:
                    return  # the athlete was already followed under another name: that page serves this fan
                name = found[1].name
        try:
            refresh_profile(player_id)
        except Exception:  # a profile that can't be read never stops the rest of the page
            log.exception("build %s: the profile couldn't be read", player_id)
        with session() as db:
            card_row = db.get(PlayerCard, player_id)
            wanted = card_row is not None and needs_agent(db, player_id, card_row.card)
        if not (AGENT and wanted):
            _without_agent(player_id)
            return
        with session() as db:
            background_only = structured.binding(db, player_id) is not None  # a live source has the rest
        if background_only and not take_background():
            # The hour's backgrounds are written: the page is whole from its source without one, and a fan opening
            # it in a later hour sends the agent (agent_due).
            log.info("build %s: the hour's backgrounds are written; this one waits for a fan to open the page", player_id)
            _without_agent(player_id)
            return
        for event in follow_player(llm(), name, structured.describe(player_id), background_only=background_only):
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
                card = {**placeholder_card(player), "status": "ready", "version": version, "pending": ["profile"]}
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
    """A build that doesn't send the research agent, because it's off (config.AGENT) or the page has no part
    for it (needs_agent: its About from Wikipedia and its games from a live source, or no live source and no
    research), once a typed name has been looked up in the sources
    and the profile read (build_card). A page that exists stays as it is: one from a source that was waiting
    for its profile stops waiting. A first build with nothing to show can't be made."""
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
    builds: list[int] = field(default_factory=list)  # players whose first page nobody is building (build_card)
    lives: list[int] = field(default_factory=list)  # unbound players who need an agent live check
    refreshes: list[int] = field(default_factory=list)  # bound players whose fixtures and stats are stale
    polls: list[int] = field(default_factory=list)  # events with a live binding that are armed or live
    lanes: list[str] = field(default_factory=list)  # sources whose armed and live events are polled together
    teams: list[int] = field(default_factory=list)  # followed teams whose games are stale (teams.refresh)
    profiles: list[int] = field(default_factory=list)  # players whose profile was read a month ago (reread_profile)


def rebuild_due(card: PlayerCard, now: datetime) -> bool:
    """Whether the scheduler should build this card now: a first build nobody is running, or a source's page
    still waiting for its profile (the build queued with it was lost).

    The research agent's pages are researched again when a fan opens them (agent_due), not on a clock, and
    profiles are read again monthly (profile_due). A failed card is left alone: it waits for a fan to retry.
    A card whose build failed waits for its back_off, and none is queued again while its build is still
    running, so nothing loops.
    """
    wanted = card.status == "building" or (card.status == "ready" and bool((card.card or {}).get("pending")))
    return wanted and not bus.is_locked(f"build:{card.player_id}") and not _backing_off(card.player_id, now)


def to_arm(games: Iterable[tuple[int, str | None, datetime | None, bool]], now: datetime) -> list[int]:
    """Of scheduled games (id, sport, start, whether a source polls it), those to arm now: each within its
    sport's lead, of a sport that's on (one turned off in the admin panel isn't polled, so none of its games
    is armed). A game no source polls gets agent live checks once armed, which can't see a team sheet, so it
    keeps the usual lead."""
    return [game for game, sport, start, polled in games
            if start is not None and sports.on(sport) and start <= now + structured.arm_before(sport if polled else None)]


def due_work(now: datetime | None = None) -> Due:
    """What the scheduler should enqueue now. Only followed players get work, games fans follow by
    themselves (matches.py), and the teams fans follow (teams.py): a followed game is polled as a followed
    player's is, and its follow lapses once it's over; a followed team's games are read again as a followed
    player's are, and each is polled as a followed game.

    Events move scheduled -> armed at T-30 min, or earlier for a sport whose team sheets come out sooner
    (football's, at T-75 min: structured.arm_before). An armed or live event with a live binding gets one
    poll job, shared by every player in it, or, from a source that fetches many events in one
    request, a place in that source's lane; players without a binding get agent live checks.
    Events nobody closed are closed: after 12 hours from the agent, after 6 days from a source
    (a Test match runs five), and a card still showing one live stops; those of a sport turned off in the
    admin panel are put off till it's on again (structured.put_off), and their cards and phones' live cards
    stop showing them live. A bound player whose refreshes keep failing waits longer each time
    (structured.retry_at), and none is refreshed while their source is down to what its live games need
    (structured.spare).
    """
    from . import teams  # it reads search, which reads this
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
        # A source's games six days after their start; one with no start time of its own (armed as next:
        # structured.reschedule), six days after it was made.
        closed = db.scalars(
            update(Event)
            .where(Event.status.in_(("armed", "live")), Event.live_binding.is_not(None),
                   or_(Event.start_utc < now - timedelta(days=6), and_(Event.start_utc.is_(None), Event.created_at < now - timedelta(days=6))))
            .values(status="final")
            .returning(Event.id)
        ).all()
        # Games of a sport turned off in the admin panel aren't polled: put off, not closed, so they come back with it.
        put_off = structured.put_off(db)
        # Closed without a final poll (the source stopped returning the game), or put off: its cards stop saying LIVE.
        unstuck = structured.close_live(db, [*closed, *put_off])
        # Games followed by themselves that are over: nothing more of them is sent. The rest settle on the event
        # of a player their fan follows, where the game's here as several.
        matches.lapse(db, now)
        matches.settle(db)
        # Questions whose game's start has come close, and those its game never answered (predictions.py).
        predictions.sweep(db, now)
        # Followed teams' games, read again as often as a followed player's (teams.py): their events, linked to
        # them, are polled below as games followed by themselves are.
        due.teams = teams.due(db, now)
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
        with_qid = set(db.scalars(select(PlayerIdentity.player_id).where(PlayerIdentity.system == "wikidata", PlayerIdentity.player_id.in_(followed))).all())
        with_qid |= set(db.scalars(select(Player.id).where(Player.wikidata_qid.is_not(None), Player.id.in_(followed))).all())
        for card in db.scalars(select(PlayerCard).where(PlayerCard.player_id.in_(followed))):
            pid = card.player_id
            if not sports.on(sport_of.get(pid)):
                continue  # a sport this build doesn't run: the page stays as it is
            if rebuild_due(card, now):
                due.builds.append(pid)
            if card.status != "ready":
                continue
            if len(due.profiles) < PROFILES_PER_TICK and pid not in due.builds and profile_due(card, pid in with_qid, now):
                due.profiles.append(pid)
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
        # And games a pick waits on (predictions.py), as games followed by themselves.
        watched = or_(Event.id.in_(matches.watched()), Event.id.in_(predictions.watched()))
        itself = db.execute(select(Event.id, Event.live_binding, Event.sport).where(Event.status.in_(("armed", "live")), Event.live_binding.is_not(None), watched)).all()
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
    livecards.ended(put_off)  # and their live cards on Android phones come down
    return due


def unfollow(db: Session, fan_id: str, player_id: int) -> None:
    db.execute(delete(Follow).where(Follow.fan_id == fan_id, Follow.player_id == player_id))
