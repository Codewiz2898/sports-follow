"""Today's "This week" (week.posters, GET /api/me/week) against Postgres: the fan's players' big moments of
the last seven days, one poster per player and game, biggest first. Skipped without a throwaway database
at alembic head (tests/test_corrections_db.py says how to make one). Every test empties the tables first.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from sports_follow import server
from sports_follow.models import Event, Follow, Moment, Player

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

NOW = datetime.now(timezone.utc)


@pytest.fixture
def db():
    engine = create_engine(URL)
    with engine.begin() as c:
        c.execute(text("TRUNCATE player, event, follow, moment RESTART IDENTITY CASCADE"))
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    s.add_all([
        Player(id=1, slug="mohamed-salah", name="Mohamed Salah", sport="Football", teams=["Egypt"]),
        Player(id=2, slug="smriti-mandhana", name="Smriti Mandhana", sport="Cricket", teams=["India"]),
        Player(id=3, slug="virat-kohli", name="Virat Kohli", sport="Cricket", teams=["India"]),
    ])
    s.flush()
    s.add_all([
        Event(id=10, key="e10", sport="football", title="Egypt v South Africa", status="final"),
        Event(id=11, key="e11", sport="football", title="Egypt v Ghana", status="final"),
        Event(id=20, key="e20", sport="cricket", title="India v Australia", status="final"),
        Follow(fan_id="fan", player_id=1), Follow(fan_id="fan", player_id=2),
        Follow(fan_id="other", player_id=3),
    ])
    s.flush()
    yield s
    s.close()
    engine.dispose()


def moment(db, id: int, player: int, event: int | None, kind: str, title: str, body: str = "", priority: int = 50, level: str = "key", ago: timedelta = timedelta(days=1), corrects: int | None = None) -> None:
    db.add(Moment(id=id, key=f"m{id}", player_id=player, event_id=event, kind=kind, level=level, priority=priority, title=title, body=body, created_at=NOW - ago, corrects=corrects))
    db.flush()


def test_a_game_is_one_poster_its_biggest_moment_and_the_biggest_go_first(db):
    # Three goals in one game: the hat-trick is its poster. Then a hundred, a bigger moment than a lone goal.
    moment(db, 1, 1, 10, "goal", "Goal: Mohamed Salah", "Egypt 1 – 0 South Africa · 10'", 85)
    moment(db, 2, 1, 10, "goal", "Goal: Mohamed Salah (2)", "Egypt 2 – 0 South Africa · 60'", 85)
    moment(db, 3, 1, 10, "goal", "Hat-trick: Mohamed Salah", "Egypt 3 – 0 South Africa · 80'", 95)
    moment(db, 4, 1, 10, "final", "Mohamed Salah: Won 3–0", "", 80)
    moment(db, 5, 2, 20, "final", "Smriti Mandhana: India won by 40 runs", "", 80)  # no poster: the next one is
    moment(db, 6, 2, 20, "hundred", "Smriti Mandhana reaches 100", "112 (98) · IND 289/4", 90, ago=timedelta(days=3))
    moment(db, 7, 1, 11, "goal", "Goal: Mohamed Salah", "Egypt 1 – 0 Ghana · 33'", 85, ago=timedelta(days=5))
    posters = server.this_week("fan", db)["posters"]
    assert [(p["id"], p["word"], p["line"]) for p in posters] == [
        (3, "Hat-trick", "v South Africa"),
        (6, "Hundred", "112 (98) v Australia"),
        (7, "Goal", "33' v Ghana"),
    ]
    assert posters[1] | {"at": None} == {
        "id": 6, "player_id": 2, "player": "Smriti Mandhana", "sport": "Cricket", "event_id": 20,
        "word": "Hundred", "line": "112 (98) v Australia", "url": "/", "at": None,
    }


def test_only_the_fans_players_this_week_and_never_a_goal_ruled_out(db):
    moment(db, 1, 1, 10, "goal", "Goal: Mohamed Salah", "Egypt 1 – 0 South Africa · 10'", 85)
    moment(db, 2, 1, 10, "correction", "Goal ruled out: Mohamed Salah", "Egypt 0 – 0 South Africa · 12'", 85, corrects=1)
    moment(db, 3, 1, 11, "goal", "Goal: Mohamed Salah", "Egypt 1 – 0 Ghana · 33'", 85, ago=timedelta(days=8))  # last week
    moment(db, 4, 3, 20, "hundred", "Virat Kohli reaches 100", "100 (80)", 90)  # another fan's player
    moment(db, 5, 2, 20, "fifty", "Smriti Mandhana reaches 50", "50 (40)", 70, level="minor")  # a level no poster has
    assert server.this_week("fan", db)["posters"] == []
    assert [p["word"] for p in server.this_week("other", db)["posters"]] == ["Hundred"]
    assert server.this_week("nobody", db)["posters"] == []
