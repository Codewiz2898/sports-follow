"""Retention (retention.py, BUG-025): a finished game's timeline is cut to the rows still read, old moments
and pushes go, and idle hot copies of cards expire. Redis and the database are fakes, except in the last
test, which runs the real queries when SPORTS_FOLLOW_TEST_DATABASE_URL names an empty, migrated database.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text

from sports_follow import bus, db as db_module, results, retention, server
from sports_follow.models import PlayerCard


class Redis:
    """The calls _cards makes: SCAN, and a pipeline of TTL or EXPIRE NX."""

    def __init__(self, keys: dict[str, int]):
        self.ttls = dict(keys)  # key -> seconds to live, -1 for none

    def scan_iter(self, match, count):
        assert match == "card:*"
        return iter(list(self.ttls))

    def pipeline(self, transaction=True):
        redis, calls = self, []

        class Pipe:
            def ttl(self, key):
                calls.append(lambda: redis.ttls[key])

            def expire(self, key, seconds, nx=False):
                def run():
                    if nx and redis.ttls[key] != -1:
                        return False
                    redis.ttls[key] = seconds
                    return True

                calls.append(run)

            def execute(self):
                return [c() for c in calls]

        return Pipe()


def test_hot_copies_of_players_nobody_follows_expire(monkeypatch):
    redis = Redis({"card:1": -1, "card:2": -1, "card:3": -1, "card:4": 100, "card:999": -1})
    monkeypatch.setattr(bus, "sync_redis", lambda: redis)

    class Session:
        def scalars(self, stmt):
            return [1, 2]  # followed

    @contextmanager
    def fake_db(count):
        yield Session()

    monkeypatch.setattr(retention, "_db", fake_db)
    assert retention._cards(count=True) == 2  # card:3 and card:999 have no TTL
    assert redis.ttls == {"card:1": -1, "card:2": -1, "card:3": -1, "card:4": 100, "card:999": -1}  # counting changes nothing
    assert retention._cards(count=False) == 2
    assert redis.ttls == {"card:1": -1, "card:2": -1, "card:3": retention.CARD_TTL, "card:4": 100, "card:999": retention.CARD_TTL}
    assert retention._cards(count=False) == 0  # a copy that expires already keeps its time


def test_a_hot_copy_that_expired_is_rebuilt_from_postgres(monkeypatch):
    """Why an idle card may expire: the API rebuilds a missing hot copy from the card's row."""

    class AsyncRedis:
        def __init__(self):
            self.data = {}

        async def get(self, key):
            return self.data.get(key)

        async def set(self, key, value):
            self.data[key] = value

    class Session:
        def get(self, model, player_id):
            assert model is PlayerCard
            return PlayerCard(player_id=player_id, card={"player_id": player_id, "version": 7})

    async def in_db(fn, *args):
        return fn(Session(), *args)

    redis = AsyncRedis()
    monkeypatch.setitem(server.state, "redis", redis)
    monkeypatch.setattr(server, "in_db", in_db)
    assert asyncio.run(server.read_card(3)) == {"player_id": 3, "version": 7}
    assert json.loads(redis.data["card:3"]) == {"player_id": 3, "version": 7}


def test_one_run_at_a_time(monkeypatch):
    monkeypatch.setattr(bus, "try_lock", lambda name, seconds: None)
    monkeypatch.setattr(retention, "run", lambda **kw: pytest.fail("ran without the lock"))
    assert retention.prune() is None


def test_moments_outlive_the_activity_list(monkeypatch):
    """However low KEEP_MOMENTS_DAYS is set, the moments the activity list shows stay."""
    seen = {}
    monkeypatch.setattr(retention, "KEEP_MOMENTS_DAYS", 7)
    monkeypatch.setattr(retention, "_states", lambda cutoff, count: 0)
    monkeypatch.setattr(retention, "_lines", lambda cutoff, count: 0)
    monkeypatch.setattr(retention, "_cards", lambda count: 0)
    monkeypatch.setattr(retention, "_old", lambda table, victims, cutoff, count: seen.setdefault(table, cutoff) and 0)
    now = datetime(2026, 10, 13, tzinfo=timezone.utc)
    retention.run(now=now)
    assert now - seen["moment"] >= timedelta(days=server.ACTIVITY_DAYS)


# ---------------------------------------------------------------- the real queries


TEST_DB = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
TABLES = "notification_mute, notification, moment, push_subscription, news_player, news_item, player_line, event_state, event_player, event, follow, player_card, player_identity, player_alias, source_binding, membership, team, player"


