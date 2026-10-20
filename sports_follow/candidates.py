"""The feed's fetch (L0, BHA-298): videos about followed players, from the channels each sport trusts, kept as
candidates for the later stages to judge and rank (feed_candidates). Run by the curator (curator.py), never by
a request: the feed reads what's here and never waits on it.

Every half hour (poll), for each running video source (adapters.videos): the latest uploads of each channel a
running sport lists (Sport.video_channels), and of those, each one whose title or the opening of its description
names a followed player of that sport (named) is a candidate for that player: not a name in the link lists further
down (related videos, podcasts, a channel's other series), which are about someone else. What the source says of the new ones (one request
for every 50) then decides, with the hard filters alone, which are kept (refused): a video that may be embedded,
is public, plays in FEED_COUNTRY, isn't a live stream and runs 1 to 20 minutes. Whether it's any good is the
judge's to say, later. A video refused is remembered for a week, so it isn't asked about again while it's still
among its channel's latest.

A day after a video is kept, its views and likes are read again (second_reads), so its pace can be told from the
two reads; the curator reads them again every few hours while it's under two days old (fresh_reads), for the
ranking (rank.py). Every stored video is read again once a week (check), and dropped once it's gone: removed, made
private, no longer embeddable, or no longer playing in FEED_COUNTRY.

A candidate is one row per player and video, so it goes with the player (ON DELETE CASCADE).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from sqlalchemy import delete, exists, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from . import bus, db, feedstats, metrics, naming, shutdown, sports
from .adapters import NEWS, AdapterError, Upload, VideoFacts, VideoSource, budget, videos
from .config import FEED_COUNTRY
from .models import Athlete, FeedCandidate, Follow, Player, PlayerAlias, PlayerIdentity

log = logging.getLogger("sports_follow.candidates")

MIN_SECONDS, MAX_SECONDS = 60, 20 * 60  # how long a kept video runs
SECOND_READ_AFTER = timedelta(days=1)  # a kept video's views and likes are read again this long after
# While a kept video is this young, its numbers are read again this often (fresh_reads): how fast it's watched in
# its first two days is most of what ranks it (rank.py), and a first read minutes after it's out says little.
FRESH_FOR, FRESH_EVERY = timedelta(hours=48), timedelta(hours=6)
RECHECK_AFTER = timedelta(days=7)  # every stored video is read again this often, and dropped once gone
# Seconds a refused video isn't asked about again: a week for what doesn't change, an hour for what does (a Premiere
# not aired yet is "live", an upload still processing "private", one not yet visible "gone").
REFUSED_FOR = {"length": 7 * 86400, "not embeddable": 7 * 86400, "blocked": 7 * 86400}
REFUSED_AWHILE = 3600
STALE_AFTER = timedelta(days=30)  # a row no check has read for this long goes, whatever its source's state
READS_PER_RUN = 2000  # the most stored videos one run reads again: 40 requests
SHORT_NAME = 4  # a one-word name (a surname, a nickname) shorter than this never names anyone by itself
_POSSESSIVE = re.compile(r"['’`]s\b", re.I)  # "Kohli's": the name, not "kohlis" (naming.words drops apostrophes)


# ---------------------------------------------------------------- who's followed, by which names


@dataclass
class Followed:
    """A followed player as the feed looks for them: their sport (its key), every name they go by, and the sides
    they turn out for (their teams, their country), which tell a namesake apart in a story (news.py)."""

    player_id: int
    sport: str
    name: str
    aliases: list[str] = field(default_factory=list)
    teams: list[str] = field(default_factory=list)


def followed(session: Session) -> list[Followed]:
    """Every player at least one fan follows, with their sport and the names they go by: their own, what fans
    typed to find them, and the registry's other names for them."""
    rows = session.execute(select(Player.id, Player.name, Player.sport, Player.wikidata_qid, Player.teams, Player.nationality)
                           .where(exists().where(Follow.player_id == Player.id))).all()  # fmt: skip
    out = {pid: Followed(pid, sports.family(sport), name, teams=_sides(teams, country)) for pid, name, sport, _, teams, country in rows}
    if not out:
        return []
    for pid, alias in session.execute(select(PlayerAlias.player_id, PlayerAlias.alias).where(PlayerAlias.player_id.in_(out))):
        out[pid].aliases.append(alias.replace("-", " "))
    qids: dict[str, set[int]] = {}
    for pid, _, _, qid, _, _ in rows:
        if qid:
            qids.setdefault(qid, set()).add(pid)
    for pid, qid in session.execute(select(PlayerIdentity.player_id, PlayerIdentity.external_id).where(PlayerIdentity.player_id.in_(out), PlayerIdentity.system == "wikidata")):
        qids.setdefault(qid, set()).add(pid)
    if qids:
        for qid, sport, name, aliases in session.execute(select(Athlete.qid, Athlete.sport, Athlete.name, Athlete.aliases).where(Athlete.qid.in_(qids))):
            for pid in qids[qid]:
                if out[pid].sport in ("", sports.family(sport)) or not sports.get(out[pid].sport):
                    out[pid].aliases += [name, *(a for a in aliases or [] if isinstance(a, str))]
    return list(out.values())


