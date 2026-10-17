"""League games (league.py): the big results and big games coming up of the competitions the running sources
cover, whoever follows them, ranked by each sport. Sources, Redis and the events table are made up here."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from sports_follow import league, sports
from sports_follow.adapters import Fixture
from sports_follow.sports import Notable

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


@pytest.fixture
def redis(monkeypatch):
    """Redis as a dict: what each source's last read left."""
    store: dict[str, str] = {}
    fake = SimpleNamespace(get=store.get, set=lambda key, value, ex=None: store.__setitem__(key, value))
    monkeypatch.setattr(league.bus, "sync_redis", lambda: fake)
    monkeypatch.setattr(league, "_served", {})
    return store


class Source:
    """A made-up source that lists its competitions' games, and the days it was asked for."""

    def __init__(self, system: str, sports: tuple[str, ...], games: list[Fixture] | None = None):
        self.system, self.sports, self.games, self.asked = system, sports, games or [], []

    def schedule(self, start: date, end: date) -> list[Fixture]:
        self.asked.append((start, end))
        return self.games


@pytest.fixture
def sources(monkeypatch):
    """Only made-up sources run: a football one, and two esports ones, of two games and of one."""
    made = SimpleNamespace(pitch=Source("pitch", ("football",)), arena=Source("arena", ("valorant", "lol")), lobby=Source("lobby", ("pubg",)))
    monkeypatch.setattr(league, "ADAPTERS", {s.system: s for s in vars(made).values()})
    return made


def fixture(source_id: str, status: str, start: datetime | None, **extra) -> Fixture:
    return Fixture(**{"source_id": source_id, "title": f"Game {source_id}", "competition": "League", "start_utc": start, "venue": None, "status": status,
                      "team_ids": ["1", "2"], "locator": {}, **extra})


class Db:
    """The events table, as key -> id."""

    def __init__(self, events: dict[str, int] | None = None):
        self.events = events or {}

    def execute(self, query):
        wanted = set(query.compile().params["key_1"])
        return [(k, v) for k, v in self.events.items() if k in wanted]


def test_a_read_keeps_what_its_sport_ranks_finished_or_to_come(sources, redis, monkeypatch):
    """Three days back to a fortnight on; a game on now is Live now's, a postponed one is nobody's, and one its
    sport doesn't rank isn't kept."""
    sources.pitch.games = [
        fixture("1", "final", NOW - timedelta(days=1), result={"home": {"score": "2"}, "away": {"score": "1"}}),
        fixture("2", "scheduled", NOW + timedelta(days=2)),
        fixture("3", "live", NOW),
        fixture("4", "postponed", NOW + timedelta(days=1)),
        fixture("5", "scheduled", NOW + timedelta(days=3), detail="small"),
        fixture("6", "scheduled", None),
    ]
    monkeypatch.setattr(sports.BY_KEY["football"], "notable", lambda f: None if f.detail == "small" else Notable(40, word="2–1" if f.status == "final" else "", why="Leaders"))
    assert league.read("pitch", NOW) == 2
    assert sources.pitch.asked == [(date(2026, 10, 13), date(2026, 10, 30))]
    kept = json.loads(redis["league:games:pitch"])
    assert kept["as_of"] == NOW.isoformat()
    assert [(g["key"], g["status"], g["word"], g["why"], g["sport"]) for g in kept["games"]] == [("pitch:1", "final", "2–1", "Leaders", "football"), ("pitch:2", "scheduled", "", "Leaders", "football")]


def test_each_sport_reads_its_first_source_that_lists_a_schedule_or_every_one_side_by_side(sources, monkeypatch):
    monkeypatch.setitem(league.ADAPTERS, "second", Source("second", ("football",)))
    monkeypatch.setitem(league.ADAPTERS, "no_schedule", SimpleNamespace(system="no_schedule", sports=("cricket",)))
    assert [a.system for a in league.sources()] == ["pitch", "arena", "lobby"]  # a source of two games, once


