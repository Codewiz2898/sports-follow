"""Each esports game a sport of its own (BHA-266), against a throwaway database at alembic head: the migration
that moves the registry's esports pros, and what hangs off them, to their games, and back; and an operator's
decision kept under "esports" (from before) standing for every game. Skipped without one:

    docker compose exec postgres createdb -U sports sports_follow_test
    DATABASE_URL=postgresql+psycopg://sports:sports@127.0.0.1:5433/sports_follow_test .venv/bin/alembic upgrade head
    SPORTS_FOLLOW_TEST_DATABASE_URL=postgresql+psycopg://sports:sports@127.0.0.1:5433/sports_follow_test .venv/bin/python -m pytest tests/test_esports_games_db.py

Every test empties the tables first.
"""

from __future__ import annotations

import importlib.util
import json
import os
from contextlib import contextmanager
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from sports_follow import players, registry
from sports_follow.adapters import KNOWN, PlayerRef, Roster, Rostered
from sports_follow.models import Athlete

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

MIGRATION = Path(__file__).parent.parent / "migrations/versions/d85acb8b68e0_esports_games_as_sports.py"


@pytest.fixture
def engine(monkeypatch):
    engine = create_engine(URL)
    with engine.begin() as c:
        c.execute(text("TRUNCATE athlete, athlete_override, athlete_unmatch, starter, player, event, control RESTART IDENTITY CASCADE"))
    made = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def unit():
        s = made()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    monkeypatch.setattr(registry, "session", unit)
    yield engine
    engine.dispose()


def migrate(engine, step: str) -> None:
    spec = importlib.util.spec_from_file_location("esports_games_as_sports", MIGRATION)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as c, Operations.context(MigrationContext.configure(c)):
        getattr(migration, step)()


def athlete(qid: str, league: str | None, country: str | None = None, sport: str = "esports") -> dict:
    return {"qid": qid, "sport": sport, "name": qid, "aliases": [], "search_text": f" {qid} |", "birth_date": None, "country": country, "teams": [],
            "league": league, "title": None, "ids": {}, "image": None, "sitelinks": 0, "current": True}


def test_the_migration_moves_each_pro_and_whats_theirs_to_their_game_and_back(engine):
    with engine.begin() as c:
        c.execute(Athlete.__table__.insert(), [athlete("pandascore:1", "valorant"), athlete("pandascore:2", "cs-go"), athlete("pandascore:3", "kog"),
                                               athlete("lp:pubgm:4", "pubgmobile", "India"), athlete("lp:pubgm:5", "pubgmobile", "China"),
                                               athlete("lp:ff:6", "freefire"), athlete("pubg:flc_tgltn", "pubg"), athlete("op:7", None),
                                               athlete("Q1", None, sport="cricket")])
        c.execute(text("""INSERT INTO athlete_override (qid, sport, action, name, ids) VALUES
            ('pandascore:1', 'esports', 'keep', 'one', '{}'), ('pandascore:99', 'esports', 'remove', 'gone', '{"pandascore_esports": "99"}')"""))
        c.execute(text("INSERT INTO athlete_unmatch (qid, sport, person, name) VALUES ('lp:pubgm:4', 'esports', NULL, 'four'), ('pandascore:2', 'esports', 'Q5', 'two')"))
        c.execute(text("INSERT INTO starter (qid, sport, position) VALUES ('pandascore:2', 'esports', 1)"))
        c.execute(text("""INSERT INTO player (id, slug, name, sport, status, teams) VALUES
            (1, 'a', 'A', 'Esports', 'active', '[]'), (2, 'b', 'B', 'Esports', 'active', '[]'), (3, 'c', 'C', 'Esports', 'active', '[]'), (4, 'd', 'D', 'CS2', 'active', '[]')"""))
        binding = "INSERT INTO source_binding (player_id, purpose, adapter, locator, confidence, priority, health) VALUES (:p, 'fixtures', :a, CAST(:l AS jsonb), 1, 10, '{}')"
        c.execute(text(binding), [{"p": 1, "a": "pandascore_esports", "l": json.dumps({"game": "King of Glory", "league": "kog"})},
                                  {"p": 2, "a": "liquipedia_esports", "l": json.dumps({"league": "freefire"})}])
        c.execute(text("INSERT INTO event (id, key, sport, competition, title, status, participants) VALUES (1, 'x:1', 'Esports', '', 'A v B', 'final', '[]'), (2, 'x:2', 'Esports', '', 'C v D', 'final', '[]')"))
        c.execute(text("INSERT INTO event_player (event_id, player_id) VALUES (1, 1), (2, 3)"))
        c.execute(text("""INSERT INTO control (key, value) VALUES ('sports_off', '["esports", "tennis"]')"""))

    def rows(sql: str) -> list[tuple]:
        with engine.begin() as c:
            return [tuple(r) for r in c.execute(text(sql))]

    migrate(engine, "upgrade")
    assert rows("SELECT qid, sport FROM athlete ORDER BY qid") == [
        ("Q1", "cricket"), ("lp:ff:6", "freefire"), ("lp:pubgm:4", "bgmi"), ("lp:pubgm:5", "pubgmobile"), ("op:7", "esports"),  # no game: the group
        ("pandascore:1", "valorant"), ("pandascore:2", "cs2"), ("pandascore:3", "hok"), ("pubg:flc_tgltn", "pubg")]
    # Decisions follow their athlete; a removed pro's (no row) stays the group's, every game's.
    assert rows("SELECT qid, sport FROM athlete_override ORDER BY qid") == [("pandascore:1", "valorant"), ("pandascore:99", "esports")]
    assert rows("SELECT qid, sport FROM athlete_unmatch ORDER BY qid") == [("lp:pubgm:4", "bgmi"), ("pandascore:2", "cs2")]
    assert rows("SELECT qid, sport FROM starter") == [("pandascore:2", "cs2")]
    # A player followed as "Esports" takes their game from their binding; their events follow them.
    assert rows("SELECT id, sport FROM player ORDER BY id") == [(1, "Honor of Kings"), (2, "Free Fire"), (3, "Esports"), (4, "CS2")]
    assert rows("SELECT id, sport FROM event ORDER BY id") == [(1, "Honor of Kings"), (2, "Esports")]
    off = rows("SELECT value FROM control")[0][0]
    assert "esports" not in off and {"tennis", "valorant", "bgmi", "pubg"} <= set(off) and len(off) == 17

    migrate(engine, "downgrade")
    assert rows("SELECT qid, sport FROM athlete WHERE sport <> 'cricket' ORDER BY qid") == [(q, "esports") for q in (
        "lp:ff:6", "lp:pubgm:4", "lp:pubgm:5", "op:7", "pandascore:1", "pandascore:2", "pandascore:3", "pubg:flc_tgltn")]
    assert {s for (s,) in rows("SELECT sport FROM athlete_override UNION SELECT sport FROM athlete_unmatch UNION SELECT sport FROM starter")} == {"esports"}
    assert rows("SELECT value FROM control")[0][0] == ["esports", "tennis"]


