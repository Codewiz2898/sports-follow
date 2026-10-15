"""Today's "This week" and the Wire (week.py, GET /api/me/week) against Postgres: the fan's players' big
moments of the last seven days, one poster per player and game, biggest first, and their games that ended
with no moment that makes one, read off their results; and their line-ups and news, newest first. Skipped without a throwaway database at alembic head (tests/test_corrections_db.py
says how to make one). Every test empties the tables first.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from sports_follow import notify, pipeline, server, week
from sports_follow.models import Event, EventPlayer, Follow, Moment, Player, PlayerLine
from sports_follow.moments import Found, Seen, detect

from .test_moments import goal, line, scored

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

NOW = datetime.now(timezone.utc)


@pytest.fixture
def db():
    engine = create_engine(URL)
    with engine.begin() as c:
        c.execute(text("TRUNCATE player, event, follow, moment, news_item, news_player RESTART IDENTITY CASCADE"))
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
        Event(id=21, key="e21", sport="cricket", title="India v England", status="scheduled"),
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


def finished(db, player: int, event: int, contribution: str | None, result: str = "", ago: timedelta = timedelta(days=1)) -> None:
    """The player's result in a game that started `ago`, as the results refresh stores it for a game first
    seen after it ended (structured.result_item): their line in a few words and the result, no moments."""
    game = db.get(Event, event)
    game.start_utc = NOW - ago
    db.add(EventPlayer(event_id=event, player_id=player, result={"title": game.title, "result": result, "player_contribution": contribution}))
    db.flush()


def test_a_game_first_seen_after_it_ended_is_read_off_its_result(db):
    finished(db, 2, 20, "112 (98)", "India won by 40 runs", ago=timedelta(days=2))
    finished(db, 1, 10, "2 goals", "Won 2–0")
    finished(db, 1, 11, "Started", "Drew 0–0")  # nothing a poster says
    posters = server.this_week("fan", db)["posters"]
    assert [(p["id"], p["word"], p["line"]) for p in posters] == [(None, "Hundred", "112 (98) v Australia"), (None, "Brace", "v South Africa")]
    assert datetime.fromisoformat(posters[0]["at"]) == NOW - timedelta(days=2)  # the game's start dates it
    assert posters[0] | {"at": None} == {
        "id": None, "player_id": 2, "player": "Smriti Mandhana", "sport": "Cricket", "event_id": 20,
        "word": "Hundred", "line": "112 (98) v Australia", "url": "/player/2", "at": None,
    }


def test_moments_win_and_a_game_whose_moments_make_no_poster_is_read_off_its_result(db):
    # Polled live: the moment says what the goal did, which the line can't.
    moment(db, 1, 1, 10, "goal", "Late equaliser: Mohamed Salah", "Egypt 1 – 1 South Africa · 88' · assist Trezeguet", 95)
    finished(db, 1, 10, "1 goal", "Drew 1–1")
    # Polled live too, from the start, and only the result was told: the line makes the poster.
    moment(db, 2, 2, 20, "start", "Smriti Mandhana's game has started", "", 40, ago=timedelta(days=2))
    moment(db, 3, 2, 20, "final", "Smriti Mandhana: India won by 40 runs", "", 80)
    finished(db, 2, 20, "112 (98)", "India won by 40 runs")
    assert [(p["id"], p["word"], p["line"]) for p in server.this_week("fan", db)["posters"]] == [
        (1, "Late equaliser", "88' v South Africa"),
        (None, "Hundred", "112 (98) v Australia"),
    ]


def test_only_the_fans_players_games_that_ended_this_week(db):
    finished(db, 1, 10, "2 goals", "Won 2–0", ago=timedelta(days=8))  # last week
    finished(db, 2, 21, "103 (90)")  # its game isn't over
    finished(db, 3, 20, "100 (80)", "India won by 40 runs")  # another fan's player
    db.get(Event, 11).start_utc = NOW - timedelta(days=1)
    db.add(EventPlayer(event_id=11, player_id=1))  # no result stored for them yet
    db.flush()
    assert server.this_week("fan", db)["posters"] == []
    assert [p["word"] for p in server.this_week("other", db)["posters"]] == ["Hundred"]


def test_two_players_in_a_game_are_a_poster_each_and_a_result_without_a_line_has_the_one_stored(db):
    db.add(Follow(fan_id="fan", player_id=3))
    finished(db, 2, 20, "57 (40)", "India won by 40 runs")
    finished(db, 3, 20, None, "India won by 40 runs")  # read before its scorecard: the line stored for him has it
    db.add_all([
        PlayerLine(event_id=20, player_id=3, as_of=NOW - timedelta(hours=30), stats={"headline": "38* (30)", "stats": []}),
        PlayerLine(event_id=20, player_id=3, as_of=NOW - timedelta(hours=26), stats={"headline": "38 (31) · 4/30 (10 ov)", "stats": []}),
    ])
    db.flush()
    assert [(p["player"], p["word"], p["line"]) for p in server.this_week("fan", db)["posters"]] == [
        ("Virat Kohli", "Four-for", "4/30 (10 ov) v Australia"),  # a four-for's moment outranks a fifty's
        ("Smriti Mandhana", "Fifty", "57 (40) v Australia"),
    ]


def test_posters_read_off_results_count_toward_the_limit_the_newest_first(db):
    db.add_all(Event(id=100 + n, key=f"g{n}", sport="football", title="Egypt v Ghana", status="final") for n in range(week.LIMIT + 5))
    db.flush()
    for n in range(week.LIMIT + 5):
        finished(db, 1, 100 + n, "1 goal", "Won 1–0", ago=timedelta(hours=n + 1))
    posters = server.this_week("fan", db)["posters"]
    assert [p["event_id"] for p in posters] == [100 + n for n in range(week.LIMIT)]
    assert len(posters) == 12  # Today shows every one the server sends (BHA-226)


def test_a_shared_game_is_its_poster_whenever_it_was_and_whoever_follows(db):
    # A link to a game (share.py) previews the poster This week makes of it, long after the week, for anyone.
    moment(db, 1, 1, 10, "goal", "Goal: Mohamed Salah", "Egypt 1 – 0 South Africa · 10'", 85, ago=timedelta(days=40))
    moment(db, 2, 1, 10, "goal", "Goal: Mohamed Salah (2)", "Egypt 2 – 0 South Africa · 60'", 85, ago=timedelta(days=40))
    game = week.game(db, 1, 10)
    assert game is not None and (game["word"], game["line"], game["title"], game["competition"]) == ("Brace", "v South Africa", "Egypt v South Africa", None)
    # No moment makes one: read off the result.
    finished(db, 2, 20, "112 (98)", "India won by 40 runs", ago=timedelta(days=30))
    game = week.game(db, 2, 20)
    assert game is not None and (game["id"], game["word"], game["line"]) == (None, "Hundred", "112 (98) v Australia")
    # A goal VAR ruled out makes none, nor does a game the player isn't in, or one with nothing to show.
    moment(db, 3, 1, 11, "goal", "Goal: Mohamed Salah", "Egypt 1 – 0 Ghana · 33'", 85)
    moment(db, 4, 1, 11, "correction", "Goal ruled out: Mohamed Salah", "Egypt 0 – 0 Ghana · 35'", 85, corrects=3)
    assert week.game(db, 1, 11) is None
    assert week.game(db, 3, 10) is None and week.game(db, 1, 20) is None
    finished(db, 1, 11, "Started", "Drew 0–0")
    assert week.game(db, 1, 11) is None


def story(db, player_id: int, headline: str, kind: str | None, breaking: bool = True, hours: int = 2, source: str = "Sportstar") -> None:
    """A story the research agent brought back for the player, down the real path: stored, then a moment
    when it's new, recent and marked as just happened (pipeline.news_moments)."""
    player = db.get(Player, player_id)
    item = {"headline": headline, "summary": "", "source": source, "url": f"https://news.example/{abs(hash(headline))}", "published": (NOW - timedelta(hours=hours)).isoformat(), "kind": kind, "breaking": breaking}
    fresh = pipeline._write_news(db, player, [item])
    notify.record(db, player_id, None, pipeline.news_moments(player.name, fresh, NOW))
    db.flush()