def test_a_sport_turned_off_isnt_read(sources, monkeypatch):
    monkeypatch.setattr(sports, "ENABLED", tuple(s for s in sports.ENABLED if not s.group))
    assert [a.system for a in league.sources()] == ["pitch"]
    monkeypatch.setattr(sports, "ENABLED", tuple(s for s in sports.ALL if s.key != "valorant"))
    assert [a.system for a in league.sources()] == ["pitch", "arena", "lobby"]  # its other game still runs


def test_a_source_of_several_games_reads_each_as_its_sport_and_a_game_turned_off_isnt_served(sources, redis, monkeypatch):
    sources.arena.games = [fixture("v", "final", NOW - timedelta(hours=2), sport="valorant", tier=3, detail="Grand final · Bo5",
                                   result={"teams": [{"id": "1", "name": "SEN", "score": 3, "winner": True}, {"id": "2", "name": "FNC", "score": 1, "winner": False}]}),
                           fixture("l", "scheduled", NOW + timedelta(days=1), sport="lol", tier=2, detail="Semi-final · Bo5")]
    assert league.read("arena", NOW) == 2
    assert [(g["key"], g["sport"], g["why"]) for g in json.loads(redis["league:games:arena"])["games"]] == [("arena:v", "valorant", "Grand final"), ("arena:l", "lol", "Semi-final")]
    monkeypatch.setattr(sports, "ENABLED", tuple(s for s in sports.ALL if s.key != "valorant"))
    served, _ = league.offer(Db(), NOW)
    assert [g["key"] for g in served["results"]["games"]] == [] and [g["key"] for g in served["coming"]["games"]] == ["arena:l"]


def keep(redis: dict, system: str, games: list[dict], as_of: datetime = NOW) -> None:
    redis[f"league:games:{system}"] = json.dumps({"as_of": as_of.isoformat(), "games": games})


def game(key: str, status: str, start: datetime, rank: int, sport: str = "football") -> dict:
    return {"key": key, "sport": sport, "title": key, "competition": "League", "detail": None, "start_utc": start.isoformat(), "status": status,
            "rank": rank, "word": "", "line": "", "why": ""}


def test_results_since_yesterday_biggest_first_then_newest_of_every_sport(sources, redis):
    keep(redis, "pitch", [game("pitch:old", "final", NOW - timedelta(days=3), 99), game("pitch:a", "final", NOW - timedelta(hours=30), 40),
                          game("pitch:b", "final", NOW - timedelta(hours=2), 40), game("pitch:c", "final", NOW - timedelta(hours=20), 80)])
    keep(redis, "arena", [game("arena:d", "final", NOW - timedelta(hours=5), 60, "valorant")])
    served, stale = league.offer(Db({"pitch:b": 7}), NOW)
    assert stale == ["lobby"]  # never read
    assert served["results"]["day"] is None
    assert [(g["key"], g["event_ids"]) for g in served["results"]["games"]] == [("pitch:c", []), ("arena:d", []), ("pitch:b", [7]), ("pitch:a", [])]
    assert "status" not in served["results"]["games"][0] and "group" not in served["results"]["games"][0]


def test_a_game_says_its_match_page_where_its_source_reads_one_game_or_its_an_event_here(sources, redis):
    sources.pitch.match = lambda source_id: None  # Pitch can read a game on its own; Arena can't
    keep(redis, "pitch", [game("pitch:a", "final", NOW - timedelta(hours=2), 40), game("pitch:b", "scheduled", NOW + timedelta(days=1), 40)])
    keep(redis, "arena", [game("arena:c", "final", NOW - timedelta(hours=3), 60, "valorant"), game("arena:d", "final", NOW - timedelta(hours=4), 50, "valorant")])
    served, _ = league.offer(Db({"arena:d": 9}), NOW)
    games = [*served["results"]["games"], *served["coming"]["games"]]
    assert {g["key"]: g["match"] for g in games} == {"pitch:a": "pitch/a", "pitch:b": "pitch/b", "arena:c": None, "arena:d": "arena/d"}