def _sides(teams: Any, country: str | None) -> list[str]:
    """A player's teams (as Player.teams keeps them: names, or {"name": …}) and their country, as names."""
    names = [t if isinstance(t, str) else str((t or {}).get("name") or "") for t in (teams if isinstance(teams, list) else [])]
    return [n for n in dict.fromkeys([*names, country or ""]) if n.strip()]


def phrases(players: list[Followed]) -> dict[int, set[tuple[str, ...]]]:
    """Each player's names as the word runs a video's text must hold to name them, judged among these players
    (those a channel's videos may be about):

    - every name of two words or more, in each of its plain spellings, and run together as a hashtag writes it
      ("virat kohli", "#viratkohli");
    - their own name when it's one word (a handle, "TenZ"; a mononym, "Neymar") of three letters or more, that no
      other of these players has in any of their names;
    - a one-word nickname, and the last word of their name ("Bumrah"), when it's at least SHORT_NAME letters and
      no other of these players has that word in any of their names (two Sharmas followed: "Sharma" names neither).
    """
    owners: dict[str, set[int]] = {}
    for p in players:
        for name in (p.name, *p.aliases):
            for spelling in naming.spellings(name):
                for word in spelling:
                    owners.setdefault(word, set()).add(p.player_id)

    def theirs(word: str, pid: int) -> bool:
        return len(word) >= SHORT_NAME and owners.get(word) == {pid}

    out: dict[int, set[tuple[str, ...]]] = {}
    for p in players:
        found: set[tuple[str, ...]] = set()
        for name in (p.name, *p.aliases):
            for spelling in naming.spellings(name):
                if len(spelling) >= 2:
                    found.add(tuple(spelling))
                    if len(spelling) <= 3:
                        found.add(("".join(spelling),))
                elif len(spelling) == 1 and ((name == p.name and len(spelling[0]) >= 3 and owners.get(spelling[0]) == {p.player_id}) or theirs(spelling[0], p.player_id)):
                    found.add(tuple(spelling))
        for spelling in naming.spellings(p.name):
            if len(spelling) >= 2 and theirs(spelling[-1], p.player_id):
                found.add((spelling[-1],))
        out[p.player_id] = found
    return out


def _holds(words: list[str], phrase: tuple[str, ...]) -> bool:
    n = len(phrase)
    return any(tuple(words[i:i + n]) == phrase for i, w in enumerate(words) if w == phrase[0])


Texts = list[tuple[str, list[str], set[str]]]


def texts(upload: Upload) -> Texts:
    """A video's title and its description's opening (naming.lead) as words, each once: (where, its words, the set of them)."""
    out = []
    for where, text in (("title", upload.title), ("description", naming.lead(upload.description))):
        words = naming.words(_POSSESSIVE.sub("", text))
        out.append((where, words, set(words)))
    return out


