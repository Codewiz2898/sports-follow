"""The sports' rules down a live poll's real path (structured.apply_poll, notify.record, notify.send), from the sources'
own shapes: Sportmonks' made-up rows (tests/test_sportmonks.py: their terms don't allow committing real ones). Who won
a cup tie on penalties. ON CONFLICT and the push log need
Postgres itself, so these run against a throwaway database at alembic head and are skipped without one (see
tests/test_corrections_db.py for setting it up). Every test empties the tables first."""

from __future__ import annotations

import os
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from sports_follow import bus, notify, sports, structured, teams
from sports_follow.adapters import sportmonks_cricket as sc, sportmonks_football as sm
from sports_follow.models import Event, EventPlayer, EventTeam, Follow, MatchFollow, Player, PlayerIdentity, PushSubscription, Team, TeamFollow
from sports_follow.sports.base import DRAW

from .test_match_follow import Store
from .test_sportmonks_cricket import FIRST

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

KOHLI, GILL = 253802, 1070173
CRICKET = {"adapter": "sportmonks_cricket", "locator": {"fixture": "71215"}, "cadence": 10}
BOWLED = {"bowler": {"id": 1, "lastname": "Farooqi"}}


@pytest.fixture
def db(monkeypatch):
    engine = create_engine(URL or "postgresql+psycopg://")
    with engine.begin() as c:
        c.execute(text("TRUNCATE player, event, event_state, event_player, player_line, follow, match_follow, moment, notification, notification_mute, push_subscription,"
                       " player_identity, player_card, source_binding, team, team_follow, event_team, prediction_question, prediction_pick RESTART IDENTITY CASCADE"))
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

    for module in (structured, notify, teams):
        monkeypatch.setattr(module, "session", unit)
    s = made()
    yield s
    s.close()
    engine.dispose()


@pytest.fixture
def pushed(monkeypatch):
    """Polls applied at once, polled all along, with Redis made up; every push as (fan, title, body)."""
    sent: list[tuple[str, str, str]] = []
    redis = Store()
    monkeypatch.setattr(bus, "sync_redis", lambda: redis)
    monkeypatch.setattr(structured, "_polled", lambda event_id, cadence: True)
    monkeypatch.setattr(notify, "dispatch", notify.send)  # at once, not in the background
    monkeypatch.setattr(notify, "push", lambda endpoint, p256dh, auth, payload, ttl, topic=None: sent.append((endpoint.rsplit("/", 1)[1], payload["title"], payload["body"])))
    return sent


def heard(pushed: list[tuple[str, str, str]]) -> set[tuple[str, str]]:
    """What each fan was pushed since the last time asked, as (fan, title)."""
    out = {(fan, title) for fan, title, _ in pushed}
    pushed.clear()
    return out


def fan(db, name: str, player: int | None = None, game: int | None = None, team: int | None = None, level: str = "key") -> None:
    if player is not None:
        db.add(Follow(fan_id=name, player_id=player, alert_rules={"level": level}))
    if game is not None:
        db.add(MatchFollow(fan_id=name, event_id=game, alert_rules={"level": level}, keys=[]))
    if team is not None:
        db.add(TeamFollow(fan_id=name, team_id=team, alert_rules={"level": level}))
    db.add(PushSubscription(fan_id=name, endpoint=f"https://fcm.googleapis.com/fcm/send/{name}", p256dh="k", auth="a"))
    db.commit()


def player(db, player_id: int, name: str, system: str, athlete_id: int | str, sport: str = "Cricket") -> None:
    db.add(Player(id=player_id, slug=name.lower().replace(" ", "-"), name=name, sport=sport))
    db.add(PlayerIdentity(player_id=player_id, system=system, external_id=str(athlete_id), confidence=1.0))
    db.commit()


def game(db, event_id: int, key: str, binding: dict, *players: int, sport: str = "Cricket", title: str = "India v Afghanistan") -> None:
    db.add(Event(id=event_id, key=key, sport=sport, title=title, status="armed", live_binding=binding))
    db.flush()
    db.add_all([EventPlayer(event_id=event_id, player_id=p) for p in players])
    db.commit()


