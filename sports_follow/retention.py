"""Retention: what nothing reads any more goes, once a day (the worker's prune job, 02:15 UTC).

- **A finished game's timeline.** Each poll that changes something appends an event_state row, and a
  player_line row for each player whose numbers moved. Once a game is final and nothing new has come
  for KEEP_TIMELINE_DAYS, only the rows still read stay: its latest state (what a poll compares with,
  should the game be polled again) and its latest final state (results.detail's score), and each
  player's latest line (results.detail, form, and a poll's baseline).
- **Moments and the push log** (moment, notification) older than KEEP_MOMENTS_DAYS. The activity list
  shows 30 days, the noise rules read the last hour and the games in a batch (alerts.plan), and a tap
  on a push older than that only goes uncounted (the opened route answers ok: false). A moment's key
  stops it being told twice only while its game can still be polled, so the moments of a game not yet
  final stay.
- **Lapsed match follows** (match_follow, matches.py): a game followed by itself, a month (ACTIVITY_DAYS)
  after its follow lapsed: the activity list reads a follow's dates to list the game's moments.
- **Who was out** (sidelined): a spell no read of its source has listed for sidelined.KEEP_DAYS, and the
  Wire's facts about it with it. One still listed stays, over or not: the next read compares with it.
- **Hot copies of cards** (card:<id> in Redis) of players nobody follows get a day to live:
  server.read_card rebuilds a missing one from Postgres.
- **Clips' unfinished uploads** (clips.sweep): a clip whose upload never reached done, once its upload links have
  run out, has its files deleted and is kept as failed; and a file a link still running put back after its clip
  failed or was taken down goes too. Only with a clips bucket.

Rows go BATCH at a time, each batch in a transaction of its own, so no lock is held for long. One run
at a time (a lock).

    .venv/bin/python -m sports_follow.retention            # prune now
    .venv/bin/python -m sports_follow.retention --count    # what a run would remove; writes nothing
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from itertools import islice
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from . import bus, clips, clipstore, logs, sidelined
from .config import KEEP_MOMENTS_DAYS, KEEP_TIMELINE_DAYS
from .db import session
from .models import Follow

log = logging.getLogger("sports_follow.retention")

LOCK = "retention"
BATCH = 5000  # rows deleted a transaction
GAMES = 200  # games (or player-and-game pairs) looked at a transaction
ACTIVITY_DAYS = 31  # the activity list's 30 (server.ACTIVITY_DAYS) and a day: moments are never kept for less
CARD_TTL = 24 * 3600

# Finished games with more than one state and none since the cutoff.
SETTLED_GAMES = """
    SELECT s.event_id FROM event_state s JOIN event e ON e.id = s.event_id
    WHERE e.status = 'final'
    GROUP BY s.event_id HAVING count(*) > 1 AND max(s.as_of) < :cutoff
    ORDER BY s.event_id"""
# Their states but the latest and the latest final one.
OLD_STATES = """
    SELECT id FROM (
        SELECT id, status = 'final' AS final,
               row_number() OVER (PARTITION BY event_id ORDER BY as_of DESC, id DESC) AS newest,
               row_number() OVER (PARTITION BY event_id, status = 'final' ORDER BY as_of DESC, id DESC) AS newest_of_kind
        FROM event_state WHERE event_id = ANY(:games)
    ) ranked WHERE newest > 1 AND NOT (final AND newest_of_kind = 1)"""
# Each player's finished games where they have more than one line and none since the cutoff.
SETTLED_LINES = """
    SELECT l.player_id, l.event_id FROM player_line l JOIN event e ON e.id = l.event_id
    WHERE e.status = 'final'
    GROUP BY l.player_id, l.event_id HAVING count(*) > 1 AND max(l.as_of) < :cutoff
    ORDER BY l.player_id, l.event_id"""
# Their lines but the latest.
OLD_LINES = """
    SELECT id FROM (
        SELECT l.id, row_number() OVER (PARTITION BY l.player_id, l.event_id ORDER BY l.as_of DESC, l.id DESC) AS newest
        FROM player_line l JOIN unnest(CAST(:players AS integer[]), CAST(:games AS integer[])) AS g(player_id, event_id)
          ON l.player_id = g.player_id AND l.event_id = g.event_id
    ) ranked WHERE newest > 1"""
OLD_MOMENTS = """
    SELECT m.id FROM moment m LEFT JOIN event e ON e.id = m.event_id
    WHERE m.created_at < :cutoff AND (e.id IS NULL OR e.status = 'final')"""
OLD_NOTIFICATIONS = "SELECT id FROM notification WHERE created_at < :cutoff"
OLD_SIDELINED = "SELECT id FROM sidelined WHERE seen_at < :cutoff"
OLD_MATCH_FOLLOWS = "SELECT id FROM match_follow WHERE until < :cutoff"


@contextmanager
def _db(count: bool) -> Iterator[Session]:
    """A session; read-only when only counting."""
    with session() as db:
        if count:
            db.execute(text("SET TRANSACTION READ ONLY"))
        yield db


def _cut(db: Session, table: str, victims: str, params: dict[str, Any], count: bool) -> int:
    """Delete the rows of `table` that the query `victims` picks, BATCH at a time; or count them."""
    if count:
        return db.scalar(text(f"SELECT count(*) FROM ({victims}) v"), params) or 0
    gone = 0
    while True:
        n = db.execute(text(f"DELETE FROM {table} WHERE id IN (SELECT id FROM ({victims}) v LIMIT :batch)"), {**params, "batch": BATCH}).rowcount
        db.commit()
        gone += n
        if n < BATCH:
            return gone


def _states(cutoff: datetime, count: bool) -> int:
    with _db(True) as db:
        games = list(db.scalars(text(SETTLED_GAMES), {"cutoff": cutoff}))
    gone = 0
    for i in range(0, len(games), GAMES):
        with _db(count) as db:
            gone += _cut(db, "event_state", OLD_STATES, {"games": games[i : i + GAMES]}, count)
    return gone


def _lines(cutoff: datetime, count: bool) -> int:
    with _db(True) as db:
        pairs = db.execute(text(SETTLED_LINES), {"cutoff": cutoff}).all()
    gone = 0
    for i in range(0, len(pairs), GAMES):
        chunk = pairs[i : i + GAMES]
        with _db(count) as db:
            gone += _cut(db, "player_line", OLD_LINES, {"players": [p for p, _ in chunk], "games": [e for _, e in chunk]}, count)
    return gone


def _old(table: str, victims: str, cutoff: datetime, count: bool) -> int:
    with _db(count) as db:
        return _cut(db, table, victims, {"cutoff": cutoff}, count)


def _cards(count: bool) -> int:
    """Give the hot copies of players nobody follows a day to live (a copy that has one keeps it);
    or count the ones that have none."""
    r = bus.sync_redis()
    keys = r.scan_iter(match="card:*", count=1000)
    done = 0
    while chunk := list(islice(keys, 1000)):
        ids = {int(k.split(":", 1)[1]): k for k in chunk if k.split(":", 1)[1].isdigit()}
        with _db(True) as db:
            followed = set(db.scalars(select(Follow.player_id).where(Follow.player_id.in_(list(ids))))) if ids else set()
        pipe = r.pipeline(transaction=False)
        for player_id, key in ids.items():
            if player_id in followed:
                continue
            if count:
                pipe.ttl(key)
            else:
                pipe.expire(key, CARD_TTL, nx=True)
        done += sum(1 for x in pipe.execute() if (x == -1 if count else x))
    return done


def _clip_uploads(now: datetime, count: bool) -> int:
    """Clear clips' unfinished uploads (clips.sweep); or count them. A bucket that fails is logged, and tried again
    on the next run."""
    if clipstore.STORE is None:
        return 0
    try:
        with _db(count) as db:
            return clips.sweep(db, now, count)
    except Exception:
        log.warning("retention: the clips bucket failed while clearing unfinished uploads; tried again next run", exc_info=True)
        return 0


def run(count: bool = False, now: datetime | None = None) -> dict[str, int]:
    """Prune everything once, or (count) only count what would go."""
    now = now or datetime.now(timezone.utc)
    timeline = now - timedelta(days=max(1, KEEP_TIMELINE_DAYS))
    old = now - timedelta(days=max(ACTIVITY_DAYS, KEEP_MOMENTS_DAYS))
    return {
        "event_state": _states(timeline, count),
        "player_line": _lines(timeline, count),
        "moment": _old("moment", OLD_MOMENTS, old, count),
        "notification": _old("notification", OLD_NOTIFICATIONS, old, count),
        "sidelined": _old("sidelined", OLD_SIDELINED, now - timedelta(days=sidelined.KEEP_DAYS), count),
        "match_follow": _old("match_follow", OLD_MATCH_FOLLOWS, now - timedelta(days=ACTIVITY_DAYS), count),
        "card_keys": _cards(count),
        "clip_uploads": _clip_uploads(now, count),
    }


def prune() -> dict[str, int] | None:
    """The daily run, under the lock; None if another run holds it."""
    token = bus.try_lock(LOCK, 3 * 3600)
    if token is None:
        log.info("retention: another run is going; skipped")
        return None
    try:
        done = run()
    finally:
        bus.release(LOCK, token)
    log.info("retention: %(event_state)d game states, %(player_line)d player lines, %(moment)d moments, %(notification)d notifications, %(sidelined)d spells out and %(match_follow)d lapsed match follows removed; %(card_keys)d idle cards set to expire; %(clip_uploads)d clips' unfinished uploads cleared", done)
    return done


if __name__ == "__main__":
    logs.setup()
    if sys.argv[1:] == ["--count"]:
        print(run(count=True))
    elif not sys.argv[1:]:
        result = prune()
        if result is None:
            sys.exit(f"another retention run is going (the worker's daily one); try again when it's done, or redis-cli del lock:{LOCK} if none is")
        print(result)
    else:
        sys.exit("usage: python -m sports_follow.retention [--count]")
