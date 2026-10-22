"""Each esports game a sport of its own (BHA-266), against a throwaway database at alembic head: the migration
that moves the registry's esports pros, and what hangs off them, to their games, and back; an operator's
decision kept under "esports" (from before) standing for every game; and a followed pro who changes teams
(BUG-200). Skipped without one:

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
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from sports_follow import bus, pipeline, players, registry, structured
from sports_follow.adapters import KNOWN, PlayerRef, Roster, Rostered, budget, pandascore
from sports_follow.models import Athlete, Event, EventPlayer, Player, PlayerCard, PlayerIdentity, SourceBinding

from .test_pipeline import FakeRedis

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

MIGRATION = Path(__file__).parent.parent / "migrations/versions/d85acb8b68e0_esports_games_as_sports.py"


@pytest.fixture
def engine(monkeypatch):
    engine = create_engine(URL)
    with engine.begin() as c:
        c.execute(text("TRUNCATE athlete, athlete_override, athlete_unmatch, starter, player, event, control, campaign RESTART IDENTITY CASCADE"))
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
        campaign = ("INSERT INTO campaign (slug, sport, title, lines, players, teams, games) "
                    "VALUES (:slug, 'esports', 'An event', '[]', CAST(:players AS jsonb), CAST(:teams AS jsonb), '[]')")
        c.execute(text(campaign), [{"slug": "worlds", "players": json.dumps(["pandascore:3"]), "teams": json.dumps([{"name": "T1", "game": "King of Glory"}])},
                                   {"slug": "ewc", "players": "[]", "teams": json.dumps([{"name": "T1", "game": "League of Legends"}, {"name": "SEN", "game": "Valorant"}])},
                                   {"slug": "soon", "players": "[]", "teams": "[]"}])

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
    # A campaign of one game's teams and players is that game's; one of several games', or of none yet, the group's.
    assert rows("SELECT slug, sport FROM campaign ORDER BY slug") == [("ewc", "esports"), ("soon", "esports"), ("worlds", "hok")]

    migrate(engine, "downgrade")
    assert rows("SELECT qid, sport FROM athlete WHERE sport <> 'cricket' ORDER BY qid") == [(q, "esports") for q in (
        "lp:ff:6", "lp:pubgm:4", "lp:pubgm:5", "op:7", "pandascore:1", "pandascore:2", "pandascore:3", "pubg:flc_tgltn")]
    assert {s for (s,) in rows("SELECT sport FROM athlete_override UNION SELECT sport FROM athlete_unmatch UNION SELECT sport FROM starter")} == {"esports"}
    assert rows("SELECT value FROM control")[0][0] == ["esports", "tennis"]
    assert {s for (s,) in rows("SELECT sport FROM campaign")} == {"esports"}


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


def test_a_search_filtered_to_esports_finds_its_games_that_run(engine, monkeypatch):
    from sports_follow import sports

    with registry.session() as db:
        registry.upsert(db, [athlete("pandascore:1", "cs-go", sport="cs2"), athlete("pandascore:2", "valorant", sport="valorant"),
                             athlete("op:3", None), {**athlete("Q4", None, sport="football"), "name": "pandascore:4", "search_text": " pandascore:4 |"}])
    monkeypatch.setattr(sports, "ENABLED", tuple(s for s in sports.ALL if s.key != "valorant"))
    with registry.session() as db:
        assert sorted(a.qid for a in registry.find(db, "pandascore", "esports")) == ["pandascore:1"]  # a game's, not one off, not football's
        assert sorted(a.qid for a in registry.find(db, "op", "esports")) == ["op:3"]  # a pro of no game: the group's own


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


def test_a_game_turned_off_isnt_polled_while_its_source_runs_for_another(engine, monkeypatch):
    """A CS2 pro's armed series and a Valorant pro's, from one source of both games: with Valorant turned off,
    only the CS2 one is polled (pipeline.due_work) and watched (structured._watching)."""
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    from sports_follow import bus, pipeline, sports, structured

    now = datetime.now(timezone.utc)
    with engine.begin() as c:
        c.execute(text("""INSERT INTO player (id, slug, name, sport, status, teams) VALUES (1, 'a', 'A', 'CS2', 'active', '[]'), (2, 'b', 'B', 'Valorant', 'active', '[]')"""))
        c.execute(text("""INSERT INTO player_card (player_id, status, card, version, freshness, is_live) VALUES
            (1, 'ready', '{}', 1, '{}', false), (2, 'ready', '{}', 1, '{}', false)"""))
        c.execute(text("INSERT INTO follow (fan_id, player_id, alert_rules) VALUES ('fan', 1, '{}'), ('fan', 2, '{}')"))
        c.execute(text("INSERT INTO player_identity (player_id, system, external_id, confidence) VALUES (1, 'arena', '11', 1), (2, 'arena', '22', 1)"))
        c.execute(text("""INSERT INTO event (id, key, sport, competition, title, status, participants, start_utc, live_binding) VALUES
            (1, 'arena:1', 'CS2', '', 'X v Y', 'armed', '[]', :start, '{"adapter": "arena", "locator": {"match": "1"}}'),
            (2, 'arena:2', 'Valorant', '', 'Z v W', 'armed', '[]', :start, '{"adapter": "arena", "locator": {"match": "2"}}')"""), {"start": now + timedelta(minutes=10)})
        c.execute(text("INSERT INTO event_player (event_id, player_id) VALUES (1, 1), (2, 2)"))
    made = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def unit():
        s = made()
        try:
            yield s
            s.commit()
        finally:
            s.close()

    monkeypatch.setattr(pipeline, "session", unit)
    monkeypatch.setattr(structured, "session", unit)
    monkeypatch.setattr(bus, "is_locked", lambda name: False)
    monkeypatch.setattr(pipeline, "rebuild_due", lambda card, now: False)
    monkeypatch.setitem(structured.ADAPTERS, "arena", SimpleNamespace(system="arena", sports=("cs2", "valorant")))
    assert sorted(pipeline.due_work(now).polls) == [1, 2]
    assert structured._watching(2) is not None
    monkeypatch.setattr(sports, "ENABLED", tuple(s for s in sports.ALL if s.key != "valorant"))
    assert pipeline.due_work(now).polls == [1]
    assert structured._watching(2) is None and structured._watching(1) == ({"adapter": "arena", "locator": {"match": "1"}}, structured.ADAPTERS["arena"], [(1, "11")])


# ---------------------------------------------------------------- a followed pro who changes teams (BUG-200)

OLD, NEW, RIVAL = "4001", "4002", "4999"


def _series(match_id: int, team: str, name: str) -> dict:
    """A best of three of a team's against Rival in two days, as PandaScore lists it."""
    soon = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat().replace("+00:00", "Z")
    sides = [{"type": "Team", "opponent": {"id": int(t), "name": n, "acronym": n[:3].upper()}} for t, n in ((team, name), (RIVAL, "Rival"))]
    return {"id": match_id, "name": f"{name} vs Rival", "status": "not_started", "scheduled_at": soon, "number_of_games": 3, "match_type": "best_of",
            "opponents": sides, "results": [], "games": [], "videogame": {"slug": "valorant"}, "league": {"name": "VCT"}, "tournament": {"tier": "a"}}