def test_the_wire_is_line_ups_to_come_and_the_stories_marked_as_just_happened(db):
    notify.record(db, 2, 21, [Found("lineup", "key", "Smriti Mandhana is in the XI", "India v England · line-ups are out", "starting", 50)])
    notify.record(db, 2, 20, [Found("lineup", "key", "Smriti Mandhana is in the XI", "India v Australia · line-ups are out", "starting", 50)])  # that game is over
    story(db, 1, "BREAKING: Mohamed Salah ruled out for three weeks", "injury", hours=1, source="BBC Sport")
    story(db, 2, "Mandhana retires from T20Is", "retirement", hours=5)
    story(db, 1, "Salah sets a club record", "milestone", hours=3)  # a minor moment: not the Wire's
    story(db, 1, "Is Salah's time at the top over?", "transfer", breaking=False)  # an opinion piece: no moment
    story(db, 3, "Kohli out with a back spasm", "injury")  # another fan's player
    db.execute(text("UPDATE moment SET created_at = now() - interval '1 hour' WHERE kind = 'lineup'"))
    db.execute(text("UPDATE moment SET created_at = now() - interval '5 hours' WHERE title LIKE '%retirement%'"))
    rows = server.this_week("fan", db)["wire"]
    assert [(r["tag"], r["text"], r["source"]) for r in rows] == [
        ("Injury", "Mohamed Salah ruled out for three weeks", "BBC Sport"),  # never "breaking"
        ("Line-up", "Smriti Mandhana is in the XI v England", None),
        ("Retired", "Mandhana retires from T20Is", "Sportstar"),
    ]
    assert rows[0]["url"].startswith("https://news.example/") and rows[1]["url"] == "/player/2"
    assert {r["sport"] for r in rows} == {"Football", "Cricket"}
    assert server.this_week("nobody", db) == {"posters": [], "wire": []}


