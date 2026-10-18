"""What the Android app draws (fcm.py): a moment's scorecard (notify.scorecard) and a followed game's live card
(livecards.py), decided against a fake Redis and sent to a fake FCM (tests/test_fcm.py). tests/test_livecards_db.py
sends them to the fans of a game in a real database."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from sports_follow import alerts, bus, livecards, notify, sports
from sports_follow.adapters.base import Snapshot

from .test_fcm import fake_fcm, no_fcm  # noqa: F401  (the fixtures)
from .test_pipeline import FakeRedis

NOW = datetime(2026, 10, 18, 15, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- a moment's scorecard


def item(moment_id: int, kind: str, title: str, body: str = "", player_id: int | None = 7, level: str = "key") -> alerts.Item:
    return alerts.Item(moment_id, player_id, 1, kind, level, 80, title, body, "/player/7", NOW)


@pytest.mark.parametrize("sport, kind, title, body, word, line", [
    ("football", "goal", "Goal: Bukayo Saka", "1–0 · 23'", "Goal", "Bukayo Saka"),  # its sport's word (Sport.high)
    # Football's high reads a hat-trick alone as a goal (one of three may be all VAR left standing, on a poster).
    ("football", "goal", "Hat-trick: Bukayo Saka", "3–0 · 67'", "Goal", "Hat-trick: Bukayo Saka"),
    ("football", "goal", "Late equaliser: Bukayo Saka", "2–2 · 88'", "Late equaliser", "Bukayo Saka"),
    ("football", "red", "Red card: Bukayo Saka", "", "Red card", "Bukayo Saka"),  # the title's own
    ("cricket", "hundred", "Virat Kohli reaches 100", "100* (92) · IND 250/3", "Hundred", "Virat Kohli reaches 100"),
    ("cricket", "wickets", "Jasprit Bumrah takes 5 wickets", "5/23 (8 ov)", "Five-for", "Jasprit Bumrah takes 5 wickets"),
    ("cricket", "out", "Virat Kohli out for 82", "82 (70)", "Out", "Virat Kohli out for 82"),  # its kind's
    ("tennis", "upset", "Upset: Jakub Mensik beats [2] Carlos Alcaraz", "", "Upset", "Jakub Mensik beats [2] Carlos Alcaraz"),
    # A title that starts with the player's name has no word of its own: the kind's, or none.
    ("basketball", "points", "Nikola Jokić: 30 points", "", "30 points", "Nikola Jokić: 30 points"),
    ("basketball", "points", "Nikola Jokić: 20 points", "", "", "Nikola Jokić: 20 points"),
    ("football", "final", "Nikola Jokić: Egypt 2 – 1 Ghana", "", "Result", "Nikola Jokić: Egypt 2 – 1 Ghana"),
    ("football", "news", "Nikola Jokić: injury news", "Out for a month", "News", "Nikola Jokić: injury news"),
    ("football", "lineup", "Bukayo Saka starts", "Starting XI", "Line-up", "Bukayo Saka starts"),
    ("golf", "start", "Rory McIlroy's game has started", "", "Live", "Rory McIlroy's game has started"),
    ("chess", "swing", "Turnaround: Gukesh D is winning", "", "Turnaround", "Gukesh D is winning"),
    # A long head is a sentence, not a word; nor is a whole title (esports' word for a game's own match won).
    ("chess", "swing", "A very long way of saying it: Gukesh D is winning", "", "", "A very long way of saying it: Gukesh D is winning"),
    ("bgmi", "dinner", "Booyah, Soul", "Match 3", "", "Booyah, Soul"),
    ("bgmi", "dinner", "Booyah: GodLike Esports", "Owais · Match 3 · 9 finishes", "Booyah", "GodLike Esports"),
])
def test_a_scorecards_big_word_is_its_sports_else_its_titles_else_its_kinds(sport, kind, title, body, word, line):
    who = "Nikola Jokić" if title.startswith("Nikola") else ""
    assert notify.word_and_line(sports.of(sport), item(1, kind, title, body), title, who) == (word, line)


def board(names: tuple[str, ...] = ("Arsenal v Chelsea", "Arsenal vs Chelsea")) -> livecards.Board:
    seen = sports.of("football").glance({"home": {"abbr": "ARS", "score": "1"}, "away": {"abbr": "CHE", "score": "0"}}, "", "67'", "live", [])
    return livecards.Board(sports.of("football"), livecards.keys(sports.of("football"), "Premier League", "espn_soccer:740001", "live", seen), names)


def push(lines: list[str], story: list[str] | None = None, player_id: int | None = None, event_id: int | None = 1, kind: str = "goal") -> alerts.Push:
    return alerts.Push(event_id, player_id, kind, list(range(1, len(lines) + 1)), lines, lines[0], "", "/match/x", True, "e1", 900, story or [])


def test_a_games_own_moment_is_read_without_the_games_name():
    lines = ["Arsenal v Chelsea: Late goal: Bukayo Saka puts Arsenal ahead", "Arsenal v Chelsea: Yellow card for Declan Rice"]
    items = {1: item(1, "goal", lines[0], player_id=None), 2: item(2, "yellow", lines[1], player_id=None, level="minor")}
    story = ["Arsenal vs Chelsea: under way", "x" * 100, "dropped: only three lines go"]
    drawn = notify.scorecard(push(lines, story), items, {1: board()}, {})
    assert drawn == {
        "sport": "football", "colour": "#12c46c", "ink": "#0b1a10", "league": "Premier League", "match": "/match/espn_soccer/740001",
        "sides": [["ARS", "1"], ["CHE", "0"]], "status": "67'", "live": "1",
        "word": "Late goal", "line": "Bukayo Saka puts Arsenal ahead",
        # The push's other lines, then the game's earlier ones, newest first, each at most 80 characters.
        "story": ["Yellow card for Declan Rice", "under way", "x" * 79 + "…"], "photo": "",
    }
    # A goal whose title says only what happened has its kind's word.
    plain = ["Arsenal v Chelsea: Bukayo Saka scores"]
    assert notify.scorecard(push(plain), {1: item(1, "goal", plain[0], player_id=None)}, {1: board()}, {})["word"] == "Goal"


def test_a_players_news_wears_their_sports_colour_and_their_photo(monkeypatch):
    monkeypatch.setattr(notify, "SITE_URL", "https://stann.example")
    news = ["Rohit Sharma: injury news"]
    drawn = notify.scorecard(push(news, player_id=7, event_id=None, kind="news"), {1: item(1, "news", news[0])}, {}, {7: ("Rohit Sharma", "Cricket", "/api/photos/ab.jpg")})
    assert drawn == {"sport": "cricket", "colour": "#1f5bef", "ink": "#ffffff", "league": "", "match": "", "sides": [], "status": "", "live": "0",
                     "word": "News", "line": "Rohit Sharma: injury news", "story": [], "photo": "https://stann.example/api/photos/ab.jpg"}
    # Without the site's address (in development) there's no address for the photo the app could load.
    monkeypatch.setattr(notify, "SITE_URL", "")
    assert notify.scorecard(push(news, player_id=7, event_id=None, kind="news"), {1: item(1, "news", news[0])}, {}, {7: ("Rohit Sharma", "Cricket", "/api/photos/ab.jpg")})["photo"] == ""


def test_the_test_push_is_a_moment_of_no_sport_in_stann_blue():
    message = notify.fcm.message("t" * 40, {"id": 0, "title": "Notifications are on", "body": "…", "url": "/", "tag": "test", "kind": "test", "loud": True, "quiet": False}, 600)
    data = message["data"]
    assert "notification" not in message and (data["v"], data["type"], data["tag"], data["loud"]) == ("2", "moment", "test", "1")
    assert {k: data[k] for k in ("sport", "colour", "ink", "league", "match", "sides", "status", "live", "word", "line", "story", "photo")} == {
        "sport": "", "colour": "#1f5bef", "ink": "#ffffff", "league": "", "match": "", "sides": "", "status": "", "live": "0",
        "word": "", "line": "Notifications are on", "story": "", "photo": ""}


# ---------------------------------------------------------------- live cards


class Redis(FakeRedis):
    """FakeRedis, with delete saying how many keys it deleted, as Redis does."""

    def delete(self, *keys):
        gone = sum(1 for key in keys if key in self.data)
        super().delete(*keys)
        return gone


@pytest.fixture
def redis(monkeypatch):
    fake = Redis()
    monkeypatch.setattr(bus, "_sync", fake)
    return fake


@pytest.fixture
def cards(monkeypatch):
    """The cards livecards hands to its pool, as (event id, kind, card), sent nowhere; and its clock, moved by hand."""
    sent: list[tuple[int, str, dict | None]] = []
    clock = SimpleNamespace(now=1_000_000.0)
    monkeypatch.setattr(livecards, "_pool", SimpleNamespace(submit=lambda fn, event_id, kind, made: sent.append((event_id, kind, made))))
    monkeypatch.setattr(livecards.time, "time", lambda: clock.now)
    return SimpleNamespace(sent=sent, clock=clock)


EVENT = SimpleNamespace(id=5, sport="Football", competition="Premier League", key="espn_soccer:740001", title="Arsenal vs Chelsea")


def snap(home: int, away: int, clock: str, status: str = "live", goals: list[dict] | None = None) -> Snapshot:
    state = {"kind": "football", "home": {"id": "1", "name": "Arsenal", "abbr": "ARS", "score": str(home)}, "away": {"id": "2", "name": "Chelsea", "abbr": "CHE", "score": str(away)}}
    return Snapshot(status, f"Arsenal {home} – {away} Chelsea", clock, state, moments=goals or [])


def test_a_live_games_card_goes_when_its_score_changes_and_its_clock_waits(fake_fcm, redis, cards):  # noqa: F811
    goal = {"id": "g1", "clock": "23'", "kind": "GOAL", "team_id": "2", "own_goal": False}
    livecards.polled(EVENT, snap(0, 1, "23'", goals=[goal]))
    [(event_id, kind, made)] = cards.sent
    assert (event_id, kind) == (5, "live")
    assert made == {"sport": "football", "colour": "#12c46c", "ink": "#0b1a10", "league": "Premier League", "match": "/match/espn_soccer/740001",
                    "sides": [["ARS", "0"], ["CHE", "1"]], "status": "23'", "live": "1", "type": "live", "tag": "live-e5", "event_id": 5,
                    "title": "Arsenal 0 – 1 Chelsea", "body": "23'", "progress": "23/90", "marks": [[23, 1]]}
    # As strings, as FCM takes them.
    data = livecards.fcm.data_message("t" * 40, {**made, "players": ["Bukayo Saka · Started"]}, livecards.LIVE_TTL)
    assert (data["data"]["sides"], data["data"]["marks"], data["data"]["players"], data["android"]) == \
        ('[["ARS","0"],["CHE","1"]]', "[[23,1]]", '["Bukayo Saka · Started"]', {"priority": "normal", "ttl": "60s", "collapse_key": "live-e5"})

    livecards.polled(EVENT, snap(0, 1, "23'", goals=[goal]))  # nothing new
    cards.clock.now += 5
    livecards.polled(EVENT, snap(0, 1, "24'", goals=[goal]))  # only the clock: it waits
    assert len(cards.sent) == 1
    livecards.polled(EVENT, snap(1, 1, "24'", goals=[goal]))  # a goal goes at once
    assert len(cards.sent) == 2 and cards.sent[-1][2]["sides"] == [["ARS", "1"], ["CHE", "1"]]
    cards.clock.now += 10
    livecards.polled(EVENT, snap(1, 1, "25'"))
    assert len(cards.sent) == 2
    cards.clock.now += livecards.THROTTLE
    livecards.polled(EVENT, snap(1, 1, "25'"))  # the clock that waited goes with a later poll
    assert len(cards.sent) == 3 and cards.sent[-1][2]["status"] == "25'"


def test_a_games_end_goes_once_and_only_after_its_card(fake_fcm, redis, cards):  # noqa: F811
    livecards.polled(EVENT, snap(0, 0, "FT", status="final"))  # it never had a card out
    assert cards.sent == []
    livecards.polled(EVENT, snap(2, 1, "90+3'"))
    livecards.polled(EVENT, snap(2, 1, "FT", status="final"))
    livecards.polled(EVENT, snap(2, 1, "FT", status="final"))
    assert [(e, kind, made) for e, kind, made in cards.sent[1:]] == [(5, "end", None)]
    # A game the schedule closed without a final poll (structured.finish) has its end too.
    livecards.polled(SimpleNamespace(**{**vars(EVENT), "id": 6}), snap(0, 0, "10'"))
    livecards.ended([6, 7])
    assert [(e, kind) for e, kind, _ in cards.sent[2:]] == [(6, "live"), (6, "end")]


def test_without_fcm_nothing_is_decided_or_sent(no_fcm, cards, monkeypatch):  # noqa: F811
    monkeypatch.setattr(bus, "_sync", None)  # Redis isn't even asked
    livecards.polled(EVENT, snap(1, 0, "10'"))
    livecards.ended([5])
    assert cards.sent == []


def test_a_failing_card_never_fails_the_poll(fake_fcm, cards, monkeypatch, caplog):  # noqa: F811
    monkeypatch.setattr(bus, "sync_redis", lambda: (_ for _ in ()).throw(ConnectionError("Redis is down")))
    livecards.polled(EVENT, snap(1, 0, "10'"))
    livecards.polled(EVENT, snap(1, 0, "FT", status="final"))
    assert cards.sent == [] and len([r for r in caplog.records if r.name == "sports_follow.livecards"]) == 2


def test_a_lobby_has_no_sides_and_no_bar(fake_fcm, redis, cards):  # noqa: F811
    lobby = Snapshot("live", "GodLike lead · 87 pts", "After match 3 of 6", {"kind": "battle_royale", "standings": [{"id": "a", "name": "GodLike", "place": 1}]})
    livecards.polled(SimpleNamespace(id=9, sport="BGMI", competition="BGIS", key="liquipedia_esports:x", title="BGIS Finals"), lobby)
    [(_, _, made)] = cards.sent
    assert (made["sport"], made["colour"], made["sides"], made["status"], made["progress"], made["marks"]) == ("bgmi", "#166534", [], "After match 3 of 6", "", [])
    assert livecards.fcm.data_message("t" * 40, made, 60)["data"]["live"] == "1"