def test_the_sports_take_turns_and_the_esports_games_one_between_them(sources, redis):
    """A busy esports week doesn't fill the results: each sport's biggest, the biggest of those first, then
    each one's next biggest; the esports games take one turn between them, which goes round the games, so
    neither one game's season nor many games take them all. A read from before each game was a sport (its
    sport "esports") is in esports' turn."""
    hours = lambda n: NOW - timedelta(hours=n)  # noqa: E731
    keep(redis, "arena", [game("lol:1", "final", hours(1), 90, "lol"), game("lol:2", "final", hours(2), 85, "lol"),
                          game("lol:3", "final", hours(3), 80, "lol"), game("val:1", "final", hours(4), 60, "valorant")])
    keep(redis, "lobby", [{**game("pubg:1", "final", hours(5), 55, "esports"), "group": "PUBG"}])
    keep(redis, "pitch", [game("pitch:1", "final", hours(6), 50), game("pitch:2", "final", hours(7), 40)])
    served, _ = league.offer(Db(), NOW)
    assert [g["key"] for g in served["results"]["games"]] == ["lol:1", "pitch:1", "val:1", "pitch:2", "pubg:1", "lol:2", "lol:3"]


def test_with_none_since_yesterday_the_latest_day_that_had_results(sources, redis):
    keep(redis, "pitch", [game("pitch:a", "final", datetime(2026, 10, 12, 19, tzinfo=timezone.utc), 40), game("pitch:b", "final", datetime(2026, 10, 13, 15, tzinfo=timezone.utc), 40),
                          game("pitch:c", "final", datetime(2026, 10, 13, 20, tzinfo=timezone.utc), 65), game("pitch:next", "scheduled", NOW + timedelta(days=1), 40)])
    served, _ = league.offer(Db(), NOW)
    assert served["results"] == {"day": "2026-10-13", "games": [served["results"]["games"][0], served["results"]["games"][1]]}
    assert [g["key"] for g in served["results"]["games"]] == ["pitch:c", "pitch:b"]


def test_since_yesterday_is_where_the_fan_is(sources, redis):
    """At 23:00 UTC on the 15th it's already the 16th in India (UTC+5:30): a game of the 14th is two days ago
    there, and a game of 19:00 UTC on the 15th is "yesterday"."""
    late = datetime(2026, 10, 15, 23, tzinfo=timezone.utc)
    keep(redis, "pitch", [game("pitch:14th", "final", datetime(2026, 10, 14, 12, tzinfo=timezone.utc), 40), game("pitch:15th", "final", datetime(2026, 10, 15, 19, tzinfo=timezone.utc), 40)], as_of=late)
    assert [g["key"] for g in league.offer(Db(), late)[0]["results"]["games"]] == ["pitch:15th", "pitch:14th"]
    assert [g["key"] for g in league.offer(Db(), late, tz=330)[0]["results"]["games"]] == ["pitch:15th"]
    # And with only the 14th's, that's the latest day there.
    keep(redis, "pitch", [game("pitch:14th", "final", datetime(2026, 10, 14, 12, tzinfo=timezone.utc), 40)], as_of=late)
    assert league.offer(Db(), late, tz=330)[0]["results"]["day"] == "2026-10-14"


def test_coming_up_is_the_next_fortnight_biggest_first_then_soonest(sources, redis):
    keep(redis, "pitch", [game("pitch:started", "scheduled", NOW - timedelta(minutes=5), 90), game("pitch:a", "scheduled", NOW + timedelta(days=5), 40),
                          game("pitch:b", "scheduled", NOW + timedelta(days=1), 40), game("pitch:c", "scheduled", NOW + timedelta(days=9), 85),
                          game("pitch:far", "scheduled", NOW + timedelta(days=15), 99)])
    served, _ = league.offer(Db(), NOW)
    assert [g["key"] for g in served["coming"]["games"]] == ["pitch:c", "pitch:b", "pitch:a"]