class Moving:
    """PandaScore for a pro, ProX (31001), on OldTeam until `team` says otherwise, each team with its next series:
    what it was asked, and in which lane."""

    def __init__(self, monkeypatch):
        self.team = (OLD, "OldTeam")
        self.asked: list[tuple[str, str | None, str]] = []
        monkeypatch.setattr(pandascore, "get_json", self.get_json)
        monkeypatch.setattr(pandascore, "get_all", self.get_all)
        monkeypatch.setattr(pandascore, "spare", lambda: True)

    def get_json(self, path, ttl, **params):
        self.asked.append((path, None, budget.lane()))
        if path == "/players/31001":
            return {"id": 31001, "name": "ProX", "current_team": {"id": int(self.team[0]), "name": self.team[1]}, "current_videogame": {"slug": "valorant"}}
        return []

    def get_all(self, path, ttl, pages=2, **params):
        team = params.get("filter[opponent_id]")
        self.asked.append((path, team, budget.lane()))
        return {OLD: [_series(91, OLD, "OldTeam")], NEW: [_series(92, NEW, "NewTeam")]}.get(team, [])

    def read(self) -> list[tuple[str, str | None, str]]:
        """What it was asked since the last look, each request once (a refresh reads a team's list twice, from the cache)."""
        out = list(dict.fromkeys(self.asked))
        self.asked.clear()
        return out