def _longest_first(names: Iterable[tuple[str, ...]]) -> list[tuple[str, ...]]:
    return sorted(names, key=lambda p: (-len(p), p))


def named(upload: Upload, names: Iterable[tuple[str, ...]], read: Texts | None = None) -> tuple[str, str] | None:
    """The name (its words) a video's title, else its description's opening, names a player by, and where: ("virat
    kohli", "title"); None if neither does. The longest name found wins. read: the upload's texts(), when the
    caller already has them."""
    ordered = _longest_first(names)
    for where, words, present in read if read is not None else texts(upload):
        for phrase in ordered:
            if phrase[0] in present and _holds(words, phrase):
                return " ".join(phrase), where
    return None


# ---------------------------------------------------------------- channels, and the hard filters


def channels(source: VideoSource) -> dict[str, set[str]]:
    """The channels of the source's shape that the running sports list, each with the sports whose players its
    videos are looked at for: those sports' keys, and their groups' (an esports pro whose game isn't known)."""
    out: dict[str, set[str]] = {}
    for sport in sports.ENABLED:
        for channel in sport.video_channels:
            if source.channel_pattern.fullmatch(channel):
                out.setdefault(channel, set()).update({sport.key, *([sport.group] if sport.group else [])})
    return out


def refused(facts: VideoFacts | None, country: str = FEED_COUNTRY) -> str | None:
    """Why the feed doesn't keep a video (a hard filter), or None to keep it."""
    if (why := gone(facts, country)) or facts is None:
        return why or "gone"
    if facts.live:
        return "live"
    if facts.seconds is None or not MIN_SECONDS <= facts.seconds <= MAX_SECONDS:
        return "length"
    return None


def gone(facts: VideoFacts | None, country: str = FEED_COUNTRY) -> str | None:
    """Why a stored video can no longer be shown (the weekly check), or None while it can."""
    if facts is None:
        return "gone"
    if not facts.public:
        return "private"
    if not facts.embeddable:
        return "not embeddable"
    if not facts.plays_in(country):
        return f"blocked in {country}"
    return None


def _read(facts: VideoFacts, at: datetime) -> dict[str, Any]:
    return {"at": at.isoformat(), "views": facts.views, "likes": facts.likes}


def features(facts: VideoFacts, at: datetime) -> dict[str, Any]:
    """A new candidate's numbers: its views, likes and length, and this first read of them."""
    return {"views": facts.views, "likes": facts.likes, "seconds": facts.seconds, "reads": [_read(facts, at)]}


def read_again(kept: dict[str, Any], facts: VideoFacts, at: datetime) -> dict[str, Any]:
    """A stored candidate's numbers after reading it again: the latest views and likes, and three reads at most,
    the first, the one before this and this one: its pace now is between the last two (rank.growth), so an
    old video still being watched shows as one."""
    before = list(kept.get("reads") or [])
    reads = before[:1] + before[1:][-1:] + [_read(facts, at)]
    return {**kept, "views": facts.views, "likes": facts.likes, "reads": reads}


def candidate(upload: Upload, name: str, where: str, sport: str, facts: VideoFacts | None) -> dict[str, Any]:
    """What a candidate row keeps of what was fetched."""
    return {
        "title": upload.title,
        "description": upload.description,
        "channel": upload.channel,
        "channel_id": upload.channel_id,
        "published": upload.published.isoformat() if upload.published else None,
        "thumbnail": upload.thumbnail,
        "named": name,
        "where": where,
        "sport": sport,
        "details": {k: v for k, v in (facts.raw if facts else {}).items() if k != "statistics"},
        "paid": facts.paid if facts else False,
    }


# ---------------------------------------------------------------- videos refused, remembered a while


def _refused_key(source: VideoSource, video_id: str) -> str:
    return f"feed:refused:{source.system}:{video_id}"