@pytest.mark.skipif(not TEST_DB, reason="SPORTS_FOLLOW_TEST_DATABASE_URL isn't set (an empty database at alembic head)")
def test_a_finished_games_timeline_is_cut_to_what_is_read(monkeypatch):
    engine = create_engine(TEST_DB)
    monkeypatch.setattr(db_module, "SessionLocal", db_module.sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setattr(retention, "_cards", lambda count: 0)
    now = datetime.now(timezone.utc)
    ago = lambda days, minutes=0: now - timedelta(days=days, minutes=minutes)  # noqa: E731
    with engine.begin() as c:
        c.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
        x = lambda sql, **p: c.execute(text(sql), p)  # noqa: E731
        x("INSERT INTO player (id, slug, name, sport, status, teams) VALUES (1, 'a', 'A', 'cricket', 'active', '[]')")
        x("INSERT INTO follow (fan_id, player_id, alert_rules, created_at) VALUES ('fan', 1, '{}', :t)", t=ago(200))
        # 1: final 40 days ago; 2: final 3 days ago; 3: still live; 4: final, then a late update that isn't.
        for e, status, age in [(1, "final", 40), (2, "final", 3), (3, "live", 40), (4, "final", 40)]:
            x("INSERT INTO event (id, key, sport, competition, title, participants, status, start_utc) VALUES (:e, :k, 'cricket', '', :t, '[]', :s, :a)", e=e, k=f"k{e}", t=f"G{e}", s=status, a=ago(age, 600))
            x("INSERT INTO event_player (event_id, player_id, result) VALUES (:e, 1, CAST(:r AS jsonb))", e=e, r=json.dumps({"title": f"G{e}", "result": "won"}) if status == "final" else None)
            for k in range(10):
                final = status == "final" and k == 9
                x("INSERT INTO event_state (event_id, as_of, status, score_label, state) VALUES (:e, :a, :s, :sc, '{}')", e=e, a=ago(age, 600 - k), s="final" if final else "live", sc=f"{e}:{k}")
                x("INSERT INTO player_line (event_id, player_id, as_of, stats) VALUES (:e, 1, :a, CAST(:j AS jsonb))", e=e, a=ago(age, 600 - k), j=json.dumps({"headline": f"{e}:{k}"}))
        x("INSERT INTO event_state (event_id, as_of, status, score_label, state) VALUES (4, :a, 'live', 'late', '{}')", a=ago(40, 500))
        for key, event, age in [("old-1", 1, 100), ("old-news", None, 100), ("old-live", 3, 100), ("new-2", 2, 20)]:
            x("INSERT INTO moment (key, player_id, event_id, kind, level, priority, title, body, url, created_at) VALUES (:k, 1, :e, 'fifty', 'key', 50, :k, '', '/', :a)", k=key, e=event, a=ago(age))
        for age in (100, 10):
            x("INSERT INTO notification (fan_id, player_id, moment_ids, kind, lines, loud, devices, delivered, created_at) VALUES ('fan', 1, '[]', 'fifty', '[]', true, 1, 1, :a)", a=ago(age))

    def view():
        with db_module.session() as s:
            return json.dumps({
                "detail": [results.detail(s, 1, e) for e in (1, 2, 4)],
                "form": results.player_form(s, 1, 10),
                "activity": server.activity(None, 50, "fan", s),
                "latest": s.execute(text("SELECT DISTINCT ON (event_id) event_id, id FROM event_state ORDER BY event_id, as_of DESC")).all(),
                "lines": s.execute(text("SELECT DISTINCT ON (event_id) event_id, id FROM player_line ORDER BY event_id, as_of DESC")).all(),
            }, default=str)

    before = view()
    would = retention.run(count=True)
    done = retention.run()
    assert would == done == {"event_state": 9 + 9, "player_line": 9 + 9, "moment": 2, "notification": 1, "sidelined": 0, "card_keys": 0}
    assert view() == before
    with engine.connect() as c:
        kept = c.execute(text("SELECT event_id, score_label FROM event_state ORDER BY event_id, id")).all()
        assert [(e, s) for e, s in kept if e in (1, 4)] == [(1, "1:9"), (4, "4:9"), (4, "late")]  # final, and the latest
        assert c.scalar(text("SELECT count(*) FROM event_state WHERE event_id IN (2, 3)")) == 20  # recent, and live: untouched
        assert sorted(c.scalars(text("SELECT key FROM moment"))) == ["new-2", "old-live"]
    assert retention.run() == {"event_state": 0, "player_line": 0, "moment": 0, "notification": 0, "sidelined": 0, "card_keys": 0}
    with engine.begin() as c:
        c.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
