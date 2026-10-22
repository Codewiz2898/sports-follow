"""The sports' rules down a live poll's real path (structured.apply_poll, notify.record, notify.send), from the sources'
own shapes: Sportmonks' made-up rows (tests/test_sportmonks_cricket.py, tests/test_sportmonks.py: their terms don't
allow committing real ones) and ESPN's recorded scorecard (tests/fixtures/espn). What a cricket fan hears from the
start to the result (the start, a first-ball duck, a rain delay, a player left out of the XI, a result whose summary
comes late, a match abandoned after play) and who won a cup tie on penalties. ON CONFLICT and the push log need
Postgres itself, so these run against a throwaway database at alembic head and are skipped without one (see
tests/test_corrections_db.py for setting it up). Every test empties the tables first."""

from __future__ import annotations

import copy
import os
from contextlib import contextmanager
from dataclasses import asdict

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from sports_follow import bus, notify, sports, structured, teams
from sports_follow.adapters import PlayerRef, espn_cricket, sportmonks_cricket as sc, sportmonks_football as sm
from sports_follow.models import Event, EventPlayer, EventTeam, Follow, MatchFollow, Player, PlayerCard, PlayerIdentity, PushSubscription, SourceBinding, Team, TeamFollow
from sports_follow.sports.base import DRAW

from .test_adapters import load
from .test_match_follow import Store
from .test_sportmonks_cricket import AFG, FIRST, IND, KEEPER, OPENER, SEAMER, SPINNER, _bat, _match, _picked, _row

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


# ---------------------------------------------------------------- a first-ball duck (BHA-557)


def test_a_first_ball_duck_is_told_to_the_batters_fans_and_the_games_but_an_innings_followed_mid_way_isnt(db, pushed):
    player(db, 1, "Rohit Opener", "sportmonks_cricket", OPENER)
    player(db, 2, "Virat Kohli", "sportmonks_cricket", KOHLI)
    player(db, 3, "Shubman Gill", "sportmonks_cricket", GILL)
    game(db, 1, "sportmonks_cricket:71215", CRICKET, 1, 2, 3)
    fan(db, "rohit", player=1)
    fan(db, "kohli", player=2)
    fan(db, "game", game=1)
    xi = [_picked(OPENER, IND), _picked(KOHLI, IND), _picked(GILL, IND), _picked(SPINNER, AFG)]
    watched = [(1, str(OPENER)), (2, str(KOHLI))]
    rohit_out = _bat(OPENER, 0, 1, "Clean Bowled", **BOWLED)
    kohli_out = _bat(KOHLI, 0, 1, "LBW OUT", sort=3, **BOWLED)
    cricket(_row("NS", lineup=xi), watched)
    cricket(_row("1st Innings", innings(0, 0, 0), xi, batting=[_bat(OPENER, 0, 0, None)]), watched)  # no ball faced: no line yet
    heard(pushed)
    # The first ball: Rohit's first line is his duck. Kohli walks in (no line till he faces), and goes first ball too.
    cricket(_row("1st Innings", innings(0, 1, 0.1), xi, batting=[rohit_out]), watched)
    cricket(_row("1st Innings", innings(0, 1, 0.1), xi, batting=[rohit_out, _bat(KOHLI, 0, 0, None, sort=3)]), watched)
    cricket(_row("1st Innings", innings(0, 2, 0.2), xi, batting=[rohit_out, kohli_out]), watched)
    assert heard(pushed) == {("rohit", "Rohit Opener out for 0"), ("kohli", "Virat Kohli out for 0"),
                             ("game", "India v Afghanistan: Rohit Opener out for 0"), ("game", "India v Afghanistan: Virat Kohli out for 0")}

    # Gill's fan follows him on 52 an hour in: his line is new to the poll, but not news. He's told from then on.
    gill = [rohit_out, kohli_out, _bat(GILL, 52, 30, None, sort=2)]
    cricket(_row("1st Innings", innings(60, 2, 8), xi, batting=gill), watched)
    fan(db, "gill", player=3)
    heard(pushed)
    cricket(_row("1st Innings", innings(61, 2, 8.1), xi, batting=[rohit_out, kohli_out, _bat(GILL, 53, 31, None, sort=2)]), [*watched, (3, str(GILL))])
    assert ("gill", "Shubman Gill reaches 50") not in heard(pushed)
    cricket(_row("1st Innings", innings(61, 3, 8.2), xi, batting=[rohit_out, kohli_out, _bat(GILL, 53, 32, "Clean Bowled", sort=2, **BOWLED)]), [*watched, (3, str(GILL))])
    assert ("gill", "Shubman Gill out for 53") in heard(pushed)