def test_each_list_is_cut_at_its_most(sources, redis, monkeypatch):
    monkeypatch.setattr(league, "RESULTS", 2)
    monkeypatch.setattr(league, "COMING", 1)
    keep(redis, "pitch", [game(f"pitch:r{i}", "final", NOW - timedelta(hours=i + 1), 40 + i) for i in range(4)] + [game(f"pitch:c{i}", "scheduled", NOW + timedelta(days=i + 1), 40) for i in range(3)])
    served, _ = league.offer(Db(), NOW)
    assert [g["key"] for g in served["results"]["games"]] == ["pitch:r3", "pitch:r2"]
    assert [g["key"] for g in served["coming"]["games"]] == ["pitch:c0"]


def test_a_list_is_cut_after_the_sports_take_turns_so_a_smaller_sport_keeps_its_share(sources, redis, monkeypatch):
    monkeypatch.setattr(league, "COMING", 4)
    keep(redis, "arena", [game(f"arena:{i}", "scheduled", NOW + timedelta(hours=i + 1), 90 - i, "valorant") for i in range(6)])
    keep(redis, "pitch", [game("pitch:1", "scheduled", NOW + timedelta(days=3), 40)])
    served, _ = league.offer(Db(), NOW)
    assert [g["key"] for g in served["coming"]["games"]] == ["arena:0", "pitch:1", "arena:1", "arena:2"]


def test_a_read_hours_old_is_served_and_read_again_a_days_old_one_isnt_served(sources, redis):
    keep(redis, "pitch", [game("pitch:a", "final", NOW - timedelta(hours=3), 40)], as_of=NOW - timedelta(seconds=league.FRESH + 60))
    keep(redis, "arena", [game("arena:b", "final", NOW - timedelta(hours=3), 40, "valorant")], as_of=NOW - timedelta(seconds=league.SERVE + 60))
    keep(redis, "lobby", [], as_of=NOW - timedelta(minutes=5))
    served, stale = league.offer(Db(), NOW)
    assert stale == ["pitch", "arena"]
    assert [g["key"] for g in served["results"]["games"]] == ["pitch:a"]


def test_a_time_zones_fans_share_a_ranking_until_a_source_is_read_again_and_events_are_looked_up_every_time(sources, redis, monkeypatch):
    monkeypatch.setattr(league, "datetime", SimpleNamespace(now=lambda tz=None: NOW, fromisoformat=datetime.fromisoformat))
    keep(redis, "pitch", [game("pitch:a", "final", NOW - timedelta(hours=3), 40)])
    looked = []

    class Counting(Db):
        def execute(self, query):
            looked.append(1)
            return super().execute(query)

    ranked = []
    monkeypatch.setattr(league, "_ranked", lambda games, now, tz, rank=league._ranked: ranked.append(tz) or rank(games, now, tz))
    first, _ = league.cached(Counting())
    assert [g["event_ids"] for g in first["results"]["games"]] == [[]]
    # A follow a moment ago made the game's event: the next look, from the same ranking, has it.
    again, _ = league.cached(Counting({"pitch:a": 7}))
    assert [g["event_ids"] for g in again["results"]["games"]] == [[7]] and ranked == [0] and len(looked) == 2
    league.cached(Counting(), tz=330)  # another time zone is ranked for itself
    keep(redis, "pitch", [game("pitch:b", "final", NOW - timedelta(hours=1), 40)], as_of=NOW + timedelta(seconds=1))
    later, _ = league.cached(Counting())
    assert [g["key"] for g in later["results"]["games"]] == ["pitch:b"] and ranked == [0, 330, 0]


# ---------------------------------------------------------------- each sport says how big a game is