def test_a_brace_is_counted_from_the_goals_that_stand(db):
    # The moments the rules write for 1-0 then 2-0: the first "puts Egypt ahead" outranks the second's
    # plain "(2)", and the poster still counts two goals.
    polls = [Seen("live", line=line("Started")), scored(1, 0, [goal("10'")], clock="10'"), scored(2, 0, [goal("60'"), goal("10'")], goals=2, clock="60'")]
    for before, now in zip(polls, polls[1:]):
        notify.record(db, 1, 10, detect("Football", "Mohamed Salah", "173896", before, now))
    db.flush()
    assert [(p["word"], p["line"]) for p in server.this_week("fan", db)["posters"]] == [("Brace", "v South Africa")]
    # VAR rules out the first: one goal stands, under its "(2)" title.
    first = db.scalar(text("SELECT id FROM moment WHERE title LIKE '%puts Egypt ahead'"))
    notify.record(db, 1, 10, [Found("correction", "key", "Goal ruled out: Mohamed Salah", "Egypt 1 – 0 South Africa · 62'", db.get(Moment, first).key.split(":", 2)[2])])
    db.flush()
    assert [(p["word"], p["line"]) for p in server.this_week("fan", db)["posters"]] == [("Goal", "60' v South Africa")]


def test_the_wire_tells_a_late_team_sheet_change_and_each_story_once(db):
    # Named to start, then moved to the bench: only the change is still true.
    notify.record(db, 2, 21, [Found("lineup", "key", "Smriti Mandhana is in the XI", "India v England · line-ups are out", "starting", 50)])
    notify.record(db, 2, 21, [Found("lineup", "key", "Smriti Mandhana is named a substitute", "India v England · line-ups are out", "bench", 45)])
    # One story about two of the fan's players: one row.
    story(db, 1, "Salah and Mandhana named in the team of the week", "milestone")
    story(db, 1, "Salah and Mandhana both out injured", "injury")
    story(db, 2, "Salah and Mandhana both out injured", "injury")
    # A story first stored for a player as nothing in particular, then for another as an injury: the
    # moment says what it was told as.
    story(db, 3, "Mandhana hurt at training", None, breaking=False)
    story(db, 2, "Mandhana hurt at training", "injury")
    rows = server.this_week("fan", db)["wire"]
    assert sorted((r["tag"], r["text"]) for r in rows) == [
        ("Injury", "Mandhana hurt at training"),
        ("Injury", "Salah and Mandhana both out injured"),
        ("Line-up", "Smriti Mandhana is named a substitute v England"),
    ]