def _was_refused(source: VideoSource, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    try:
        seen = cast(list[Any], bus.sync_redis().mget([_refused_key(source, v) for v in ids]))
    except Exception:  # noqa: BLE001 — without Redis they're asked about again: a request per 50
        return set()
    return {v for v, s in zip(ids, seen) if s}


def _remember_refused(source: VideoSource, why: dict[str, str]) -> None:
    if not why:
        return
    try:
        pipe = bus.sync_redis().pipeline()
        for video_id, reason in why.items():
            pipe.set(_refused_key(source, video_id), reason, ex=REFUSED_FOR.get(reason.split(" in ")[0], REFUSED_AWHILE))
        pipe.execute()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- the poll


@dataclass
class Found:
    """One upload that names one followed player."""

    player_id: int
    sport: str
    upload: Upload
    name: str
    where: str
    moment_id: int | None = None  # the moment it was searched for (searches.py); the ranking works it out for the rest
    sweep: int | None = None  # the sweep that found it (sweeps.py: a FeedSweep's id)


def match(uploads: list[Upload], players: list[Followed], names: dict[int, set[tuple[str, ...]]]) -> list[Found]:
    """Each upload, once for each of these players it names. Each upload's text is read into words once."""
    ordered = {p.player_id: _longest_first(names.get(p.player_id, ())) for p in players}
    out = []
    for upload in uploads:
        read = texts(upload)
        for p in players:
            hit = named(upload, ordered[p.player_id], read)
            if hit:
                out.append(Found(p.player_id, p.sport, upload, *hit))
    return out


def keep(source: VideoSource, found: list[Found], now: datetime, stream: str = "channels") -> dict[str, int]:
    """Store what's new of what was found: a video already kept for someone else is kept for this player too,
    as it is; the rest are asked about (a request per 50, with no transaction open meanwhile) and kept if no
    hard filter refuses them. stream: where it was found, for the funnel's counts (feedstats.keep_counted)."""
    counts = {"kept": 0, "refused": 0}
    ids = sorted({f.upload.video_id for f in found})
    if not ids:
        return counts
    stored: dict[str, dict[str, Any]] = {}
    have: set[tuple[int, str]] = set()
    with db.session() as session:
        rows = session.execute(select(FeedCandidate.player_id, FeedCandidate.external_id, FeedCandidate.url, FeedCandidate.candidate, FeedCandidate.features)
                               .where(FeedCandidate.source == source.system, FeedCandidate.external_id.in_(ids)))
        for pid, vid, url, kept, numbers in rows:
            stored.setdefault(vid, {"url": url, "candidate": kept or {}, "features": numbers or {}})
            have.add((pid, vid))
    new = [f for f in found if (f.player_id, f.upload.video_id) not in have]
    if not new:
        feedstats.keep_counted(stream, found, have)
        return counts
    skip = _was_refused(source, sorted({f.upload.video_id for f in new} - stored.keys()))
    ask = sorted({f.upload.video_id for f in new} - stored.keys() - skip)
    facts = source.facts(ask) if ask else {}
    why = {v: r for v in ask if (r := refused(facts.get(v)))}
    _remember_refused(source, why)
    counts["refused"] = len(why)
    stored_for: list[str] = []  # a sport for each row stored
    with db.session() as session:
        for f in new:
            vid = f.upload.video_id
            if vid in skip or vid in why:
                continue
            if vid in stored:
                same = stored[vid]
                values = {"url": same["url"], "features": same["features"],
                          "candidate": {**candidate(f.upload, f.name, f.where, f.sport, None), "details": same["candidate"].get("details") or {},
                                        "paid": same["candidate"].get("paid") is True}}
            else:
                values = {"url": f.upload.url, "candidate": candidate(f.upload, f.name, f.where, f.sport, facts[vid]), "features": features(facts[vid], now)}
            if f.moment_id is not None:
                values["candidate"]["searched_for"] = f.moment_id  # the ranking keeps it while it stands (rank.rescore)
            if f.sweep is not None:
                values["candidate"] |= {"swept": f.sweep, "stream": stream}  # what it served (sweeps.report), and its stream (rank.stream)
            # Its own savepoint: a player removed meanwhile fails only their row.
            try:
                with session.begin_nested():
                    added = session.scalar(insert(FeedCandidate).values(player_id=f.player_id, source=source.system, external_id=vid, fetched_at=now, checked_at=now,
                                                                        moment_id=f.moment_id, **values)
                                           .on_conflict_do_nothing(index_elements=["player_id", "external_id"]).returning(FeedCandidate.id))
                    counts["kept"] += added is not None
                    stored_for += [f.sport] if added is not None else []
            except Exception as exc:  # noqa: BLE001
                log.info("feed: %s %s for player %s not kept: %s", source.system, vid, f.player_id, type(exc).__name__)
    feedstats.keep_counted(stream, found, have, skip, why, facts, stored_for)
    return counts


def poll(now: datetime | None = None) -> dict[str, int]:
    """Read every running video source's channels once, and keep what's new; then read again what was kept a
    day ago (second_reads). Returns what it did."""
    now = now or datetime.now(timezone.utc)
    totals = {"channels": 0, "unread": 0, "found": 0, "kept": 0, "refused": 0, "dropped": 0, "read_again": 0}
    with db.session() as session:
        players = followed(session)
    for source in videos():
        listed = channels(source)
        if not listed:
            continue
        uploads: dict[str, list[Upload]] = {}
        for channel in listed:
            if shutdown.stopping.is_set():
                return totals
            try:
                uploads[channel] = source.uploads(channel)
            except budget.RateLimited as exc:
                log.warning("feed: %s: %s; its other channels wait for the next poll", source.system, exc)
                break
            except AdapterError as exc:
                totals["unread"] += 1
                log.info("feed: %s channel %s not read: %s", source.system, channel, exc)
        totals["channels"] += len(uploads)
        found: list[Found] = []
        by_sports: dict[frozenset[str], tuple[list[Followed], dict[int, set[tuple[str, ...]]]]] = {}
        for channel, theirs in uploads.items():
            keys = frozenset(listed[channel])
            if keys not in by_sports:
                group = [p for p in players if p.sport in keys]
                by_sports[keys] = (group, phrases(group))
            matched = match(theirs, *by_sports[keys])
            feedstats.read_channel(channel, keys, theirs, matched, now)
            found += matched
        totals["found"] += len(found)
        try:
            counts = keep(source, found, now)
        except (AdapterError, budget.RateLimited) as exc:
            log.warning("feed: %s: what's new not kept this time: %s", source.system, exc)
            counts = {}
        for k, v in counts.items():
            totals[k] += v
        again = second_reads(source, now)
        totals["read_again"] += again["read"]
        totals["dropped"] += again["dropped"]
    metrics.feed("kept", totals["kept"])
    metrics.feed("refused", totals["refused"])
    metrics.feed("dropped", totals["dropped"])
    return totals


# ---------------------------------------------------------------- reading stored videos again


def _again(source: VideoSource, ids: list[str], now: datetime) -> dict[str, int]:
    """Read these stored videos again, a request per 50: drop the ones gone, and give the rest their latest
    numbers. Stops early when the worker is stopping or the source's allowance is spent."""
    counts = {"read": 0, "dropped": 0}
    for i in range(0, len(ids), source.batch):
        if shutdown.stopping.is_set():
            break
        part = ids[i:i + source.batch]
        try:
            facts = source.facts(part, max_age=0)
        except (AdapterError, budget.RateLimited) as exc:
            log.warning("feed: %s: stored videos not read again: %s", source.system, exc)
            break
        with db.session() as session:
            dead = {v: why for v in part if (why := gone(facts.get(v)))}
            if dead:
                session.execute(delete(FeedCandidate).where(FeedCandidate.source == source.system, FeedCandidate.external_id.in_(dead)))
                log.info("feed: %s dropped %d stored video(s): %s", source.system, len(dead), ", ".join(f"{v} ({w})" for v, w in sorted(dead.items())))
            for row in session.scalars(select(FeedCandidate).where(FeedCandidate.source == source.system, FeedCandidate.external_id.in_([v for v in part if v not in dead]))):
                row.features = read_again(row.features or {}, facts[row.external_id], now)
                paid = facts[row.external_id].paid
                if (row.candidate or {}).get("paid") is not paid:
                    row.candidate = {**(row.candidate or {}), "paid": paid}
                    if paid:  # declared after it was judged: unserved until the judge says again (the rules: a promo)
                        row.verdict, row.judged_at, row.score = None, None, None
                row.checked_at = now
        counts["read"] += len(part) - len(dead)
        counts["dropped"] += len(dead)
    return counts


def second_reads(source: VideoSource, now: datetime) -> dict[str, int]:
    """Read again the videos kept SECOND_READ_AFTER ago that have been read only once."""
    with db.session() as session:
        ids = list(session.scalars(
            select(FeedCandidate.external_id).where(FeedCandidate.source == source.system, FeedCandidate.fetched_at <= now - SECOND_READ_AFTER,
                                                    FeedCandidate.checked_at <= FeedCandidate.fetched_at)
            .group_by(FeedCandidate.external_id).order_by(FeedCandidate.external_id).limit(READS_PER_RUN)))
    return _again(source, ids, now)


def fresh_reads(now: datetime | None = None) -> dict[str, int]:
    """Read again, for each running video source, the videos kept in the last FRESH_FOR that no read has seen
    for FRESH_EVERY: a request per 50, so a day of them costs a few units in all."""
    now = now or datetime.now(timezone.utc)
    totals = {"read": 0, "dropped": 0}
    for source in videos():
        with db.session() as session:
            ids = list(session.scalars(
                select(FeedCandidate.external_id).where(FeedCandidate.source == source.system, FeedCandidate.fetched_at > now - FRESH_FOR)
                .group_by(FeedCandidate.external_id).having(func.max(FeedCandidate.checked_at) <= now - FRESH_EVERY)
                .order_by(FeedCandidate.external_id).limit(READS_PER_RUN)))
        counts = _again(source, ids, now)
        totals["read"] += counts["read"]
        totals["dropped"] += counts["dropped"]
    metrics.feed("dropped", totals["dropped"])
    return totals


def check(now: datetime | None = None) -> dict[str, int]:
    """Read again every stored video not read for RECHECK_AFTER, for each running video source, and drop those
    gone (removed, private, no longer embeddable, or blocked in FEED_COUNTRY). Run daily, so each video is read
    once a week, and a run cut short is taken up the next day. A row no check has read for STALE_AFTER goes
    too, whatever its source's state."""
    now = now or datetime.now(timezone.utc)
    totals = {"read": 0, "dropped": 0}
    # Rows no check could read for a month (their source turned off, or without its key) go: what's shown must
    # have been confirmed lately, and YouTube's terms keep its data 30 days at most without reading it again.
    # A publisher's story (news.py) is never read again: the weekly prune drops it by age instead.
    with db.session() as session:
        stale = session.execute(delete(FeedCandidate).where(FeedCandidate.checked_at < now - STALE_AFTER, FeedCandidate.source.not_in(list(NEWS)))
                                .returning(FeedCandidate.id)).all()  # fmt: skip
    if stale:
        log.info("feed: dropped %d candidate(s) no check has read for %d days", len(stale), STALE_AFTER.days)
        totals["dropped"] += len(stale)
    for source in videos():
        with db.session() as session:
            ids = _stale(session, source, now)
        counts = _again(source, ids, now)
        totals["read"] += counts["read"]
        totals["dropped"] += counts["dropped"]
    metrics.feed("dropped", totals["dropped"])
    return totals


def _stale(session: Session, source: VideoSource, now: datetime) -> list[str]:
    """The source's stored videos not read for RECHECK_AFTER, least lately read first."""
    oldest = func.min(FeedCandidate.checked_at)
    return list(session.scalars(
        select(FeedCandidate.external_id).where(FeedCandidate.source == source.system).group_by(FeedCandidate.external_id)
        .having(oldest <= now - RECHECK_AFTER).order_by(oldest).limit(READS_PER_RUN)))