@pytest.fixture
def prox(engine, monkeypatch):
    """ProX followed on OldTeam: bound to PandaScore there, their card ready. Returns their player id, PandaScore
    (Moving) and the Redis their marks are kept in."""
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

    monkeypatch.setattr(structured, "session", unit)
    monkeypatch.setattr(pipeline, "session", unit)
    redis = FakeRedis()
    monkeypatch.setattr(bus, "_sync", redis)
    monkeypatch.setitem(structured.ADAPTERS, "pandascore_esports", KNOWN["pandascore_esports"])
    ref = PlayerRef("pandascore_esports", "31001", "ProX", [OLD], ["OldTeam"], league="valorant", game="Valorant")
    with unit() as db:
        player = Player(slug="prox", name="ProX", sport="Valorant", status="active", teams=["OldTeam"])
        db.add(player)
        db.flush()
        db.add(PlayerIdentity(player_id=player.id, system="pandascore_esports", external_id="31001", confidence=1.0))
        db.add_all([SourceBinding(player_id=player.id, purpose=purpose, adapter="pandascore_esports", locator=asdict(ref), confidence=1.0, priority=10, health={})
                    for purpose in ("fixtures", "stats")])
        card = {"player": {"name": "ProX"}, "live": {"is_live": False, "player_stats": []}, "upcoming": [], "recent_results": [], "freshness": {}}
        db.add(PlayerCard(player_id=player.id, status="ready", card=card, version=1, freshness={}))
    return player.id, Moving(monkeypatch), redis


def bound_to(player_id: int) -> dict[str, list[str]]:
    """The teams each of the player's bindings reads, by purpose."""
    with structured.session() as db:
        return {row.purpose: row.locator["team_ids"] for row in db.scalars(select(SourceBinding).where(SourceBinding.player_id == player_id))}


def games_of(player_id: int) -> list[str]:
    """The games the player is in here: those their fans are told of."""
    with structured.session() as db:
        return sorted(db.scalars(select(Event.title).join(EventPlayer, EventPlayer.event_id == Event.id).where(EventPlayer.player_id == player_id)))


def test_a_pro_who_changes_teams_gets_the_new_teams_series_once_their_source_is_read_again(prox):
    # BUG-200: a pro followed on OldTeam who moved to NewTeam kept OldTeam's series and pushes for good, and never got
    # NewTeam's: every refresh read the binding as first written. Now a refresh reads the pro again every few hours.
    player_id, source, redis = prox
    structured.refresh(player_id)
    assert source.read() == [("/players/31001", None, "refresh"), ("/matches", OLD, "refresh")]  # one request, in the refresh's lane
    assert games_of(player_id) == ["OldTeam vs Rival"]

    source.team = (NEW, "NewTeam")  # the transfer
    structured.refresh(player_id)
    assert source.read() == [("/matches", OLD, "refresh")]  # not read again at every refresh
    assert redis.ttl[f"lock:reread:{player_id}"] == structured.REREAD

    redis.delete(f"lock:reread:{player_id}")  # those hours pass
    structured.refresh(player_id)
    assert source.read() == [("/players/31001", None, "refresh"), ("/matches", NEW, "refresh")]
    assert bound_to(player_id) == {"fixtures": [NEW], "stats": [NEW]}
    # NewTeam's series is theirs now; OldTeam's, still to come, no longer is: no start or result push for it.
    assert games_of(player_id) == ["NewTeam vs Rival"]
    with structured.session() as db:
        card = db.get(PlayerCard, player_id).card
    assert [(u["title"], u["team"]) for u in card["upcoming"]] == [("NewTeam vs Rival", "NewTeam")]

    structured.refresh(player_id)
    assert source.read() == [("/matches", NEW, "refresh")]  # and no OldTeam series is linked again
    assert games_of(player_id) == ["NewTeam vs Rival"]


def test_following_a_pro_again_from_search_moves_them_to_the_team_their_source_has_now(prox):
    # BUG-200: unfollowing and following again from a fresh search result didn't repair it: the pick found the
    # player already here and went no further. Now a pick naming other teams than the binding moves it.
    player_id, source, redis = prox
    structured.refresh(player_id)
    assert games_of(player_id) == ["OldTeam vs Rival"]
    source.team = (NEW, "NewTeam")
    source.read()

    adapter = KNOWN["pandascore_esports"]
    picked = adapter.player("31001")  # the search result the fan picked, as the follow reads it
    assert picked is not None and picked.team_ids == [NEW]
    assert pipeline.start_from_pick(adapter, picked) == (player_id, False)
    assert bound_to(player_id) == {"fixtures": [NEW], "stats": [NEW]}
    assert games_of(player_id) == []  # OldTeam's series no longer theirs; NewTeam's come with the next refresh,
    with structured.session() as db:
        assert "fixtures" not in db.get(PlayerCard, player_id).freshness  # which is due at once
    source.read()
    structured.refresh(player_id)  # within the hours a refresh doesn't read the pro again
    assert source.read() == [("/matches", NEW, "refresh")]
    assert games_of(player_id) == ["NewTeam vs Rival"]

    # A pick of the teams already bound changes nothing.
    assert pipeline.start_from_pick(adapter, picked) == (player_id, False)
    assert games_of(player_id) == ["NewTeam vs Rival"]
