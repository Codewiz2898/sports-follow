"""What a source's roster run does to the registry's real rows (registry.import_roster): after a complete
run, rows it didn't see again are kept for its grace, and rows holding an id of a shape the source no
longer gives go at once. DELETE … WHERE on JSONB needs Postgres itself, so these run against a throwaway
database at alembic head and are skipped without one:

    docker compose exec postgres createdb -U sports sports_follow_test
    DATABASE_URL=postgresql+psycopg://sports:sports@127.0.0.1:5433/sports_follow_test .venv/bin/alembic upgrade head
    SPORTS_FOLLOW_TEST_DATABASE_URL=postgresql+psycopg://sports:sports@127.0.0.1:5433/sports_follow_test .venv/bin/python -m pytest tests/test_registry_db.py

Every test empties the table first.
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from sports_follow import registry
from sports_follow.adapters import Adapter, PlayerRef, Roster, Rostered
from sports_follow.models import Athlete

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")


@pytest.fixture
def db(monkeypatch):
    engine = create_engine(URL)
    with engine.begin() as c:
        c.execute(text("TRUNCATE athlete, athlete_override RESTART IDENTITY CASCADE"))
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
    s = made()
    yield s
    s.close()
    engine.dispose()


def pro(key: str, athlete_id: str, name: str) -> Rostered:
    return Rostered(key=key, ref=PlayerRef("fake_source", athlete_id, name, ["FLC"], ["FLC"], league="fake"))


class Source(Adapter):
    """A source with a roster that used to give its athletes ids of one shape ("account.1") and now gives another."""

    system, sports, roster_prefix = "fake_source", ("pubg",), "fake:"
    id_pattern = re.compile(r"[a-z_]{1,20}")
    provider = SimpleNamespace(name="Fake")

    def roster(self, keep, wanted=None):
        keep([pro("fake:new", "flc_tgltn", "TGLTN")])
        return Roster(complete=True, grace=timedelta(days=21))


def test_rows_with_an_id_the_source_no_longer_gives_go_with_the_next_complete_run(db):
    # BHA-216: TGLTN's two rows (an event account each) stayed in search beside his new one for the grace.
    with registry.session() as s:
        registry.upsert(s, registry.roster_rows(Source(), [pro("fake:a1", "account.1", "TGLTN"), pro("fake:a2", "account.2", "TGLTN"), pro("fake:old", "flc_shrimzy", "Shrimzy")]))
    assert registry.import_roster(Source()) == {"pubg": 1}
    # Shrimzy wasn't seen this run, but his id is of today's shape: kept for the grace, as before.
    assert sorted(db.scalars(select(Athlete.qid))) == ["fake:new", "fake:old"]


def test_a_star_is_found_by_the_names_fans_use(db, monkeypatch):
    # "leo messi" and "msd" found nothing: Wikidata keeps their aliases under "mul" (BUG-145). Dhoni with six of
    # his 18 here, the ones fans type most (which eight of all 18 are kept: test_registry, MAX_ALIASES).
    from sports_follow import search
    from tests.test_registry import DHONI, MESSI, STARS_NAMES, stars

    six = {"Mahendra Singh Dhoni", "M.S. Dhoni", "MSD", "Mahi", "Thala", "Captain Cool"}
    names = [r for r in STARS_NAMES if r["p"] != DHONI or r["kind"] == "label" or r["n"] in six]
    rows = [stars(monkeypatch, "football", MESSI, names), stars(monkeypatch, "cricket", DHONI, names)]
    with registry.session() as s:
        registry.upsert(s, rows, photos=False)
    for query, qid in [("leo messi", "Q615"), ("msd", "Q470774"), ("mahendra singh dhoni", "Q470774"), ("m.s. dhoni", "Q470774")]:
        found = search.run(db, query, may_ask=lambda: False)["results"]
        assert qid in [r.get("qid") for r in found], query
