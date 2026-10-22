"""A player or a team followed by more fans than one query can name (BUG-325): Postgres takes at most 65,535 values
a statement, and planning a batch's pushes named every follower at once, so a moment of a player past that sent nothing
to anyone, and took every other moment of its batch with it, at every try. Now the queries that name fans do so a part
at a time (notify.chunks), with the same result for any number; and the tick's sweep of unsent moments sends a game's
apart where the whole batch fails, so one that can't be planned holds back no other's. Against a throwaway database
at alembic head (skipped without one; see tests/test_corrections_db.py for setting it up); the crowds are made in
the database itself, in one statement each."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from sports_follow import notify, worker
from sports_follow.models import Event, EventTeam, Follow, Moment, Notification, NotificationMute, Player, PushSubscription, Team, TeamFollow
from sports_follow.moments import Found

from .test_alerts_db import URL, db, device, pushed  # noqa: F401  (the fixtures)
from .test_board_alerts_db import Redis

pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

CROWD = 70_000  # more fans than the 65,535 values Postgres takes in one statement


def crowd(db, table: str, column: str, value: int, level: str | None = None) -> None:  # noqa: F811
    """CROWD made-up anonymous fans ("crowd-1" on) following one player ("follow", "player_id") or team ("team_follow",
    "team_id"), at `level` or the default."""
    db.execute(text(f"INSERT INTO {table} (fan_id, {column}, alert_rules, created_at) SELECT 'crowd-' || g, :value, CAST(:rules AS jsonb), now() FROM generate_series(1, :n) g"),
               {"value": value, "rules": json.dumps({"level": level} if level else {}), "n": CROWD})
    db.commit()


def crowd_devices(db) -> None:  # noqa: F811
    """A browser each for the whole crowd: notifications on."""
    db.execute(text("INSERT INTO push_subscription (fan_id, transport, endpoint, p256dh, auth, timezone, failures, live_cards, live_stopped, created_at)"
                    " SELECT 'crowd-' || g, 'webpush', 'https://fcm.googleapis.com/fcm/send/crowd-' || g, 'k', 'a', 'UTC', 0, true, '[]'::jsonb, now() FROM generate_series(1, :n) g"),
               {"n": CROWD})
    db.commit()


def game(db) -> None:  # noqa: F811
    """A live game with two players in it: a star (1) and a teammate (2)."""
    db.add_all([Player(id=1, slug="star", name="Star", sport="Football"), Player(id=2, slug="teammate", name="Teammate", sport="Football"),
                Event(id=1, key="g1", sport="football", title="Arsenal v Chelsea", status="live")])
    db.commit()


def goals(db) -> list[int]:  # noqa: F811
    """A goal by each of them, found by one poll: one batch."""
    ids = [m for pid, who in ((1, "Star"), (2, "Teammate")) for m in notify.record(db, pid, 1, [Found("goal", "key", f"Goal: {who}", "1–0 · 23'", f"goal@{pid}", 85)])]
    db.commit()
    return ids


def unsent(db) -> list[int]:  # noqa: F811
    db.expire_all()
    return list(db.scalars(select(Moment.id).where(Moment.sent_at.is_(None)).order_by(Moment.id)))


def test_a_player_followed_by_more_fans_than_a_query_can_name_still_has_their_moments_sent(db, pushed):  # noqa: F811
    # The star has 70,000 followers who never turned notifications on (each cookie a follow), and one who did; the
    # teammate one fan. Both goals were planned together, and every follower named at once: nobody heard either.
    game(db)
    crowd(db, "follow", "player_id", 1)
    db.add_all([Follow(fan_id="star-fan", player_id=1, alert_rules={}), Follow(fan_id="teammate-fan", player_id=2, alert_rules={})])
    db.commit()
    device(db, "star-fan")
    device(db, "teammate-fan")
    notify.send(goals(db))
    assert unsent(db) == []
    assert sorted((fan, p["title"]) for fan, p in pushed) == [("star-fan", "Goal: Star"), ("teammate-fan", "Goal: Teammate")]


def test_a_team_followed_by_more_fans_than_a_query_can_name_still_has_its_games_moments_sent(db, pushed):  # noqa: F811
    # The same through a team: its fans go into the batch's with the players' (teams.watches), India's cricket team as
    # much as a star.
    game(db)
    db.add(Team(id=1, name="Arsenal", sport="football", system="espn_soccer", external_id="359"))
    db.flush()
    db.add(EventTeam(event_id=1, team_id=1))
    db.commit()
    crowd(db, "team_follow", "team_id", 1)
    db.add_all([TeamFollow(fan_id="gooner", team_id=1, alert_rules={}), Follow(fan_id="teammate-fan", player_id=2, alert_rules={})])
    db.commit()
    device(db, "gooner")
    device(db, "teammate-fan")
    ids = notify.record_game(db, 1, [Found("start", "key", "Arsenal v Chelsea: kick-off", "", "start", 60)], "/match/espn_soccer/1") + goals(db)
    db.commit()
    notify.send(ids)
    assert unsent(db) == []
    heard = sorted((fan, p["title"]) for fan, p in pushed)
    assert ("gooner", "Arsenal v Chelsea: kick-off") in heard and ("teammate-fan", "Goal: Teammate") in heard


def test_more_fans_with_notifications_on_than_a_query_can_name_are_planned_too(db, pushed):  # noqa: F811
    # 70,000 followers with a browser each, who set the star to off, so none of them is pushed, but each is a fan
    # with a device: their mutes and earlier pushes were looked up naming them all at once.
    game(db)
    crowd(db, "follow", "player_id", 1, level="off")
    crowd_devices(db)
    db.add_all([Follow(fan_id="star-fan", player_id=1, alert_rules={}), Follow(fan_id="teammate-fan", player_id=2, alert_rules={}),
                Follow(fan_id="muted", player_id=1, alert_rules={}), NotificationMute(fan_id="muted", event_id=1)])
    db.commit()
    for fan in ("star-fan", "teammate-fan", "muted"):
        device(db, fan)
    notify.send(goals(db))
    assert unsent(db) == []
    assert sorted((fan, p["title"]) for fan, p in pushed) == [("star-fan", "Goal: Star"), ("teammate-fan", "Goal: Teammate")]


@pytest.mark.parametrize("at_once", [None, 1])
def test_fans_named_a_part_at_a_time_are_planned_as_when_named_at_once(db, pushed, monkeypatch, at_once):  # noqa: F811
    # The same plan whatever the parts: each fan's newest MAX_DEVICES devices, their mutes, and their pushes of the game
    # this hour, each fan's in their part.
    if at_once:
        monkeypatch.setattr(notify, "FANS_AT_ONCE", at_once)
    game(db)
    db.add_all([Follow(fan_id=fan, player_id=1, alert_rules={"level": "everything"}) for fan in ("many", "one", "muted")])
    db.add(NotificationMute(fan_id="muted", event_id=1))
    db.commit()
    for n in range(notify.MAX_DEVICES + 2):
        db.add(PushSubscription(fan_id="many", endpoint=f"https://fcm.googleapis.com/fcm/send/many-{n}", p256dh="k", auth="a", timezone="UTC"))
    db.commit()
    device(db, "one")
    device(db, "muted")
    for n, minute in ((1, 23), (2, 51)):
        ids = notify.record(db, 1, 1, [Found("goal", "key", "Goal: Star", f"{n}–0 · {minute}'", f"goal@{minute}", 85)])
        db.commit()
        notify.send(ids)
    newest = [f"many-{n}" for n in range(notify.MAX_DEVICES + 1, 1, -1)]
    assert sorted(endpoint for endpoint, _ in pushed) == sorted(["one", "one", *newest, *newest])  # never the muted fan's
    rows: dict[str, list[tuple[int, bool]]] = {}
    for fan, devices, loud in db.execute(select(Notification.fan_id, Notification.devices, Notification.loud).order_by(Notification.id)):
        rows.setdefault(fan, []).append((devices, loud))
    assert rows == {"many": [(notify.MAX_DEVICES, True), (notify.MAX_DEVICES, False)], "one": [(1, True), (1, False)]}  # the second goal's a silent update


def test_a_moment_left_to_a_boards_other_side_is_sent_late_to_more_fans_than_a_query_can_name(db, pushed, monkeypatch):  # noqa: F811
    # A moment held for its board's other side (alerts.voiced) keeps the fans it was left for: sent late, the ones it
    # reached already were looked up naming them all at once.
    redis = Redis()
    monkeypatch.setattr(notify.bus, "sync_redis", lambda: redis)
    game(db)
    db.add(Follow(fan_id="star-fan", player_id=1, alert_rules={}))
    db.commit()
    device(db, "star-fan")
    [held] = notify.record(db, 1, 1, [Found("goal", "key", "Goal: Star", "1–0 · 23'", "goal@1", 85)])
    db.execute(text("UPDATE moment SET sent_at = now() WHERE id = :id"), {"id": held})
    db.commit()
    fans = [f"crowd-{n}" for n in range(1, CROWD + 1)] + ["star-fan"]
    redis.hset(notify.HELD, str(held), json.dumps({"fans": fans, "at": datetime.now(timezone.utc).timestamp(), "kind": "goal"}))
    assert notify.send_held([held]) == 1
    assert [(fan, p["title"]) for fan, p in pushed] == [("star-fan", "Goal: Star")]
    assert redis.hgetall(notify.HELD) == {}


# ---------------------------------------------------------------- the tick's sweep


def test_the_sweep_sends_every_other_moment_past_one_that_cant_be_planned(db, pushed, monkeypatch, caplog):  # noqa: F811
    # The tick sends moments no sender claimed, the oldest 500, as one batch. One of them failing to plan failed them
    # all, and the next tick swept up the same list: every game's moments waited out their twelve hours.
    db.add_all([Player(id=n, slug=f"striker-{n}", name=f"Striker {n}", sport="Football") for n in (1, 2, 3)])
    db.add_all([Event(id=n, key=f"g{n}", sport="football", title=f"Game {n}", status="live") for n in (1, 2, 3)])
    db.flush()
    db.add_all([Follow(fan_id="fan", player_id=n, alert_rules={}) for n in (1, 2, 3)])
    db.commit()
    device(db, "fan")
    ids = [m for n in (1, 2, 3) for m in notify.record(db, n, n, [Found("goal", "key", f"Goal: Striker {n}", "1–0 · 23'", f"goal@{n}", 85)])]
    db.commit()
    bad = ids[1]
    plan = notify._plan  # pyright: ignore[reportPrivateUsage]

    def failing(db, items, now, *args, **kwargs):
        if any(i.id == bad for i in items):
            raise RuntimeError("can't plan this one")
        return plan(db, items, now, *args, **kwargs)

    monkeypatch.setattr(notify, "_plan", failing)
    later = datetime.now(timezone.utc) + timedelta(seconds=notify.SWEEP_AFTER + 1)
    assert notify.pending(later) == ids
    with caplog.at_level(logging.ERROR, logger="sports_follow.notify"):
        asyncio.run(worker.send_moments({}, notify.pending(later)))
    assert sorted(p["title"] for _, p in pushed) == ["Goal: Striker 1", "Goal: Striker 3"]
    assert unsent(db) == [bad]  # left for the next sweep, and logged
    assert any(str(bad) in r.getMessage() for r in caplog.records)
    monkeypatch.setattr(notify, "_plan", plan)  # whatever it was, it's planned on a later sweep
    pushed.clear()
    asyncio.run(worker.send_moments({}, notify.pending(later)))
    assert [p["title"] for _, p in pushed] == ["Goal: Striker 2"] and unsent(db) == []


def test_moments_held_for_a_boards_other_side_go_late_past_one_that_cant_be_planned(db, pushed, monkeypatch):  # noqa: F811
    # The tick's other sweep: every held moment due went in one go, so one that couldn't be planned kept every other
    # from its fans, every tick. Now it's kept for the next tick, alone.
    redis = Redis()
    monkeypatch.setattr(notify.bus, "sync_redis", lambda: redis)
    game(db)
    db.add_all([Follow(fan_id="star-fan", player_id=1, alert_rules={}), Follow(fan_id="teammate-fan", player_id=2, alert_rules={})])
    db.commit()
    device(db, "star-fan")
    device(db, "teammate-fan")
    ids = goals(db)
    db.execute(text("UPDATE moment SET sent_at = now()"))
    db.commit()
    for moment_id, fan in zip(ids, ("star-fan", "teammate-fan")):
        redis.hset(notify.HELD, str(moment_id), json.dumps({"fans": [fan], "at": datetime.now(timezone.utc).timestamp(), "kind": "goal"}))
    bad = ids[0]
    plan = notify._plan  # pyright: ignore[reportPrivateUsage]

    def failing(db, items, now, *args, **kwargs):
        if any(i.id == bad for i in items):
            raise RuntimeError("can't plan this one")
        return plan(db, items, now, *args, **kwargs)

    monkeypatch.setattr(notify, "_plan", failing)
    assert notify.send_held(ids) == 1
    assert [(fan, p["title"]) for fan, p in pushed] == [("teammate-fan", "Goal: Teammate")]
    assert list(redis.hgetall(notify.HELD)) == [str(bad)]  # kept for the next tick
    monkeypatch.setattr(notify, "_plan", plan)
    pushed.clear()
    assert notify.send_held([bad]) == 1 and [(fan, p["title"]) for fan, p in pushed] == [("star-fan", "Goal: Star")]
    assert redis.hgetall(notify.HELD) == {}