def football(status: str, detail: str | None = "Matchday 7", table: list | None = None, score: tuple[int, int] | None = None, pens: tuple[int, int] | None = None) -> Fixture:
    result = None
    if score:
        home, away = ({"id": "1", "name": "Arsenal", "score": str(score[0]), "winner": score[0] > score[1] or None}, {"id": "2", "name": "Chelsea", "score": str(score[1]), "winner": score[1] > score[0] or None})
        if pens:
            home.update(pens=pens[0], winner=pens[0] > pens[1])
            away.update(pens=pens[1], winner=pens[1] > pens[0])
        result = {"home": home, "away": away}
    return fixture("9", status, NOW, detail=detail, result=result, facts={"table": table} if table else {})


def test_football_ranks_by_stage_then_the_table_and_a_result_by_what_happened():
    rank = sports.BY_KEY["football"].notable
    assert rank(football("scheduled", "Final")) == Notable(95, why="Final")
    assert rank(football("scheduled", "Semi-finals", table=[1, 2])) == Notable(85, why="Semi-final")
    assert rank(football("scheduled", table=[1, 3])) == Notable(80, why="1st v 3rd")
    assert rank(football("scheduled", table=[5, 7])) == Notable(65, why="5th v 7th")
    assert rank(football("scheduled", table=[1, 12])) == Notable(55, why="Leaders")
    assert rank(football("scheduled", table=[9, 14])) == Notable(40)
    assert rank(football("scheduled")) == Notable(40)
    assert rank(football("final", table=[18, 3], score=(2, 1))) == Notable(55, word="2–1", why="Upset")
    assert rank(football("final", table=[2, 6], score=(4, 1))) == Notable(75, word="4–1", why="2nd v 6th")
    assert rank(football("final", table=[9, 14], score=(4, 3))) == Notable(50, word="4–3", why="7 goals")
    assert rank(football("final", "Final", score=(1, 1), pens=(4, 2))) == Notable(100, word="1–1", why="Final")


def cricket(summary: str, national: bool = True, detail: str = "2nd T20I", competition: str = "West Indies tour of India", winner: int | None = 1, **facts) -> Fixture:
    teams = [{"id": "1", "name": "India", "winner": winner == 0}, {"id": "2", "name": "West Indies", "winner": winner == 1}]
    return fixture("9", "final", NOW, competition=competition, detail=detail, result={"summary": summary, "teams": teams}, facts={"national": True, **facts} if national else facts)


def test_cricket_ranks_internationals_and_finals_and_a_close_finish_says_so():
    rank = sports.BY_KEY["cricket"].notable
    assert rank(cricket("West Indies won by 6 wickets (with 9 balls remaining)")) == Notable(55, word="By 6 wickets", line="West Indies beat India")
    assert rank(cricket("West Indies won by 3 runs", national=False, competition="CSA T20 Challenge", detail="31st Match")) == Notable(55, word="By 3 runs", line="West Indies beat India", why="Thriller")
    assert rank(cricket("West Indies won by 5 wickets (with 2 balls remaining)")) == Notable(70, word="By 5 wickets", line="West Indies beat India", why="Last over")
    assert rank(cricket("Match tied (West Indies won the Super Over)", super_over=True)) == Notable(70, word="Super over", line="West Indies beat India", why="Super over")
    assert rank(cricket("India won by 57 runs", winner=0, competition="Triangular Series Final (UAE)", detail="Final")) == Notable(95, word="By 57 runs", line="India beat West Indies", why="Final")
    assert rank(cricket("India won by 9 runs", winner=0, competition="Big Bash Finals Series", detail="12th Match")) == Notable(55, word="By 9 runs", line="India beat West Indies")
    assert rank(cricket("No result", winner=None)) is None
    assert rank(fixture("9", "scheduled", NOW, competition="Indian Premier League", detail="Qualifier 1")) == Notable(70, why="Playoffs")


