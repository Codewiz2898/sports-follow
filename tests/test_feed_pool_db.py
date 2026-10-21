"""The feed's pool of top players against Postgres (feedpool.py, BHA-382): each running sport's best known from the
registry, by Wikipedia editions and then their team's tier (all an esports pro has); one an operator removed left
out; a player made of each who has none, as a fan's follow from search would, a few a day, the sports taking turns;
one no live source has looked for again only after a week; and the pool kept for the poll and the sweeps, after the
followed players. The live source and every athlete are made up. Skipped without a throwaway database at alembic
head (tests/test_corrections_db.py says how to make one)."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, select, text

from sports_follow import bus, candidates, db as db_module, feedpool, registry
from sports_follow.adapters import PlayerRef
from sports_follow.models import Athlete, AthleteOverride, Follow, Player, PlayerIdentity, Team
from tests.test_feed_fetch_db import Redis

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")


class Source:
    """A live source, made up: the system a registry athlete is found in, and the sport it has them in."""

    system = "made_up_cricket"

    def sport_of(self, ref: PlayerRef) -> str:
        return ref.game or "Cricket"


@pytest.fixture
def world(monkeypatch):
    engine = create_engine(URL or "postgresql+psycopg://")
    with engine.begin() as c:
        c.execute(text("TRUNCATE athlete, athlete_override, team, follow, player_alias, player_identity, player RESTART IDENTITY CASCADE"))
    monkeypatch.setattr(db_module, "SessionLocal", db_module.sessionmaker(bind=engine, expire_on_commit=False))
    redis = Redis()
    monkeypatch.setattr(bus, "sync_redis", lambda: redis)
    monkeypatch.setattr(bus, "store_card", lambda player_id, card: None)
    monkeypatch.setattr(feedpool, "FEED_POOL_SIZE", 2)
    looked: list[str] = []

    def resolve(person):
        looked.append(person.name)
        if "Unknown" in person.name:
            return None
        return Source(), PlayerRef(Source.system, person.qid.lower(), person.name, [], list(person.teams))

    monkeypatch.setattr(registry, "resolve", resolve)
    yield looked
    engine.dispose()


def athlete(qid: str, name: str, sport: str = "cricket", sitelinks: int = 0, teams: tuple[str, ...] = (), country: str | None = None,
            ids: dict | None = None) -> None:
    with db_module.session() as s:
        s.add(Athlete(qid=qid, sport=sport, name=name, aliases=[], search_text=name.lower(), sitelinks=sitelinks, teams=list(teams), country=country,
                      ids=ids or {}, current=True))  # fmt: skip


def team(name: str, sport: str, tier: int) -> None:
    with db_module.session() as s:
        s.add(Team(name=name, sport=sport, system="made_up", external_id=name, tier=tier))


def test_a_sports_best_known_are_by_wikipedia_editions_then_their_teams_tier_and_one_neither_rates_is_left_out(world):
    athlete("Q1", "Pat Cummins", sitelinks=40, country="Australia")
    athlete("Q2", "Rishabh Pant", sitelinks=25, country="India")  # the home country's count double: 50
    athlete("Q3", "Nobody Known")
    athlete("pandascore:1", "NiKo", sport="esports", teams=("Team Falcons",))
    athlete("pandascore:2", "Smalltime", sport="esports", teams=("Small Five",))
    athlete("pandascore:3", "Unrated", sport="esports", teams=("Garage Five",))
    team("Team Falcons", "cs2", 3)
    team("Small Five", "cs2", 1)
    with db_module.session() as s:
        assert [a.name for a in feedpool.ranked(s, "cricket", 10)] == ["Rishabh Pant", "Pat Cummins"]
        assert [a.name for a in feedpool.ranked(s, "esports", 10)] == ["NiKo", "Smalltime"]
        assert [a.name for a in feedpool.ranked(s, "cricket", 1)] == ["Rishabh Pant"]


def test_a_refresh_makes_players_of_the_pool_a_few_a_day_the_sports_taking_turns_and_keeps_the_pool(world):
    athlete("Q1", "Pat Cummins", sitelinks=40, teams=("Australia",))
    athlete("Q2", "Steve Smith", sitelinks=30, teams=("Australia",))
    athlete("Q3", "Mitchell Starc", sitelinks=20)  # past the pool's size (2)
    athlete("Q9", "Removed One", sitelinks=90, ids={"made_up_cricket": "x9"})
    athlete("Q8", "Unknown Pro", sport="chess", sitelinks=80)  # no live source has him
    athlete("Q7", "Hikaru Nakamura", sport="chess", sitelinks=70)
    with db_module.session() as s:
        s.add(AthleteOverride(qid="Q9", sport="cricket", action="remove", name="Removed One", ids={"made_up_cricket": "x9"}))
    done = feedpool.refresh(adds=3)
    # The best known of each sport first, in turns: cricket's first, chess's first (not found), cricket's second.
    assert world == ["Pat Cummins", "Unknown Pro", "Steve Smith"] and done == {"players": 2, "made": 2, "missing": 1}
    with db_module.session() as s:
        made = {p.name: p for p in s.scalars(select(Player))}
        assert set(made) == {"Pat Cummins", "Steve Smith"} and made["Pat Cummins"].teams == ["Australia"]
        assert s.scalar(select(PlayerIdentity.external_id).where(PlayerIdentity.player_id == made["Pat Cummins"].id, PlayerIdentity.system == "wikidata")) == "Q1"
        assert not s.scalar(select(Follow.id))  # nobody follows them: the worker reads nothing of theirs until a fan does
    assert feedpool.stored() == {"cricket": [made["Pat Cummins"].id, made["Steve Smith"].id]}
    # The next day: the one no source has isn't looked for again, and the rest are made.
    world.clear()
    assert feedpool.refresh(adds=3) == {"players": 3, "made": 1, "missing": 0} and world == ["Hikaru Nakamura"]
    with db_module.session() as s:
        hikaru = s.scalar(select(Player.id).where(Player.name == "Hikaru Nakamura"))
    assert feedpool.ids() == [made["Pat Cummins"].id, hikaru, made["Steve Smith"].id]  # the sports taking turns down their lists


def test_the_pool_is_fetched_for_after_the_followed_players_and_a_followed_one_in_it_stays_followed(world):
    athlete("Q1", "Pat Cummins", sitelinks=40)
    athlete("Q2", "Steve Smith", sitelinks=30)
    feedpool.refresh()
    with db_module.session() as s:
        cummins, smith = (s.scalar(select(Player.id).where(Player.name == n)) for n in ("Pat Cummins", "Steve Smith"))
        followed = Player(slug="travis-head", name="Travis Head", sport="cricket", teams=[])
        s.add(followed)
        s.flush()
        s.add(Follow(fan_id="fan", player_id=followed.id, alert_rules={}))
        s.add(Follow(fan_id="fan", player_id=smith, alert_rules={}))
        head = followed.id
    with db_module.session() as s:
        players = candidates.followed(s, feedpool.ids())
    assert [(p.player_id, p.pooled) for p in players][-1] == (cummins, True)
    assert {(p.player_id, p.pooled) for p in players} == {(head, False), (smith, False), (cummins, True)}
    with db_module.session() as s:
        assert {p.player_id for p in candidates.followed(s)} == {head, smith}  # without the pool: the followed alone, as before