# ---------------------------------------------------------------- the start, and a rain delay (BHA-558)


def test_cricket_fans_hear_the_game_start_and_a_rain_delay_keeps_it_live(db, pushed):
    player(db, 1, "Rohit Opener", "sportmonks_cricket", OPENER)
    game(db, 1, "sportmonks_cricket:71215", CRICKET, 1)
    fan(db, "rohit", player=1)
    xi = [_picked(OPENER, IND), _picked(SPINNER, AFG)]
    watched = [(1, str(OPENER))]
    cricket(_row("NS", lineup=xi), watched)
    assert heard(pushed) == {("rohit", "Rohit Opener is in the XI")}
    # The first ball is due: nobody has a line yet, but Rohit is in the XI.
    cricket(_row("1st Innings", innings(0, 0, 0), xi, batting=[_bat(OPENER, 0, 0, None)]), watched)
    assert heard(pushed) == {("rohit", "Rohit Opener's game has started")}
    cricket(_row("1st Innings", innings(30, 0, 3), xi, batting=[_bat(OPENER, 22, 14, None)]), watched)
    # Rain: Sportmonks says "Delayed" once play has started. The game is still on, waiting, and polled.
    rain = _row("Delayed", innings(30, 0, 3), xi, batting=[_bat(OPENER, 22, 14, None)])
    assert (sc.parse_match(rain).status, sc.parse_match(rain).clock_label) == ("live", "Delayed")
    assert sc.parse_fixture(rain).status == "live"
    cricket(rain, watched)
    assert db.get(Event, 1, populate_existing=True).status == "live"
    cricket(_row("1st Innings", innings(34, 0, 3.2), xi, batting=[_bat(OPENER, 26, 16, None)]), watched)
    assert heard(pushed) == set()  # no second start at the restart
    # Rain before a ball is bowled: not started yet.
    assert sc.parse_match(_row("Delayed", [], xi)).status == "scheduled"


# ---------------------------------------------------------------- left out of the XI (BHA-559)


def test_a_cricketer_left_out_of_the_xi_hears_no_result_and_their_page_doesnt_show_the_game_live(db, pushed):
    player(db, 1, "Rohit Opener", "sportmonks_cricket", OPENER)
    player(db, 2, "Dropped Seamer", "sportmonks_cricket", SEAMER)  # in India's squad, linked by it, not in the XI
    game(db, 1, "sportmonks_cricket:71215", CRICKET, 1, 2)
    # His page went live with the game before the XI was out (a source whose sheet comes late).
    db.add(PlayerCard(player_id=2, status="ready", card={"live": {"is_live": True, "event_id": 1}, "upcoming": [{"event_id": 1, "status": "live"}], "recent_results": []}, is_live=True))
    db.commit()
    fan(db, "rohit", player=1)
    fan(db, "dropped", player=2)
    xi = [_picked(OPENER, IND), _picked(SPINNER, AFG)]
    watched = [(1, str(OPENER)), (2, str(SEAMER))]
    cricket(_row("NS", lineup=xi), watched)
    cricket(_row("1st Innings", innings(30, 0, 3), xi, batting=[_bat(OPENER, 22, 14, None)]), watched)
    card = db.get(PlayerCard, 2, populate_existing=True)
    assert not card.is_live and card.card["live"] == {"is_live": False, "player_stats": []}
    heard(pushed)
    cricket(_row("Finished", [FIRST, {"team_id": AFG, "inning": 2, "score": 94, "wickets": 10, "overs": 14.1}], xi, batting=[_bat(OPENER, 70, 40, "Clean Bowled", **BOWLED)],
                 note="India won by 127 runs", winner_team_id=IND), watched)
    assert heard(pushed) == {("rohit", "Rohit Opener: India won by 127 runs")}
    card = db.get(PlayerCard, 2, populate_existing=True)
    assert not card.is_live and card.card["recent_results"] == []  # not a game he played


# ---------------------------------------------------------------- two followed teammates (BUG-206, BUG-300)