def cricket(row: dict, watchers: list[tuple[int, str]]) -> None:
    """One poll of the Sportmonks match, as the live lane hands it over."""
    structured.apply_poll(1, CRICKET, sc.SportmonksCricket(), watchers, sc.parse_match(row))


def innings(runs: int, wickets: int, overs: float) -> list[dict]:
    return [{**FIRST, "score": runs, "wickets": wickets, "overs": overs}]


# ---------------------------------------------------------------- penalties (BHA-461)


def _side(team_id: int, name: str, code: str, location: str, winner: bool | None) -> dict:
    return {"id": team_id, "name": name, "short_code": code, "meta": {"location": location, "winner": winner}}


def _shootout(state: str, home_pens: int, away_pens: int, winner: bool | None = None) -> dict:
    """Arsenal 1–1 Chelsea after extra time, then a shootout: a cup final in Sportmonks' shape."""
    return {"id": 999, "starting_at_timestamp": 1788710400, "state": {"developer_name": state, "name": state}, "league": {"name": "FA Cup"}, "round": {"name": "Final"},
            "participants": [_side(1, "Arsenal", "ARS", "home", winner), _side(2, "Chelsea", "CHE", "away", None if winner is None else not winner)],
            "scores": [{"description": "CURRENT", "score": {"goals": 1, "participant": "home"}}, {"description": "CURRENT", "score": {"goals": 1, "participant": "away"}},
                       {"description": "PENALTY_SHOOTOUT", "score": {"goals": home_pens, "participant": "home"}},
                       {"description": "PENALTY_SHOOTOUT", "score": {"goals": away_pens, "participant": "away"}}]}


def test_a_cup_tie_settled_on_penalties_says_who_won(db, pushed):
    rules = sports.get("football")
    final = sm.parse_match(_shootout("FT_PEN", 4, 2, winner=True))
    assert final.score_label == "Arsenal 1 – 1 Chelsea (4–2 pens)"
    assert rules.verdict(final.state, final.score_label, final.clock_label) == "Arsenal won on penalties"
    assert rules.side_final(final.state, final.score_label, final.clock_label, "2") == "Chelsea lost 1–1 (2–4 pens) v Arsenal"
    assert rules.answer("result", "final", final.state) == DRAW  # Call it: a game level after extra time is a draw
    [fixture] = sm.parse_fixtures([_shootout("FT_PEN", 4, 2, winner=True)])
    assert (sm.SportmonksFootball().result_label(fixture, ["2"]), sm.SportmonksFootball().result_label(fixture, ["1"])) == ("Lost 1–1 (2–4 pens)", "Won 1–1 (4–2 pens)")
    assert rules.notable(fixture).rank == 100

    binding = {"adapter": "sportmonks_football", "locator": {"fixture": "999"}, "cadence": 10}
    player(db, 1, "Bukayo Saka", "sportmonks_football", 4125, sport="Football")
    game(db, 1, "sportmonks_football:999", binding, 1, sport="Football", title="Arsenal vs Chelsea")
    db.add_all([Team(id=1, name="Arsenal", sport="football", system="sportmonks_football", external_id="1"), Team(id=2, name="Chelsea", sport="football", system="sportmonks_football", external_id="2")])
    db.flush()
    db.add_all([EventTeam(event_id=1, team_id=1), EventTeam(event_id=1, team_id=2)])
    db.commit()
    fan(db, "saka", player=1)
    fan(db, "game", game=1)
    fan(db, "blue", team=2)

    def poll(x: dict) -> None:
        structured.apply_poll(1, binding, sm.SportmonksFootball(), [(1, "4125")], sm.parse_match(x))

    poll(_shootout("INPLAY_PENALTIES", 3, 2))
    heard(pushed)
    poll(_shootout("FT_PEN", 4, 2, winner=True))
    assert heard(pushed) == {("game", "Arsenal v Chelsea: Arsenal won on penalties"), ("blue", "Chelsea lost 1–1 (2–4 pens) v Arsenal"),
                             ("saka", "Bukayo Saka: Arsenal 1 – 1 Chelsea (4–2 pens)")}