def test_a_cricket_match_to_come_whose_sides_are_both_still_to_be_decided_isnt_big_until_they_are():
    rank = sports.BY_KEY["cricket"].notable
    final = fixture("9", "scheduled", NOW + timedelta(days=7), competition="Triangular Series Final (UAE)", detail="Final", team_names=["TBC", "TBC"])
    assert rank(final) is None
    final.team_names = ["India", "TBC"]  # one semi-final is over
    assert rank(final) == Notable(95, why="Final")
    final.team_names = ["India", "Sri Lanka"]
    assert rank(final) == Notable(95, why="Final")


@pytest.mark.parametrize(("sides", "said"), [
    (["TBC", "TBC"], True), (["TBA", "tbd"], True), (["", " "], True), (["To be confirmed", "TBC"], True),
    (["India", "TBC"], False), (["TBC Warriors", "TBC"], False), ([], False),
])
def test_a_side_still_to_be_decided_is_read_from_its_name(sides, said):
    assert sports.base.undecided(sides) is said


def series(tier: int, detail: str, score: tuple[int, int] | None = None, forfeit: bool = False) -> Fixture:
    result = {"teams": [{"id": "1", "name": "Vitality", "score": score[0], "winner": score[0] > score[1]}, {"id": "2", "name": "FaZe", "score": score[1], "winner": score[1] > score[0]}],
              "forfeit": forfeit, "draw": False} if score else None
    return fixture("9", "final" if score else "scheduled", NOW, detail=detail, result=result, tier=tier)


def test_esports_ranks_the_big_events_series_by_their_stage_and_a_series_that_went_the_distance():
    rank = sports.BY_KEY["esports"].notable
    assert rank(series(3, "Grand final · Bo5")) == Notable(90, why="Grand final")
    assert rank(series(3, "Upper Bracket Final · Bo3", (2, 1))) == Notable(83, word="2–1", line="Vitality beat FaZe", why="Bracket final")
    assert rank(series(2, "Group A · Bo3", (2, 0))) == Notable(40, word="2–0", line="Vitality beat FaZe", why="Tier A")
    assert rank(series(2, "Bo3", (1, 2))) == Notable(48, word="2–1", line="FaZe beat Vitality", why="Decider")  # the winner's score first
    assert rank(series(1, "Grand final · Bo5")) is None  # a smaller event's
    assert rank(series(3, "Bo3", (2, 0), forfeit=True)) is None


def test_esports_ranks_a_big_events_lobby_by_its_tier_and_names_its_winner():
    rank = sports.BY_KEY["esports"].notable
    lobby = fixture("t:2026-10-15:1", "final", NOW, team_ids=["TWIS", "NAVI", "FUR"], tier=3,
                    result={"teams": 16, "standings": [{"id": "TWIS", "name": "TWIS", "place": 1, "points": 87.0}]})
    assert rank(lobby) == Notable(55, word="TWIS", line="Won the lobby · 87 pts · 16 teams", why="Tier S")
    lobby.tier = 1
    assert rank(lobby) is None


def test_a_sport_whose_sources_list_no_schedule_ranks_none():
    assert sports.OTHER.notable(fixture("9", "final", NOW)) is None
    assert sports.BY_KEY["basketball"].notable(fixture("9", "final", NOW)) is None


@pytest.mark.parametrize(("text", "said"), [
    ("Grand final: TBD vs TBD", (3, "Grand final")), ("Upper Bracket Final · Bo3", (2, "Bracket final")), ("Semi-finals", (2, "Semi-final")),
    ("Semifinal 2: WE vs BRO", (2, "Semi-final")), ("Quarter-finals", (1, "Quarter-final")), ("Triangular Series Final (UAE)", (3, "Final")),
    ("Qualifier 1", (1, "Playoffs")), ("Eliminator", (1, "Playoffs")), ("Matchday 7", (0, "")), ("Group Stage", (0, "")), ("1st T20I", (0, "")),
    ("Elimination Match: AUR vs NIP", (0, "")), (None, (0, "")),
])
def test_a_stage_is_read_from_its_words(text, said):
    assert sports.base.stage(text) == said