def test_a_fan_of_two_teammates_hears_the_xi_the_start_and_the_result_once_in_words_for_both(db, pushed):
    # One push each, naming both: the XI was said in one player's words ("Rishabh Pant and Rohit Sharma is in the XI"),
    # and the result twice, each with its own line under it.
    player(db, 1, "Rohit Sharma", "sportmonks_cricket", OPENER)
    player(db, 2, "Rishabh Pant", "sportmonks_cricket", KEEPER)
    game(db, 1, "sportmonks_cricket:71215", CRICKET, 1, 2)
    fan(db, "two", player=1)
    db.add(Follow(fan_id="two", player_id=2, alert_rules={"level": "key"}))
    db.commit()
    xi = [_picked(OPENER, IND), _picked(KEEPER, IND), _picked(SPINNER, AFG)]
    watched = [(1, str(OPENER)), (2, str(KEEPER))]
    cricket(_row("NS", lineup=xi), watched)
    cricket(_row("1st Innings", innings(0, 0, 0), xi, batting=[_bat(OPENER, 0, 0, None)]), watched)
    cricket(_row("Finished", [FIRST, {"team_id": AFG, "inning": 2, "score": 94, "wickets": 10, "overs": 14.1}], xi, note="India won by 127 runs", winner_team_id=IND,
                 batting=[_bat(OPENER, 70, 40, "Clean Bowled", **BOWLED), _bat(KEEPER, 20, 10, "Clean Bowled", sort=2, **BOWLED)]), watched)
    assert [(title, body.split("\n")) for _, title, body in pushed] == [
        ("Rishabh Pant and Rohit Sharma are in the XI", ["India v Afghanistan · line-ups are out"]),
        ("Rishabh Pant and Rohit Sharma's game has started", ["IND 0/0 (0 ov) · 1st Innings", "Rishabh Pant and Rohit Sharma are in the XI"]),
        ("Rishabh Pant and Rohit Sharma: India won by 127 runs", ["Rishabh Pant · 20 (10)", "Rohit Sharma · 70 (40)", "Rohit Sharma out for 70", "Rohit Sharma reaches 50", "Rishabh Pant out for 20"]),
    ]


class Listed(sc.SportmonksCricket):
    """Sportmonks cricket, its reads made up: the player's fixtures as listed, nobody new on a re-read, no form."""

    def __init__(self, listed):
        super().__init__()
        self.listed = listed

    def fixtures(self, ref):
        return self.listed

    def player(self, athlete_id, league=None):
        return None

    def stats(self, ref, recent):
        return [], ""


class ListedEspn(espn_cricket.EspnCricket):
    """ESPN cricket, its reads made up as Listed's: a day's feed that failed leaves its games out of them quietly."""

    def __init__(self, listed):
        super().__init__()
        self.listed = listed

    def fixtures(self, ref):
        return self.listed

    def player(self, athlete_id, league=None):
        return None

    def stats(self, ref, recent):
        return [], ""


@pytest.mark.parametrize(("source", "unlinked"), [(Listed, True), (ListedEspn, False)], ids=["sportmonks", "espn-reads-may-come-back-short"])
def test_a_refresh_that_no_longer_lists_a_game_takes_the_player_out_of_it(db, pushed, monkeypatch, source, unlinked):
    # The XI is out without him, so Sportmonks no longer lists the game for him (in_match): he's taken out of it, so it
    # isn't polled, shown live or told for him. One still listed stays his; so does every game for a source whose reads
    # may come back short.
    adapter = source([sc.parse_fixture(_match("NS", [], id=71216, starting_at="2026-12-01T14:00:00.000000Z"))])
    monkeypatch.setitem(structured.ADAPTERS, adapter.system, adapter)
    binding = {"adapter": adapter.system, "locator": {"fixture": "71215"}, "cadence": 10}
    player(db, 2, "Dropped Seamer", adapter.system, SEAMER)
    ref = PlayerRef(adapter.system, str(SEAMER), "Dropped Seamer", [str(IND)], ["India"])
    db.add(SourceBinding(player_id=2, purpose="fixtures", adapter=adapter.system, locator=asdict(ref), priority=10, health={}))
    game(db, 1, f"{adapter.system}:71215", binding, 2)
    game(db, 2, f"{adapter.system}:71216", binding, 2)
    db.get(Event, 1).status = "live"
    db.add(PlayerCard(player_id=2, status="ready", card={"live": {"is_live": True, "event_id": 1}, "upcoming": [], "recent_results": []}, is_live=True))
    db.commit()
    structured._refresh(2)
    linked = set(db.scalars(select(EventPlayer.event_id).where(EventPlayer.player_id == 2)))
    card = db.get(PlayerCard, 2, populate_existing=True)
    if unlinked:
        assert linked == {2} and not card.is_live and card.card["live"] == {"is_live": False, "player_stats": []}
    else:
        assert linked == {1, 2} and card.is_live


