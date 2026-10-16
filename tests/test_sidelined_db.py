"""Who's out (sidelined.py) against Postgres: each read of a made-up source's teams is compared with the
last, so a spell new to it that started lately is news that the player is out, and one that's over (off
its team's list, or past its end date) news that they're back, unless another spell still has them out.
Skipped without a throwaway database at alembic head (tests/test_corrections_db.py says how to make one).
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select, text

from sports_follow import db as db_module, retention, sidelined
from sports_follow.adapters import Absence, AdapterError, PlayerRef
from sports_follow.models import Athlete, Sidelined

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

NOW = datetime(2026, 10, 16, 6, 30, tzinfo=timezone.utc)
TODAY = NOW.date()
SYSTEM = "pitch"


def spell(source_id: str, athlete_id: str, name: str, since: date | None, until: date | None = None, team: str = "19", kind: str = "injury", born: str | None = None) -> Absence:
    ref = PlayerRef(system=SYSTEM, athlete_id=athlete_id, name=name, team_ids=[team], team_names=["Arsenal" if team == "19" else "Chelsea"], born=born)
    return Absence(source_id=source_id, ref=ref, kind=kind, what="Back Injury", since=since, until=until)


class Source:
    """A made-up source: who it has out, by team, at each read."""

    system, sport = SYSTEM, "football"

    def __init__(self):
        self.teams: dict[str, list[Absence]] = {"19": [], "20": []}
        self.failing = False

    def sidelined(self) -> dict[str, list[Absence]]:
        if self.failing:
            raise AdapterError("Sportmonks unreachable")
        return {team: list(spells) for team, spells in self.teams.items()}


@pytest.fixture
def source(monkeypatch):
    engine = create_engine(URL)
    with engine.begin() as c:
        c.execute(text("TRUNCATE sidelined, athlete RESTART IDENTITY CASCADE"))
    monkeypatch.setattr(db_module, "SessionLocal", db_module.sessionmaker(bind=engine, expire_on_commit=False))
    made = Source()
    monkeypatch.setattr(sidelined, "ADAPTERS", {SYSTEM: made})
    yield made
    engine.dispose()


def rows() -> dict[str, Sidelined]:
    with db_module.session() as db:
        return {r.source_id: r for r in db.scalars(select(Sidelined))}


def test_a_first_read_tells_only_the_spells_that_started_lately(source):
    source.teams["19"] = [spell("1", "4125", "William Saliba", date(2026, 7, 19), date(2026, 12, 11)),
                          spell("2", "7001", "Kai Havertz", TODAY - timedelta(days=2))]
    assert sidelined.read(SYSTEM, NOW) == {"listed": 2, "out": 1, "back": 0}
    saliba, havertz = rows()["1"], rows()["2"]
    assert saliba.out_at is None and saliba.ended_at is None  # under way since July: not news
    assert havertz.out_at == NOW and (havertz.athlete_id, havertz.team_id, havertz.name, havertz.team, havertz.sport) == ("7001", "19", "Kai Havertz", "Arsenal", "football")


def test_a_new_spell_is_news_and_one_off_its_teams_list_is_a_return(source):
    source.teams["19"] = [spell("1", "4125", "William Saliba", date(2026, 7, 19), date(2026, 12, 11))]
    sidelined.read(SYSTEM, NOW)
    later = NOW + timedelta(hours=10)
    source.teams["19"] = [spell("2", "7001", "Kai Havertz", None)]  # Saliba is off the list; Havertz, with no start date, is on
    assert sidelined.read(SYSTEM, later) == {"listed": 1, "out": 1, "back": 1}
    saliba, havertz = rows()["1"], rows()["2"]
    assert (saliba.ended_at, saliba.back_at, saliba.seen_at) == (later, later, NOW)
    assert havertz.out_at == later
    # Read again: nothing new.
    assert sidelined.read(SYSTEM, later + timedelta(hours=12)) == {"listed": 1, "out": 0, "back": 0}
    assert rows()["1"].back_at == later


def test_a_spell_past_its_end_date_is_a_return_and_one_put_back_is_news_again(source):
    source.teams["20"] = [spell("3", "7002", "Cole Palmer", TODAY - timedelta(days=20), TODAY, team="20")]
    sidelined.read(SYSTEM, NOW)
    tomorrow = NOW + timedelta(days=1)
    assert sidelined.read(SYSTEM, tomorrow)["back"] == 1  # still listed, but expected back yesterday
    assert rows()["3"].back_at == tomorrow
    source.teams["20"] = [spell("3", "7002", "Cole Palmer", TODAY - timedelta(days=20), TODAY + timedelta(days=10), team="20")]
    later = tomorrow + timedelta(hours=12)
    assert sidelined.read(SYSTEM, later)["out"] == 1  # a setback, after we said he was back
    palmer = rows()["3"]
    assert (palmer.out_at, palmer.ended_at, palmer.back_at, palmer.until) == (later, None, None, TODAY + timedelta(days=10))


def test_a_player_still_out_for_another_spell_isnt_back(source):
    source.teams["19"] = [spell("1", "4125", "William Saliba", date(2026, 7, 19)), spell("5", "4125", "William Saliba", TODAY, kind="suspension")]
    sidelined.read(SYSTEM, NOW)
    source.teams["19"] = [spell("5", "4125", "William Saliba", TODAY, kind="suspension")]
    assert sidelined.read(SYSTEM, NOW + timedelta(hours=10))["back"] == 0
    assert rows()["1"].ended_at is not None and rows()["1"].back_at is None


def test_a_spell_its_sport_doesnt_tell_doesnt_keep_a_return_untold(source):
    # Not registered for the competition (football tells no "other" spell) and injured: the injury ends.
    source.teams["19"] = [spell("1", "4125", "William Saliba", date(2026, 7, 19)), spell("6", "4125", "William Saliba", date(2026, 9, 3), kind="other")]
    sidelined.read(SYSTEM, NOW)
    source.teams["19"] = [spell("6", "4125", "William Saliba", date(2026, 9, 3), kind="other")]
    assert sidelined.read(SYSTEM, NOW + timedelta(hours=10))["back"] == 1


def test_a_read_that_fails_or_leaves_a_team_out_ends_nothing(source):
    source.teams["20"] = [spell("3", "7002", "Cole Palmer", TODAY - timedelta(days=20), team="20")]
    sidelined.read(SYSTEM, NOW)
    source.failing = True
    sidelined.read_all()  # logged; nothing changed
    assert rows()["3"].ended_at is None
    source.failing = False
    del source.teams["20"]  # relegated out of the plan's leagues: its team isn't read
    assert sidelined.read(SYSTEM, NOW + timedelta(hours=10))["back"] == 0
    assert rows()["3"].ended_at is None and rows()["3"].seen_at == NOW


def test_a_source_turned_off_isnt_read(source, monkeypatch):
    monkeypatch.setattr(sidelined, "ADAPTERS", {})
    assert sidelined.read(SYSTEM, NOW) == {} and sidelined.sources() == []


def test_how_well_known_is_by_the_sources_id_else_the_registrys_same_person(source):
    with db_module.session() as db:
        db.add_all([
            Athlete(qid="Q1", sport="football", name="William Saliba", search_text="", ids={SYSTEM: "4125"}, sitelinks=52),
            Athlete(qid="Q2", sport="football", name="Rodri", search_text="", birth_date=date(1996, 6, 22), birth_precision=11, ids={}, sitelinks=74),
            Athlete(qid="Q3", sport="football", name="Rodri", search_text="", birth_date=date(1990, 3, 6), birth_precision=11, ids={}, sitelinks=19),
            Athlete(qid="Q4", sport="football", name="Kai Havertz", search_text="", ids={SYSTEM: "9999"}, sitelinks=66),  # another player of the name, by id
        ])
        db.commit()
    source.teams["19"] = [spell("1", "4125", "W. Saliba", None), spell("6", "8001", "Rodri", None, born="1990-03-06"),
                          spell("7", "7001", "Kai Havertz", None), spell("8", "8002", "Nobody Known", None)]
    sidelined.read(SYSTEM, NOW)
    assert {r.name: r.fame for r in rows().values()} == {"W. Saliba": 52, "Rodri": 19, "Kai Havertz": 0, "Nobody Known": 0}


def test_a_spell_no_read_has_listed_for_a_fortnight_goes(source, monkeypatch):
    source.teams["19"] = [spell("1", "4125", "William Saliba", None)]
    sidelined.read(SYSTEM, NOW - timedelta(days=sidelined.KEEP_DAYS + 1))
    source.teams["19"] = [spell("2", "7001", "Kai Havertz", None)]
    sidelined.read(SYSTEM, NOW - timedelta(days=1))
    for name in ("_states", "_lines", "_cards"):
        monkeypatch.setattr(retention, name, lambda *args, **kwargs: 0)
    assert retention.run(count=True, now=NOW)["sidelined"] == 1
    assert retention.run(now=NOW)["sidelined"] == 1
    assert set(rows()) == {"2"}