def test_a_pro_removed_as_esports_isnt_brought_back_in_any_game_and_one_kept_so_stays(engine):
    """A decision from before each game was a sport, kept under "esports", is every game's."""
    with registry.session() as db:
        db.execute(text("""INSERT INTO athlete_override (qid, sport, action, name, ids) VALUES
            ('pandascore:99', 'esports', 'remove', 'gone', '{"pandascore_esports": "99"}'), ('pandascore:5', 'esports', 'keep', 'kept', '{}')"""))
        registry.upsert(db, [athlete("pandascore:99", "valorant", sport="valorant"), athlete("pandascore:5", "cs-go", sport="cs2"), athlete("pandascore:6", "cs-go", sport="cs2")])
    with registry.session() as db:
        assert sorted(db.scalars(select(Athlete.qid))) == ["pandascore:5", "pandascore:6"]
        assert sorted(db.scalars(registry.operator_kept("cs2"))) == ["pandascore:5"]
        with pytest.raises(players.Refused, match="was removed"):
            players._refuse_removed(db, "pandascore:99", "valorant")


def test_a_run_of_some_games_keeps_and_drops_only_theirs(engine):
    """A source of several games read for one: its other games' rows are left alone."""
    adapter = KNOWN["pandascore_esports"]

    def pro(n: int, league: str) -> Rostered:
        return Rostered(key=f"pandascore:{n}", ref=PlayerRef("pandascore_esports", str(n), f"Pro {n}", ["1"], ["Team"], league=league))

    with registry.session() as db:
        registry.upsert(db, registry.roster_rows(adapter, [pro(1, "valorant"), pro(2, "valorant"), pro(3, "cs-go")]), photos=False)
        db.execute(text("UPDATE athlete SET updated_at = now() - interval '30 days'"))

    class OneGame(type(adapter)):
        def roster(self, keep, wanted=None):
            keep([pro(1, "valorant"), pro(4, "cs-go")])  # a page of both, as PandaScore's may hold
            return Roster(complete=True, leagues=["valorant"])

    assert registry.import_roster(OneGame(), ["valorant"]) == {"valorant": 1}
    with registry.session() as db:
        assert sorted(db.execute(select(Athlete.qid, Athlete.sport)).all()) == [("pandascore:1", "valorant"), ("pandascore:3", "cs2")]  # 2 gone; CS2's untouched