# ---------------------------------------------------------------- the result's summary, an abandoned match (BHA-562)


def _espn(summary: str | None = None, state: str = "post") -> dict:
    """ESPN's recorded ODI scorecard (India won by 8 wkts), live, or over with its summary as given."""
    data = copy.deepcopy(load("cricket-summary-final"))
    status = data["header"]["competitions"][0]["status"]
    status["type"]["state"] = state
    if summary is not None:
        status["summary"] = summary
    return data


def _espn_poll(data: dict) -> None:
    binding = {"adapter": "espn_cricket", "locator": {"league": "8044", "event": "1478889"}, "cadence": 8}
    structured.apply_poll(1, binding, espn_cricket.EspnCricket(), [(1, str(KOHLI))], espn_cricket.parse_summary(data))


def test_a_result_whose_summary_comes_a_poll_late_is_told_with_it(db, pushed):
    player(db, 1, "Virat Kohli", "espn_cricket", KOHLI)
    game(db, 1, "espn_cricket:1478889", {"adapter": "espn_cricket", "locator": {"league": "8044", "event": "1478889"}, "cadence": 8}, 1, title="India v West Indies")
    fan(db, "kohli", player=1)
    _espn_poll(_espn("India need 4 runs", state="in"))
    heard(pushed)
    _espn_poll(_espn(""))  # over, its summary still to come: "Result"
    assert heard(pushed) == set()
    assert db.get(Event, 1, populate_existing=True).status == "live"  # polled again
    _espn_poll(_espn())
    assert heard(pushed) == {("kohli", "Virat Kohli: India won by 8 wkts (50b rem)")}
    assert db.get(Event, 1, populate_existing=True).status == "final"


def test_a_result_whose_summary_never_comes_is_told_after_a_poll_or_two(db, pushed):
    player(db, 1, "Virat Kohli", "espn_cricket", KOHLI)
    game(db, 1, "espn_cricket:1478889", {"adapter": "espn_cricket", "locator": {"league": "8044", "event": "1478889"}, "cadence": 8}, 1, title="India v West Indies")
    fan(db, "kohli", player=1)
    _espn_poll(_espn("India need 4 runs", state="in"))
    heard(pushed)
    told = []
    for _ in range(4):
        _espn_poll(_espn(""))
        told += heard(pushed)
    assert told == [("kohli", "Virat Kohli: Result")]  # once, held at most two polls
    assert db.get(Event, 1, populate_existing=True).status == "final"


def test_a_match_abandoned_after_play_is_a_no_result_not_postponed(db, pushed):
    player(db, 1, "Rohit Opener", "sportmonks_cricket", OPENER)
    game(db, 1, "sportmonks_cricket:71215", CRICKET, 1)
    fan(db, "rohit", player=1)
    xi = [_picked(OPENER, IND), _picked(SPINNER, AFG)]
    watched = [(1, str(OPENER))]
    chase = {"team_id": AFG, "inning": 2, "score": 40, "wickets": 1, "overs": 4.2}
    cricket(_row("2nd Innings", [FIRST, chase], xi, batting=[_bat(OPENER, 70, 40, "Clean Bowled", **BOWLED)]), watched)
    heard(pushed)
    washed = _row("Aban.", [FIRST, chase], xi, batting=[_bat(OPENER, 70, 40, "Clean Bowled", **BOWLED)], note="No result (rain)")
    f = sc.parse_fixture(washed)
    assert (f.status, (f.result or {}).get("summary")) == ("final", "No result (rain)")
    snap = sc.parse_match(washed)
    assert (snap.status, snap.clock_label, snap.lines[str(OPENER)].headline) == ("final", "No result (rain)", "70 (40)")
    assert sports.get("cricket").answer("result", snap.status, snap.state) == "void"
    cricket(washed, watched)
    assert heard(pushed) == {("rohit", "Rohit Opener: No result (rain)")}
    assert db.get(Event, 1, populate_existing=True).status == "final"
    # Washed out before a ball: called off, as before.
    assert sc.parse_fixture(_match("Aban.", [])).status == "postponed"


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
