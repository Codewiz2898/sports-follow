"""Predictions with friends against real rows (predictions.py, BHA-319): a game's questions made from its board
before it starts, closed as it starts and settled from what its poller reads, through structured.apply_poll itself:
a recorded Lichess result (tests/fixtures/lichess) and a PandaScore series in its documented shape (made up, as
tests/test_pandascore.py's are). Then picks, the sweep, leagues, accounts and the numbers. ON CONFLICT and the
JSONB lookups need Postgres, so these run against a throwaway database at alembic head and are skipped without one
(tests/test_accounts_db.py says how to make it). Redis and the sources are made up."""

from __future__ import annotations

import asyncio
import os
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from sports_follow import accounts, bus, engine, league, live, match, notify, pipeline, predictions, server, sports, structured
from sports_follow.adapters import ADAPTERS, KNOWN, Fixture, LiveGame, lichess_chess, pandascore_esports as ps, sportmonks_cricket as sc, sportmonks_football as sm
from sports_follow.models import Account, Event, EventPlayer, Follow, League, LeagueMember, Pick, Player, Question
from sports_follow.sports import Notable

from .test_match_follow import Store
from .test_new_adapters import CARLSEN, LAZAVIK, load, tiebreak
from .test_pandascore import FINAL, NRG, SEN, _game, _match
from .test_predictions import board
from .test_sportmonks import AWAY, HOME, _match as football_match
from .test_sportmonks_cricket import IND, _match as cricket_match

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

ROUND = load("lichess/round-final")["round"]["id"]
GAME = "lichess_chess:mJnv1YBU"  # the board's own id at Lichess: one game under both players' fixtures
SERIES = "pandascore_esports:777"


def soon() -> datetime:
    """The games here start tomorrow: picks are made now, by the clock the app uses."""
    return datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=1)


@pytest.fixture
def db(monkeypatch):
    engine_ = create_engine(URL or "postgresql+psycopg://")
    with engine_.begin() as c:
        c.execute(text("TRUNCATE account, account_session, player, event, event_state, event_player, player_line, follow, match_follow, moment, notification, "
                       "push_subscription, prediction_question, prediction_pick, prediction_league, prediction_member, campaign, team RESTART IDENTITY CASCADE"))
    made = sessionmaker(bind=engine_, expire_on_commit=False)

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

    for module in (live, match, structured, notify, pipeline, predictions, accounts, server):
        monkeypatch.setattr(module, "session", unit)
    monkeypatch.setattr(engine, "enabled", lambda: False)  # no Stockfish asked about the boards here
    s = made()
    yield s
    s.close()
    engine_.dispose()


class Redis(Store):
    """The match-follow tests' Redis, that also leaves a key alone when told to set it only if it's not there."""

    def set(self, key: str, value: str, ex: int | None = None, nx: bool = False, get: bool = False):
        if nx and self.get(key) is not None:
            return None
        return super().set(key, value, ex=ex, get=get)


@pytest.fixture
def redis(monkeypatch):
    store = Redis()
    monkeypatch.setattr(bus, "sync_redis", lambda: store)
    return store


@pytest.fixture(autouse=True)
def running(monkeypatch):
    """The sources run here, as in production: a pick needs its game's source on, to be settled."""
    for system, sport in (("lichess_chess", "chess"), ("pandascore_esports", "valorant"), ("sportmonks_football", "football"), ("sportmonks_cricket", "cricket")):
        if system not in ADAPTERS:
            monkeypatch.setitem(ADAPTERS, system, SimpleNamespace(system=system, sports=(sport,)))


def source(system: str) -> SimpleNamespace:
    return SimpleNamespace(system=system, credit=lambda snap: system, live_note=None, lag_note=None)


def poll(event_id: int, snap, system: str = "lichess_chess") -> None:
    structured.apply_poll(event_id, {"adapter": system, "locator": {}, "cadence": 10}, source(system), [], snap)


def chess_board(db, start: datetime) -> None:
    """The grand final's first game here as each player's fixture has it: two events, one board."""
    for event_id, fide in ((1, CARLSEN), (2, LAZAVIK)):
        db.add(Event(id=event_id, key=f"lichess_chess:{ROUND}-{fide}", sport="Chess", title="Magnus Carlsen vs Denis Lazavik", start_utc=start, status="armed",
                     live_binding={"adapter": "lichess_chess", "locator": {"round": ROUND, "fide": fide}, "cadence": 10}))
    db.commit()


def picks(db) -> dict[str, tuple[str, int | None]]:
    db.expire_all()
    return {p.fan_id: (p.option, p.points) for p in db.scalars(select(Pick))}


# ---------------------------------------------------------------- made, closed and settled by the poller


def test_a_chess_game_is_asked_before_it_starts_and_settled_from_its_recorded_result(db, redis, monkeypatch):
    monkeypatch.setattr(predictions, "SPLIT_FROM", 5)
    start = soon()
    chess_board(db, start)
    poll(1, board(status="*"))
    poll(2, board(status="*", fide=LAZAVIK))  # the same board under the other player's fixture: the same question
    [q] = db.scalars(select(Question)).all()
    assert (q.game, q.ask, q.title, q.status, q.event_id, q.locks_at) == (GAME, "result", "Who wins?", "open", 1, start)
    assert q.keys == [f"lichess_chess:{ROUND}-{CARLSEN}", f"lichess_chess:{ROUND}-{LAZAVIK}"]
    assert [o["key"] for o in q.options] == [CARLSEN, "draw", LAZAVIK]
    for fan, option in (("asha", CARLSEN), ("ravi", CARLSEN), ("meera", "draw")):
        predictions.pick(db, fan, q.id, option)
    db.commit()
    # A pick waits on it: its game is polled as a followed game is, though nobody follows a player in it.
    assert db.scalars(predictions.watched()).all() == [1] and predictions.waiting(db, 1)
    # Three picks: too few for the split, which would tell a fan their friends' picks; it shows from SPLIT_FROM on.
    [few] = predictions.shown(db, "asha", [q], datetime.now(timezone.utc))
    assert all("share" not in o for o in few["options"]) and few["worth"] is None and few["mine"] == CARLSEN
    for fan in ("kiran", "dev"):
        predictions.pick(db, fan, q.id, LAZAVIK)
    db.commit()
    [shown] = predictions.shown(db, "asha", [q], datetime.now(timezone.utc))
    assert [(o["key"], o["share"]) for o in shown["options"]] == [(CARLSEN, 40), ("draw", 20), (LAZAVIK, 40)] and shown["worth"] == 60
    [unpicked] = predictions.shown(db, "sana", [q], datetime.now(timezone.utc))
    assert all("share" not in o for o in unpicked["options"]) and unpicked["picks"] == 5  # the crowd doesn't pick for them

    poll(1, board(status="*", ongoing=True))  # the first move: it closes
    db.expire_all()
    assert db.get(Question, q.id).status == "closed"
    with pytest.raises(predictions.Refused) as late:
        predictions.pick(db, "sana", q.id, LAZAVIK)
    assert late.value.status == 409
    db.rollback()

    poll(1, board(0))  # the recorded result: Carlsen won with White
    db.expire_all()
    settled = db.get(Question, q.id)
    assert (settled.status, settled.answer) == ("settled", CARLSEN)
    assert picks(db) == {"asha": (CARLSEN, 60), "ravi": (CARLSEN, 60), "meera": ("draw", 0), "kiran": (LAZAVIK, 0), "dev": (LAZAVIK, 0)}  # two of five were right
    assert db.scalars(predictions.watched()).all() == []  # nothing waits on it now
    # The result corrected at the source (an arbiter's ruling): it settles again.
    predictions.settle(db, [settled], structured.sports.get("chess"), "final", board(1).state, datetime.now(timezone.utc))
    db.commit()
    assert picks(db) == {"asha": (CARLSEN, 0), "ravi": (CARLSEN, 0), "meera": ("draw", 80), "kiran": (LAZAVIK, 0), "dev": (LAZAVIK, 0)}


def test_a_tiebreak_rounds_question_is_settled_by_its_match_through_its_polls(db, redis):
    # "Who wins?" asked of a tiebreak round's game 1, shown alone before the start (BUG-213): its games 1–4 come one at
    # a time, each a board of its own, and none of them settles it; the match does once the round's over: Lazavik won
    # 2½–1½ (Carlsen won game 1, Lazavik games 2 and 3, game 4 drawn), and the picks of Lazavik are right.
    chess_board(db, soon())

    def look(*results: str, **when: bool) -> None:
        for event_id, fide in ((1, CARLSEN), (2, LAZAVIK)):
            poll(event_id, lichess_chess.parse_board(tiebreak(*results, **when), fide))

    look("*", to_come=True)
    [q] = db.scalars(select(Question)).all()
    assert q.game == GAME
    for fan, option in (("asha", CARLSEN), ("ravi", LAZAVIK), ("meera", "draw")):
        predictions.pick(db, fan, q.id, option)
    db.commit()
    for results in (("*",), ("1-0",), ("1-0", "*"), ("1-0", "1-0"), ("1-0", "1-0", "0-1"), ("1-0", "1-0", "0-1", "½-½")):
        look(*results)
        db.expire_all()
        assert (db.get(Question, q.id).status, db.get(Question, q.id).answer) == ("closed", None), results  # type: ignore[union-attr]
    look("1-0", "1-0", "0-1", "½-½", finished=True)
    db.expire_all()
    assert (db.get(Question, q.id).status, db.get(Question, q.id).answer) == ("settled", LAZAVIK)  # type: ignore[union-attr]
    assert {fan: points > 0 for fan, (_, points) in picks(db).items() if points is not None} == {"asha": False, "ravi": True, "meera": False}
    assert db.scalars(select(Question)).all() == [q]  # one question, the match's, through all its boards


def test_a_series_settles_map_by_map_and_a_map_never_played_is_voided(db, redis):
    db.add(Event(id=5, key=SERIES, sport="Valorant", title="Sentinels vs NRG", start_utc=soon(), status="armed", live_binding={"adapter": "pandascore_esports", "locator": {"match": "777"}}))
    db.commit()
    poll(5, ps.parse_match(_match("not_started")), "pandascore_esports")
    qs = {q.ask: q for q in db.scalars(select(Question))}
    assert sorted(qs) == ["map1", "map2", "map3"] and qs["map1"].locks_at is not None and qs["map2"].locks_at is None
    for fan, option in (("a", SEN), ("b", NRG), ("c", SEN)):
        predictions.pick(db, fan, qs["map1"].id, str(option))
        predictions.pick(db, fan, qs["map3"].id, str(option))
    predictions.pick(db, "a", qs["map2"].id, str(NRG))
    db.commit()
    # Its source polls its games together, in one lane: the series is in it now, though nobody follows a player in it.
    assert structured.lane_events("pandascore_esports") == [5]
    live = _match("running", 1, 0, [_game(1, "finished", SEN), _game(2, "running"), _game(3, "not_started")])
    poll(5, ps.parse_match(live), "pandascore_esports")
    db.expire_all()
    assert [db.get(Question, qs[k].id).status for k in ("map1", "map2", "map3")] == ["settled", "closed", "open"]
    assert {(p.fan_id, p.points) for p in db.scalars(select(Pick).where(Pick.question_id == qs["map1"].id))} == {("a", 33), ("b", 0), ("c", 33)}
    two_nil = _match("finished", 2, 0, [_game(1, "finished", SEN), _game(2, "finished", SEN), _game(3, "not_played")], winner_id=SEN)
    poll(5, ps.parse_match(two_nil), "pandascore_esports")
    db.expire_all()
    assert [(db.get(Question, qs[k].id).status, db.get(Question, qs[k].id).answer) for k in ("map2", "map3")] == [("settled", str(SEN)), ("void", "void")]
    assert {p.points for p in db.scalars(select(Pick).where(Pick.question_id == qs["map3"].id))} == {None}
    assert db.scalar(select(Pick.points).where(Pick.question_id == qs["map2"].id)) == 0  # a picked NRG


def test_a_football_match_and_a_t20i_are_asked_and_settled_by_their_polls(db, redis):
    start = soon()
    for event_id, system, sport, title in ((7, "sportmonks_football", "Football", "Trabzonspor vs Galatasaray"), (8, "sportmonks_cricket", "Cricket", "India v Afghanistan")):
        fixture = "555" if sport == "Football" else "71215"
        db.add(Event(id=event_id, key=f"{system}:{fixture}", sport=sport, title=title, start_utc=start, status="armed",
                     live_binding={"adapter": system, "locator": {"fixture": fixture}, "cadence": 10}))
    db.commit()
    poll(7, sm.parse_match(football_match("NS", 0, 0)), "sportmonks_football")
    poll(8, sc.parse_match(cricket_match("NS", [])), "sportmonks_cricket")
    qs = {q.game: q for q in db.scalars(select(Question))}
    football, t20i = qs["sportmonks_football:555"], qs["sportmonks_cricket:71215"]
    assert (football.ask, football.title, football.status, football.locks_at, [o["key"] for o in football.options]) == ("result", "Who wins?", "open", start, [str(HOME), "draw", str(AWAY)])
    assert (t20i.sport, [o["label"] for o in t20i.options]) == ("cricket", ["India", "Afghanistan"])
    for fan, option in (("asha", str(HOME)), ("ravi", "draw"), ("meera", str(AWAY))):
        predictions.pick(db, fan, football.id, option)
    predictions.pick(db, "asha", t20i.id, str(IND))
    db.commit()
    assert sorted(db.scalars(predictions.watched()).all()) == [7, 8]

    poll(7, sm.parse_match(football_match("INPLAY_2ND_HALF", 1, 1)), "sportmonks_football")  # kicked off: closed
    db.expire_all()
    assert db.get(Question, football.id).status == "closed"
    poll(7, sm.parse_match(football_match("FT", 2, 1)), "sportmonks_football")
    db.expire_all()
    assert (db.get(Question, football.id).status, db.get(Question, football.id).answer) == ("settled", str(HOME))
    scored = {fan: points for fan, points in db.execute(select(Pick.fan_id, Pick.points).where(Pick.question_id == football.id))}
    assert scored == {"asha": 67, "ravi": 0, "meera": 0}  # one of three called the home win
    # Washed out without a ball bowled: no result, so no points either way.
    poll(8, sc.parse_match(cricket_match("Aban.", [])), "sportmonks_cricket")
    db.expire_all()
    assert (db.get(Question, t20i.id).status, db.get(Question, t20i.id).answer) == ("void", "void")
    assert db.scalar(select(Pick.points).where(Pick.question_id == t20i.id)) is None
    assert db.scalars(predictions.watched()).all() == []


def test_a_pick_waiting_keeps_its_game_polled_and_the_tick_closes_and_voids(db, redis, monkeypatch):
    start = soon()
    chess_board(db, start)
    poll(1, board(status="*"))
    q = db.scalars(select(Question)).one()
    with monkeypatch.context() as off:  # its source turned off: a pick could never be settled
        off.delitem(ADAPTERS, "lichess_chess")
        with pytest.raises(predictions.Refused) as unsettled:
            predictions.pick(db, "asha", q.id, CARLSEN)
        assert unsettled.value.status == 409
        db.rollback()
    monkeypatch.setitem(ADAPTERS, "lichess_chess", SimpleNamespace(system="lichess_chess", sports=("chess",)))
    assert structured._watching(1) is None  # pyright: ignore[reportPrivateUsage]  nobody's player, nobody's game, no pick
    predictions.pick(db, "asha", q.id, CARLSEN)
    db.commit()
    assert structured._watching(1) is not None  # pyright: ignore[reportPrivateUsage]
    # A pick can be changed and taken back while it's open; an answer it doesn't offer is refused.
    predictions.pick(db, "asha", q.id, "draw")
    with pytest.raises(predictions.Refused) as wrong:
        predictions.pick(db, "asha", q.id, "white")
    assert wrong.value.status == 400 and picks(db) == {"asha": ("draw", None)}
    predictions.unpick(db, "ravi", q.id)  # one they never made: nothing
    # Its start time closes it, though no poll saw the game begin; a game that never comes is voided.
    predictions.sweep(db, start + timedelta(minutes=1))
    db.expire_all()
    assert db.get(Question, q.id).status == "closed"
    with pytest.raises(predictions.Refused):
        predictions.unpick(db, "asha", q.id)
    predictions.sweep(db, start + predictions.STALE + timedelta(minutes=1))
    db.expire_all()
    assert (db.get(Question, q.id).status, picks(db)) == ("void", {"asha": ("draw", None)})


def test_a_game_put_back_moves_its_question_while_its_open_and_one_closed_stays_closed(db, redis):
    start = soon()
    chess_board(db, start)
    poll(1, board(status="*"))
    q = db.scalars(select(Question)).one()
    later = start + timedelta(hours=2)
    db.execute(text("UPDATE event SET start_utc = :at"), {"at": later})  # its source moved the round before it began
    db.commit()
    poll(1, board(status="*"))
    db.expire_all()
    assert (db.get(Question, q.id).status, db.get(Question, q.id).locks_at, db.get(Question, q.id).starts_at) == ("open", later, later)
    predictions.sweep(db, later + timedelta(minutes=1))  # its new start came, the game didn't: closed
    db.commit()
    db.execute(text("UPDATE event SET start_utc = :at"), {"at": later + timedelta(days=1)})  # put back again
    db.commit()
    poll(1, board(status="*"))
    db.expire_all()
    assert db.get(Question, q.id).status == "closed"  # friends' picks may have shown: it doesn't open again
    # Postponed, it waits for its new date: the sweep leaves it a month, not three days.
    db.execute(text("UPDATE event SET status = 'postponed'"))
    db.commit()
    predictions.sweep(db, later + predictions.STALE + timedelta(days=1))
    db.commit()
    db.expire_all()
    assert db.get(Question, q.id).status == "closed"
    predictions.sweep(db, later + predictions.POSTPONED_KEEP + timedelta(days=2))
    db.commit()
    db.expire_all()
    assert db.get(Question, q.id).status == "void"


def test_a_match_the_schedule_puts_back_keeps_its_question_till_its_new_date(db, redis):
    """A postponed match's new date comes in a schedule read, not a poll (structured.upsert_game): its questions start
    then, and the sweep waits from there, not from the old date."""
    start = soon()
    sportmonks = SimpleNamespace(system="sportmonks_football", live_cadence=10)

    def schedule(fixture: str, at: datetime, status: str) -> None:
        structured.upsert_game(db, sportmonks, "Football", Fixture(fixture, "Trabzonspor vs Galatasaray", "Super Lig", at, None, status, [str(HOME), str(AWAY)], {"fixture": fixture}))
        db.commit()

    for fixture in ("555", "556"):
        schedule(fixture, start, "scheduled")
        event_id = db.scalars(select(Event.id).where(Event.key == f"sportmonks_football:{fixture}")).one()
        poll(event_id, sm.parse_match({**football_match("NS", 0, 0), "id": int(fixture)}), "sportmonks_football")
    qs = {q.game: q for q in db.scalars(select(Question))}
    gone, moved = qs["sportmonks_football:555"], qs["sportmonks_football:556"]
    predictions.pick(db, "asha", gone.id, str(HOME))
    db.commit()
    later = start + timedelta(days=10)
    # Put back before its start: its question stays open, closing at the new kick-off.
    schedule("556", later, "scheduled")
    db.expire_all()
    assert (db.get(Question, moved.id).status, db.get(Question, moved.id).locks_at, db.get(Question, moved.id).starts_at) == ("open", later, later)
    # The other's start came with no poll (closed); then it's postponed, and weeks on given a new date.
    predictions.sweep(db, start + timedelta(minutes=1))
    db.commit()
    schedule("555", start, "postponed")
    schedule("555", later, "scheduled")
    predictions.sweep(db, start + predictions.STALE + timedelta(days=1))
    db.commit()
    db.expire_all()
    assert (db.get(Question, gone.id).status, db.get(Question, gone.id).starts_at) == ("closed", later)  # not voided
    assert db.get(Question, moved.id).status == "open"
    poll(db.get(Question, gone.id).event_id, sm.parse_match(football_match("FT", 1, 0)), "sportmonks_football")
    db.expire_all()
    assert (db.get(Question, gone.id).status, db.get(Question, gone.id).answer, picks(db)) == ("settled", str(HOME), {"asha": (str(HOME), predictions.FLOOR)})
    # Brought forward to a time that's passed (the read came late): its picks close now, not at the old time.
    kicked_off = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5)
    schedule("556", kicked_off, "scheduled")
    with pytest.raises(predictions.Refused) as shut:
        predictions.pick(db, "ravi", moved.id, str(AWAY))
    assert shut.value.status == 409 and db.get(Question, moved.id).locks_at == kicked_off


def test_a_match_postponed_before_its_start_waits_closed_to_picks_and_opens_at_its_new_date(db, redis):
    """BUG-109: a match put back before it starts (ESPN and Sportmonks often keep its date) was closed by the sweep at
    the old kick-off, though it never started, and stayed closed when its new date came: nobody could call it then.
    Now, while it waits, it takes no picks and isn't closed (so friends' picks stay hidden, as they were), and its new
    date opens it again, closing at the new kick-off."""
    start = soon()
    sportmonks = SimpleNamespace(system="sportmonks_football", live_cadence=10)

    def schedule(at: datetime, status: str) -> None:
        structured.upsert_game(db, sportmonks, "Football", Fixture("557", "Trabzonspor vs Galatasaray", "Super Lig", at, None, status, [str(HOME), str(AWAY)], {"fixture": "557"}))
        db.commit()

    schedule(start, "scheduled")
    event_id = db.scalars(select(Event.id).where(Event.key == "sportmonks_football:557")).one()
    poll(event_id, sm.parse_match({**football_match("NS", 0, 0), "id": 557}), "sportmonks_football")
    q = db.scalars(select(Question)).one()
    gang = predictions.create(db, "acct-asha", "Derby", "Asha")
    predictions.join(db, "acct-ravi", gang.code, "Ravi")
    predictions.pick(db, "acct-asha", q.id, str(HOME))
    db.commit()
    schedule(start, "postponed")  # called off for the day, its date kept
    with pytest.raises(predictions.Refused) as waiting:
        predictions.pick(db, "acct-ravi", q.id, str(AWAY))
    assert (waiting.value.status, waiting.value.message) == (409, predictions.POSTPONED)
    db.rollback()
    # The old kick-off passes, the tick's sweep and a poll of it after: not closed, for it never started.
    after = start + timedelta(minutes=1)
    predictions.sweep(db, after)
    predictions.settle(db, [db.get_one(Question, q.id)], sports.of("Football"), "postponed", sm.parse_match(football_match("POSTPONED", 0, 0)).state, after)
    db.commit()
    db.expire_all()
    assert db.get_one(Question, q.id).status == "open"
    # Shown as not open to picks, with Asha's pick still hidden from her friends and the split unshown.
    [shown] = predictions.league(db, "acct-ravi", gang.code, now=start + timedelta(hours=1))["questions"]
    assert shown["status"] == "closed" and shown["friends"] == [{"name": "Asha", "you": False}]
    assert all("share" not in o and "picks" not in o for o in shown["options"])
    # Its new date: open again, closing then.
    later = start + timedelta(days=7)
    schedule(later, "scheduled")
    db.expire_all()
    q = db.get_one(Question, q.id)
    assert (q.status, q.locks_at, q.starts_at) == ("open", later, later)
    predictions.pick(db, "acct-ravi", q.id, str(AWAY), now=start + timedelta(days=1))
    db.commit()
    predictions.sweep(db, later + timedelta(minutes=1))
    db.commit()
    db.expire_all()
    assert db.get_one(Question, q.id).status == "closed"


def test_a_later_map_takes_no_pick_once_the_series_is_under_way_and_its_board_is_stale(db, redis):
    began = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5)  # the series' start time has come
    db.add(Event(id=5, key=SERIES, sport="Valorant", title="Sentinels vs NRG", start_utc=began, status="armed", live_binding={"adapter": "pandascore_esports", "locator": {"match": "777"}}))
    db.commit()
    poll(5, ps.parse_match(_match("not_started")), "pandascore_esports")  # late, as its source often is: map 1's time has gone
    qs = {q.ask: q for q in db.scalars(select(Question))}
    assert sorted(qs) == ["map2", "map3"] and [qs[k].status for k in ("map2", "map3")] == ["open", "open"]
    # Nobody's picked: a series with maps only a poll can close is polled all the same, till they settle.
    assert set(db.scalars(predictions.watched())) == {5}
    live = _match("running", 1, 0, [_game(1, "finished", SEN), _game(2, "running"), _game(3, "not_started")])
    poll(5, ps.parse_match(live), "pandascore_esports")
    db.expire_all()
    assert [db.get(Question, qs[k].id).status for k in ("map2", "map3")] == ["closed", "open"]
    # Map 3 isn't on yet: callable on a board just polled, refused once the board's minutes old (polling stopped).
    predictions.pick(db, "b", qs["map3"].id, str(NRG))
    db.commit()
    with pytest.raises(predictions.Refused) as stale:
        predictions.pick(db, "c", qs["map3"].id, str(NRG), now=datetime.now(timezone.utc) + predictions.FRESH + timedelta(minutes=1))
    assert stale.value.status == 409 and stale.value.message == predictions.STALE_BOARD
    db.rollback()


def test_a_sport_turned_off_offers_no_questions_and_takes_no_picks(db, redis):
    from sports_follow import admin

    chess_board(db, soon())
    poll(1, board(status="*"))
    q = db.scalars(select(Question)).one()
    predictions.pick(db, "fan", q.id, CARLSEN)
    db.commit()
    admin.set_sport("chess", False)
    assert predictions.today(db, "fan") == {"open": [], "settled": []} and predictions.public(db, q.id) is None
    with pytest.raises(predictions.Refused) as off:
        predictions.pick(db, "fan", q.id, "draw")
    assert (off.value.status, off.value.message) == (409, predictions.OFF)
    db.rollback()


def request(query: str = "", client: str = "8.8.8.8"):
    from starlette.requests import Request

    return Request({"type": "http", "method": "GET", "path": "/", "query_string": query.encode(), "headers": [], "client": (client, 5000)})


def test_the_match_page_lists_its_questions_and_the_pick_route_refuses_what_it_should(db, redis, monkeypatch):
    monkeypatch.setitem(server.state, "redis", Counts())
    chess_board(db, soon())
    poll(1, board(status="*"))
    address = f"{ROUND}-{CARLSEN}"
    [q] = asyncio.run(server.match_page("lichess_chess", address, "fan"))["predictions"]
    assert (q["title"], [o["key"] for o in q["options"]], q["mine"]) == ("Who wins?", [CARLSEN, "draw", LAZAVIK], None)
    with monkeypatch.context() as off:  # its source off: nothing to call, the page as it was
        off.delitem(ADAPTERS, "lichess_chess")
        assert asyncio.run(server.match_page("lichess_chess", address, "fan"))["predictions"] == []
    picked = asyncio.run(server.make_pick(request(), q["id"], {"option": CARLSEN}, "fan"))["question"]
    assert picked["mine"] == CARLSEN and all("share" not in o and "picks" not in o for o in picked["options"])
    for body, status in (({"option": "white"}, 400), ({"option": ""}, 400), ({}, 400)):
        with pytest.raises(server.HTTPException) as refused:
            asyncio.run(server.make_pick(request(), q["id"], body, "fan"))
        assert refused.value.status_code == status
    with pytest.raises(server.HTTPException) as missing:
        asyncio.run(server.make_pick(request(), 9999, {"option": CARLSEN}, "fan"))
    assert missing.value.status_code == 404
    monkeypatch.setattr(server, "PICKS_PER_ADDRESS", 1)  # one address's picks an hour
    with pytest.raises(server.HTTPException) as flooded:
        asyncio.run(server.make_pick(request(client="8.8.4.4"), q["id"], {"option": "draw"}, "other")) and asyncio.run(server.make_pick(request(client="8.8.4.4"), q["id"], {"option": "draw"}, "third"))
    assert flooded.value.status_code == 429
    # Its link previews it, on its own game's page only.
    preview = asyncio.run(server._page_preview(f"match/lichess_chess/{address}", request(f"q={q['id']}&s=share")))  # pyright: ignore[reportPrivateUsage]
    assert preview is not None and preview.path == f"/match/lichess_chess/{address}?q={q['id']}" and preview.picture.word == "Who wins?"  # too few picks for the split
    assert asyncio.run(server._page_preview("match/lichess_chess/other-1", request(f"q={q['id']}"))) is None  # pyright: ignore[reportPrivateUsage]





def test_a_series_asked_days_ahead_keeps_its_later_maps_till_it_plays(db, redis):
    start = soon() + timedelta(days=4)
    db.add(Event(id=5, key=SERIES, sport="Valorant", title="Sentinels vs NRG", start_utc=start, status="scheduled", live_binding={"adapter": "pandascore_esports", "locator": {"match": "777"}}))
    db.commit()
    poll(5, ps.parse_match(_match("not_started")), "pandascore_esports")
    predictions.sweep(db, datetime.now(timezone.utc) + predictions.STALE + timedelta(hours=1))  # three days after they were made: still to come
    db.commit()
    assert {q.ask: q.status for q in db.scalars(select(Question))} == {"map1": "open", "map2": "open", "map3": "open"}
    predictions.sweep(db, start + predictions.STALE + timedelta(hours=1))  # three days past the start, never played
    db.commit()
    db.expire_all()
    assert {(q.status, q.answer) for q in db.scalars(select(Question))} == {("void", None)}
    # A void of the sweep's is final: a board saying otherwise later doesn't settle it.
    rows = list(db.scalars(select(Question)))
    assert predictions.settle(db, rows, structured.sports.get("valorant"), "final", ps.parse_match(FINAL).state, datetime.now(timezone.utc)) == []


def test_questions_are_made_ahead_from_a_read_of_a_fans_game_coming_up(db, redis, monkeypatch):
    start = soon()
    fixtures = [f"{ROUND}-{CARLSEN}", f"{ROUND}-{LAZAVIK}"]
    read = SimpleNamespace(system="board", sports=("chess",), name="Board", fixture_pattern=re.compile(r"[\w-]+"), live_cadence=10, live_note=None, lag_note=None,
                           credit=lambda snap: "Board", budgets=lambda: (), locator=lambda fixture: {"fixture": fixture},
                           match=lambda fixture: LiveGame(source_id=fixture, title="Magnus Carlsen vs Denis Lazavik", competition="EWC", start_utc=start, snapshot=board(status="*"), fixtures=fixtures, sport="chess"))
    monkeypatch.setitem(ADAPTERS, "board", read)
    monkeypatch.setitem(KNOWN, "board", read)
    db.add_all([Player(id=1, slug="carlsen", name="Magnus Carlsen", sport="Chess"), Event(id=1, key=f"board:{fixtures[0]}", sport="Chess", title="Carlsen", start_utc=start, status="scheduled")])
    db.flush()
    db.add_all([EventPlayer(event_id=1, player_id=1), Follow(fan_id="fan", player_id=1, alert_rules={})])
    db.commit()
    assert predictions.prepare() == [("board", fixtures[0])]
    assert predictions.prepare() == []  # asked for a while ago
    match.read("board", fixtures[0])
    predictions.made("board", fixtures[0])
    q = db.scalars(select(Question)).one()
    assert (q.game, q.keys, q.event_id) == (GAME.replace("lichess_chess", "board"), [f"board:{f}" for f in fixtures], None)  # nothing polls it yet
    redis.clock += predictions.PREPARE_EVERY + 1
    assert predictions.prepare() == []  # it's asked now
    # Today has it for the fan who follows Carlsen; picking it gives it its game's event to be polled by.
    [listed] = predictions.today(db, "fan")["open"]
    assert listed["id"] == q.id and listed["mine"] is None
    predictions.pick(db, "fan", q.id, CARLSEN)
    db.commit()
    db.expire_all()
    assert db.get(Question, q.id).event_id == 1
    # Read again once it's over (its match page's, after the last poll): settled from the read.
    over = LiveGame(source_id=fixtures[0], title="Magnus Carlsen vs Denis Lazavik", competition="EWC", start_utc=start, snapshot=board(0), fixtures=fixtures, sport="chess")
    monkeypatch.setattr(read, "match", lambda fixture: over)
    match.read("board", fixtures[0])
    predictions.made("board", fixtures[0])
    db.expire_all()
    assert (db.get(Question, q.id).status, db.get(Question, q.id).answer) == ("settled", CARLSEN)


def made_up(system: str, sport: str, games: dict[str, datetime], codes: dict[str, str] | None = None) -> SimpleNamespace:
    """A source whose schedule lists its games when each starts now, and reads one the same: not started, or as its
    Sportmonks state code says (codes)."""
    return SimpleNamespace(
        system=system, sports=(sport,), name=system, fixture_pattern=re.compile(r"\d+"), live_cadence=10, live_note=None, lag_note=None,
        credit=lambda snap: system, budgets=lambda: (), locator=lambda fixture: {"fixture": fixture},
        schedule=lambda first, last: [Fixture(source_id=i, title=f"Game {i}", competition="Super Lig", start_utc=at, venue=None, status="scheduled",
                                              team_ids=[str(HOME), str(AWAY)], locator={}, sport=sport) for i, at in games.items()],
        match=lambda fixture: LiveGame(source_id=fixture, title="Trabzonspor vs Galatasaray", competition="Super Lig", start_utc=games[fixture],
                                       snapshot=sm.parse_match(football_match((codes or {}).get(fixture, "NS"), 0, 0)), sport=sport))


def test_a_fan_who_follows_nobody_has_todays_big_games_to_call(db, redis, monkeypatch):
    """Today's big games coming up within AHEAD (league.coming), in a sport that asks, are read ahead for their
    questions though nobody follows anyone in them, and Today's calls list those, under the address their cards
    have: so a big game's card says "Call it" to a fan who follows nobody. One further off waits for its day, one
    asked already isn't read again unless the league says its start moved, and a sport that asks nothing (a lobby)
    isn't read. A pick gives its game an event to be polled by."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    start, later = now + timedelta(hours=5), now + timedelta(days=3)
    pitch_games, lobby_games = {"1": start, "2": later}, {"3": start}
    pitch, lobby = made_up("pitch", "football", pitch_games), made_up("lobby", "pubg", lobby_games)
    for adapter in (pitch, lobby):
        monkeypatch.setitem(ADAPTERS, adapter.system, adapter)
        monkeypatch.setitem(KNOWN, adapter.system, adapter)
    monkeypatch.setattr(league, "sources", lambda: [pitch, lobby])
    for sport in ("football", "pubg"):
        monkeypatch.setattr(sports.BY_KEY[sport], "notable", lambda f: Notable(60, why="Derby"))
    assert league.read("pitch", now) == 2 and league.read("lobby", now) == 1
    assert predictions.today(db, "nobody")["open"] == []  # nothing's asked yet

    assert predictions.prepare(now) == [("pitch", "1")]  # the derby today; Sunday's waits, and a lobby asks nothing
    match.read("pitch", "1")
    predictions.made("pitch", "1")
    q = db.scalars(select(Question)).one()
    assert (q.game, q.keys, q.match, q.event_id, q.locks_at) == ("pitch:1", ["pitch:1"], "pitch/1", None, start)  # nothing polls it till it's picked
    redis.clock += predictions.PREPARE_EVERY + 1
    assert predictions.prepare(now) == []  # asked now: not read again
    [open_] = predictions.today(db, "nobody")["open"]
    card = next(g for g in league.offer(db, now)[0]["coming"]["games"] if g["key"] == "pitch:1")
    assert (open_["id"], open_["mine"], open_["match"]) == (q.id, None, card["match"])  # the card's address: web/src/predictions.ts callNote

    # Put back two hours, as the league's next read says: read again, so its question closes at the new start.
    pitch_games["1"] = start + timedelta(hours=2)
    league.read("pitch", now)
    assert predictions.prepare(now) == [("pitch", "1")]
    match.read("pitch", "1")
    predictions.made("pitch", "1")
    db.expire_all()
    assert (db.get_one(Question, q.id).starts_at, db.get_one(Question, q.id).locks_at) == (start + timedelta(hours=2),) * 2
    redis.clock += predictions.PREPARE_EVERY + 1
    assert predictions.prepare(now) == []  # moved now

    # The fan who follows nobody calls it: its game gets an event, which the poller follows from now on.
    predictions.pick(db, "nobody", q.id, str(HOME))
    db.commit()
    db.expire_all()
    event = db.get_one(Event, db.get_one(Question, q.id).event_id)
    assert (event.key, (event.live_binding or {}).get("adapter"), event.start_utc) == ("pitch:1", "pitch", start + timedelta(hours=2))
    assert predictions.today(db, "nobody")["open"][0]["mine"] == str(HOME)

    # Sunday's game, once its day comes (the league read again by then), is read for its own.
    day = later - timedelta(hours=1)
    league.read("pitch", day)
    assert predictions.prepare(day) == [("pitch", "2")]


def test_a_picked_big_game_nobody_follows_closes_at_its_start_as_it_moves(db, redis, monkeypatch):
    """A big game a fan who follows nobody picked has an event nothing refreshes (BHA-392): it's read again (prepare)
    every PREPARE_EVERY in the day before it, and when the league's read says its start moved into that day, and the
    read moves the event (made), so it's armed, and its question closes, by the new start, put back or brought
    forward, before the pick or after. A league read older than the game's doesn't undo it, and a game a followed
    player is in is left to their refresh."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    start = now + timedelta(hours=5)
    games = {"1": start}
    pitch = made_up("pitch", "football", games)
    monkeypatch.setitem(ADAPTERS, "pitch", pitch)
    monkeypatch.setitem(KNOWN, "pitch", pitch)
    monkeypatch.setattr(league, "sources", lambda: [pitch])
    monkeypatch.setattr(sports.BY_KEY["football"], "notable", lambda f: Notable(60, why="Derby"))

    def moves(to: datetime, reread: bool = True) -> list[tuple[str, str]]:
        """Its source moves it, and the league's next read says so (or hasn't yet); then the worker's run, once its
        last reads may be asked again."""
        games["1"] = to
        if reread:
            league.read("pitch", now)
        redis.clock += predictions.PREPARE_EVERY + 1
        due = predictions.prepare(now)
        for system, fixture in due:
            match.read(system, fixture)
            predictions.made(system, fixture)
        db.expire_all()
        return due

    def held() -> tuple[datetime | None, datetime | None]:
        """Its event's start, and its question's close."""
        return db.get_one(Event, event.id).start_utc, db.get_one(Question, q.id).locks_at

    assert moves(start) == [("pitch", "1")]  # asked
    q = db.scalars(select(Question)).one()
    assert moves(start + timedelta(hours=1)) == [("pitch", "1")]  # put back before it's picked: its question moves
    predictions.pick(db, "nobody", q.id, str(HOME))
    db.commit()
    db.expire_all()
    event = db.get_one(Event, db.get_one(Question, q.id).event_id)
    assert held() == (start + timedelta(hours=1),) * 2

    # Armed, then put back three hours after it's picked: its event starts then, disarmed till it's due again, and
    # a poll in between doesn't take its question back to the old start.
    db.execute(text("UPDATE event SET status = 'armed'"))
    db.commit()
    later = start + timedelta(hours=4)
    assert moves(later) == [("pitch", "1")]
    assert held() == (later, later) and db.get_one(Event, event.id).status == "scheduled"
    poll(event.id, sm.parse_match(football_match("NS", 0, 0)), "pitch")
    db.expire_all()
    assert db.get_one(Question, q.id).starts_at == later and held() == (later, later)
    predictions.sweep(db, start + timedelta(hours=1, minutes=1))  # its old start: still open
    db.commit()
    db.expire_all()
    assert db.get_one(Question, q.id).status == "open"

    # Put back past the day: read no more till its day comes, or the league says it's come sooner.
    far = now + predictions.AHEAD + timedelta(hours=4)
    assert moves(far) == [("pitch", "1")] and held() == (far, far)
    assert moves(far) == []
    sooner = start + timedelta(minutes=30)
    assert moves(sooner) == [("pitch", "1")] and held() == (sooner, sooner)

    # Sooner still, as its match page's read says before the league's next read: the league's older read doesn't
    # take it back (the game's read again, and says the same).
    games["1"] = soonest = start + timedelta(minutes=15)
    match.read("pitch", "1")
    predictions.made("pitch", "1")
    db.expire_all()
    assert held() == (soonest, soonest)
    assert moves(soonest, reread=False) == [("pitch", "1")]  # the league still says sooner
    assert held() == (soonest, soonest)
    predictions.sweep(db, soonest + timedelta(minutes=1))  # its new start: closed
    db.commit()
    db.expire_all()
    assert db.get_one(Question, q.id).status == "closed"

    # A followed player in it: their refresh moves its start (structured.upsert_game), not a read, nor the league.
    db.add(Player(id=1, slug="saka", name="Bukayo Saka", sport="Football"))
    db.flush()
    db.add_all([EventPlayer(event_id=event.id, player_id=1), Follow(fan_id="fan", player_id=1, alert_rules={})])
    db.commit()
    assert moves(later) == []
    match.read("pitch", "1")
    predictions.made("pitch", "1")
    db.expire_all()
    assert db.get_one(Event, event.id).start_utc == soonest


def test_a_game_picked_from_its_match_page_is_read_again_till_it_starts(db, redis, monkeypatch):
    """A game no league read lists, picked from its match page by a fan who follows nobody in it (BHA-392): only a
    read of it says it's moved, so it's read every PREPARE_EVERY in the AHEAD before its start (prepare), and it's
    armed, and its question closes, by its start as its source moves it. A postponement that keeps its date is
    taken too, so the sweep waits POSTPONED_KEEP, not STALE, past it."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    start = now + timedelta(days=2)
    games, codes = {"1": start}, {}
    pitch = made_up("pitch", "football", games, codes)
    monkeypatch.setitem(ADAPTERS, "pitch", pitch)
    monkeypatch.setitem(KNOWN, "pitch", pitch)
    monkeypatch.setattr(league, "sources", lambda: [])  # not one of Today's big games

    def run(at: datetime) -> list[tuple[str, str]]:
        """The worker's run of prepare at a time, once its last reads may be asked again."""
        redis.clock += predictions.PREPARE_EVERY + 1
        due = predictions.prepare(at)
        for system, fixture in due:
            match.read(system, fixture)
            predictions.made(system, fixture)
        db.expire_all()
        return due

    match.read("pitch", "1")  # its match page, open
    predictions.made("pitch", "1")
    q = db.scalars(select(Question)).one()
    predictions.pick(db, "nobody", q.id, str(HOME))
    db.commit()
    event_id = db.get_one(Question, q.id).event_id
    assert event_id is not None and run(now) == []  # two days off: not read yet

    day = start - timedelta(hours=6)
    games["1"] = later = start + timedelta(hours=3)  # put back, its page closed
    assert run(day) == [("pitch", "1")]
    assert predictions.prepare(day) == []  # read again no sooner than PREPARE_EVERY
    assert (db.get_one(Event, event_id).start_utc, db.get_one(Question, q.id).locks_at) == (later, later)

    codes["1"] = "POSTPONED"  # postponed, its date kept for now: the sweep waits a month, not three days
    assert run(day) == [("pitch", "1")]
    assert (db.get_one(Event, event_id).status, db.get_one(Event, event_id).start_utc) == ("postponed", later)
    predictions.sweep(db, later + predictions.STALE + timedelta(minutes=1))  # three days past its date: not voided, nor closed (BUG-109)
    assert db.scalar(select(Question.status).where(Question.id == q.id)) == "open"
    db.rollback()  # (back to before that sweep: the game's to come still)

    codes["1"], games["1"] = "NS", (sooner := start - timedelta(hours=1))  # on again, and sooner
    assert run(day) == [("pitch", "1")]
    assert (db.get_one(Event, event_id).status, db.get_one(Event, event_id).start_utc, db.get_one(Question, q.id).locks_at) == ("scheduled", sooner, sooner)
    predictions.sweep(db, sooner + timedelta(minutes=1))
    db.commit()
    db.expire_all()
    assert db.get_one(Question, q.id).status == "closed"


def test_a_campaign_shows_its_players_games_to_call_and_a_link_previews_the_split(db, redis, monkeypatch):
    monkeypatch.setattr(predictions, "SPLIT_FROM", 5)
    start = soon()
    chess_board(db, start)
    db.add(Player(id=1, slug="carlsen", name="Magnus Carlsen", sport="Chess"))
    db.flush()
    db.add(EventPlayer(event_id=2, player_id=1))  # his fixture's event: the question was made from the other one
    db.commit()
    poll(1, board(status="*"))
    assert predictions.for_players(db, "fan", [1]) == []  # his fixture's not polled, and no read listed it, yet
    poll(2, board(status="*", fide=LAZAVIK))
    q = db.scalars(select(Question)).one()
    [listed] = predictions.for_players(db, "fan", [1])
    assert listed["id"] == q.id and predictions.for_players(db, "fan", [99]) == []
    for i in range(predictions.SPLIT_FROM - 1):
        predictions.pick(db, f"fan{i}", q.id, CARLSEN)
    db.commit()
    assert all("share" not in o for o in predictions.public(db, q.id)["options"])  # too few to show anyone
    predictions.pick(db, "last", q.id, "draw")
    db.commit()
    # Enough to show it: to the fans who've picked while it's open, never to anyone (BUG-105); to anyone once it's closed.
    assert all("share" not in o for o in predictions.public(db, q.id)["options"])
    assert [o["share"] for o in predictions.shown(db, "last", [q], datetime.now(timezone.utc))[0]["options"]] == [80, 20, 0]
    poll(1, board(status="*", ongoing=True))  # the first move: it closes
    db.expire_all()
    shown = predictions.public(db, q.id)
    assert [o["share"] for o in shown["options"]] == [80, 20, 0] and shown["mine"] is None
    assert predictions.public(db, 9999) is None


# ---------------------------------------------------------------- leagues


def settled_question(db, game: str, at: datetime, right: dict[str, str], answer: str) -> Question:
    """A question settled at `at`, its picks scored."""
    q = Question(game=game, ask="result", keys=[], sport="chess", match="lichess_chess/x", title="Who wins?", options=[{"key": k, "label": k} for k in ("w", "draw", "b")],
                 status="open", locks_at=at - timedelta(hours=4))
    db.add(q)
    db.flush()
    for fan, option in right.items():
        db.add(Pick(question_id=q.id, fan_id=fan, option=option))
    db.flush()
    predictions.score(db, q, answer, at)
    db.commit()
    return q


class Counts(dict):
    """The API's Redis, as far as its limits count (server._take)."""

    async def incr(self, key: str) -> int:
        self[key] = self.get(key, 0) + 1
        return self[key]

    async def expire(self, key: str, seconds: int, nx: bool = False) -> bool:
        return True


def test_friends_play_in_a_league_by_its_invite_and_its_table_counts_from_its_making(db, redis, monkeypatch):
    monkeypatch.setitem(server.state, "redis", Counts())
    now = datetime.now(timezone.utc)
    settled_question(db, "g:0", now - timedelta(days=2), {"acct-arjun": "w", "acct-meera": "b"}, "w")  # before the league: not in its table
    with pytest.raises(server.HTTPException) as anonymous:
        asyncio.run(server.make_league({"name": "Chess gang", "member": "Arjun"}, "anon", None))
    assert anonymous.value.status_code == 401
    signed = accounts.Signed(account_id=1, fan_id="acct-arjun", session_id=1)
    made = asyncio.run(server.make_league({"name": " Chess  gang ", "member": "Arjun"}, "acct-arjun", signed))["league"]
    code = made["code"]
    assert made["name"] == "Chess gang" and re.fullmatch(f"[{predictions.CODE}]{{6}}", code) and made["members"] == 1
    meera = accounts.Signed(account_id=2, fan_id="acct-meera", session_id=2)
    assert asyncio.run(server.league_invite(request(), code.lower(), "acct-meera", meera))["invite"] == {"code": code, "name": "Chess gang", "members": 1, "by": "Arjun", "member": False}
    with pytest.raises(server.HTTPException) as unsigned:
        asyncio.run(server.join_league(code, {"member": "Meera"}, "anon", None))
    assert unsigned.value.status_code == 401
    for fan, name in (("acct-meera", "Meera"), ("acct-ravi", "Ravi")):
        asyncio.run(server.join_league(code, {"member": name}, fan, accounts.Signed(account_id=2, fan_id=fan, session_id=2)))
    server.league_shared(code, "acct-ravi", db)
    db.expire_all()
    league = db.scalars(select(League)).one()
    assert (league.opened, league.shared) == (1, 1)

    # A game to come: friends see who's picked, not what, till it closes.
    open_q = Question(game="g:1", ask="result", keys=[], sport="chess", match="lichess_chess/y", title="Who wins?", options=[{"key": k, "label": k} for k in ("w", "draw", "b")],
                      status="open", locks_at=now + timedelta(hours=3))
    db.add(open_q)
    db.flush()
    db.add_all([Pick(question_id=open_q.id, fan_id="acct-arjun", option="w"), Pick(question_id=open_q.id, fan_id="acct-meera", option="draw")])
    db.commit()
    page = server.league_page(code, "acct-meera", db)["league"]
    [coming] = page["questions"]
    assert coming["friends"] == [{"name": "Arjun", "you": False}, {"name": "Meera", "you": True, "option": "draw"}] and coming["waiting"] == ["Ravi"]
    # Settled: two of its three picks right, so 33 points each; then the table.
    db.add(Pick(question_id=open_q.id, fan_id="acct-ravi", option="w"))
    db.flush()
    predictions.score(db, open_q, "w", datetime.now(timezone.utc))
    db.commit()
    page = server.league_page(code, "acct-meera", db)["league"]
    assert [(r["place"], r["name"], r["points"], r["right"], r["you"]) for r in page["table"]] == [(1, "Arjun", 33, 1, False), (1, "Ravi", 33, 1, False), (3, "Meera", 0, 0, True)]
    [done] = page["questions"]
    assert {f["name"]: f.get("option") for f in done["friends"]} == {"Arjun": "w", "Meera": "draw", "Ravi": "w"}
    assert server.my_leagues("acct-ravi", db)["leagues"] == [{"code": code, "name": "Chess gang", "members": 3, "place": 1, "points": 33}]
    with pytest.raises(server.HTTPException) as outsider:
        server.league_page(code, "acct-dev", db)
    assert outsider.value.status_code == 404
    # Full, and the last one out takes it with them.
    predictions.MEMBERS, members = 3, predictions.MEMBERS
    try:
        with pytest.raises(predictions.Refused):
            predictions.join(db, "acct-dev", code, "Dev")
    finally:
        predictions.MEMBERS = members
    db.rollback()
    for fan in ("acct-arjun", "acct-meera", "acct-ravi"):
        server.leave_league(code, fan, db)
    assert db.scalars(select(League)).all() == []


def test_names_keep_only_what_can_be_seen_and_one_with_nothing_to_see_is_refused(db, redis):
    """BUG-112: a NUL in a league's or a member's name was a 500 (Postgres keeps none), and a name of zero-width spaces
    alone was taken. What can't be seen is taken out now (not the joiners Indic scripts and emoji are drawn with), and
    a name with nothing left to see is refused, as an empty one is."""
    gang = predictions.create(db, "acct-asha", "Der\x00by‮", "​Asha")
    predictions.join(db, "acct-ravi", gang.code, "Ra\x00vi\ud800")
    predictions.join(db, "acct-sita", gang.code, "Sita 👩‍💻")
    db.commit()
    db.expire_all()
    assert db.get_one(League, gang.id).name == "Derby"
    assert dict(db.execute(select(LeagueMember.fan_id, LeagueMember.name)).all()) == {"acct-asha": "Asha", "acct-ravi": "Ravi", "acct-sita": "Sita 👩‍💻"}
    for blank in ("​​", "\x00", "‍", " ⁠\t"):
        with pytest.raises(predictions.Refused) as nothing:
            predictions.join(db, "acct-dev", gang.code, blank)
        assert (nothing.value.status, nothing.value.message) == (400, "Give your name.")
        db.rollback()


def test_two_members_of_a_league_cant_go_by_one_name(db, redis):
    """BUG-111: Ravi could join as "Asha", the league's maker, and the table showed two Ashas (the web's rows, keyed by
    name, mixed up too). A name is one member's in a league now, whatever its case, spaces or what can't be seen in it;
    a member may keep theirs, or change it to one nobody else has."""
    gang = predictions.create(db, "acct-asha", "Derby", "Asha")
    db.commit()
    for taken in ("Asha", " asha ", "ASHA​"):
        with pytest.raises(predictions.Refused) as clash:
            predictions.join(db, "acct-ravi", gang.code, taken)
        assert clash.value.status == 409
        db.rollback()
    predictions.join(db, "acct-ravi", gang.code, "Ravi")
    predictions.join(db, "acct-ravi", gang.code, "ravi k")  # his own, changed
    predictions.join(db, "acct-ravi", gang.code, "Ravi K")
    db.commit()
    with pytest.raises(predictions.Refused):
        predictions.join(db, "acct-asha", gang.code, "RAVI K")  # the maker renaming to his
    db.rollback()
    assert sorted(db.scalars(select(LeagueMember.name))) == ["Asha", "Ravi K"]


def test_signing_in_brings_every_pick_and_those_on_games_under_way_count_in_no_league(db, redis):
    """BUG-091 (BHA-413): signing in (to join a friend's league mid-game, say) brought only the picks on games still to
    start: the rest stayed with the device's anonymous id, which no device holds after, so the fan lost those calls,
    their points and how they went. Every one comes over now; those on games already under way or over are marked
    (moved_late) and count in no league's table: calling a game three ways on three browsers and signing in on the
    right one still wins nothing there."""
    now = datetime.now(timezone.utc)
    q1 = settled_question(db, "g:1", now, {"device": "w", "other": "b"}, "w")  # over: the device's call was right
    open_q = dict(ask="result", keys=[], sport="chess", title="Who wins?", options=[{"key": "w", "label": "w"}, {"key": "b", "label": "b"}], status="open", locks_at=now + timedelta(hours=2))
    under_way = {**open_q, "status": "closed", "locks_at": now - timedelta(minutes=5)}
    q2, q3 = Question(game="g:2", match="m/2", **open_q), Question(game="g:3", match="m/3", **open_q)
    q4, q5 = Question(game="g:4", match="m/4", **under_way), Question(game="g:5", match="m/5", **under_way)
    db.add_all([q2, q3, q4, q5])
    db.add(Account(id=1, email="sana@example.com", fan_id="acct-sana"))
    db.flush()
    db.add_all([Pick(question_id=q2.id, fan_id="device", option="w"),
                Pick(question_id=q3.id, fan_id="device", option="b"), Pick(question_id=q3.id, fan_id="acct-sana", option="w"),
                Pick(question_id=q4.id, fan_id="device", option="b"), Pick(question_id=q4.id, fan_id="acct-kiran", option="w"),
                Pick(question_id=q5.id, fan_id="device", option="w"), Pick(question_id=q5.id, fan_id="acct-sana", option="b")])
    gang = predictions.create(db, "acct-sana", "Sana's lot", "Sana")  # Sana's league from another device, made before q4 settles
    predictions.join(db, "acct-kiran", gang.code, "Kiran")
    db.commit()
    accounts.sign_in(db, 1, "device")
    db.commit()
    db.expire_all()
    # Every pick comes over, those on games under way or over marked; where the account picked too, its own stands
    # (the device's on a game under way stays where it was, in the split only).
    assert {(p.question_id, p.fan_id, p.option) for p in db.scalars(select(Pick)) if p.fan_id != "other"} == {
        (q1.id, "acct-sana", "w"), (q2.id, "acct-sana", "w"), (q3.id, "acct-sana", "w"), (q4.id, "acct-sana", "b"), (q4.id, "acct-kiran", "w"),
        (q5.id, "acct-sana", "b"), (q5.id, "device", "w")}
    assert {p.question_id for p in db.scalars(select(Pick).where(Pick.fan_id == "acct-sana", Pick.moved_late))} == {q1.id, q4.id}
    # The fan sees them, with their points.
    mine = predictions.today(db, "acct-sana")
    assert {(c["id"], c["mine"], c["points"]) for c in mine["settled"]} == {(q1.id, "w", 50)}
    # q4 settles: Sana's call (made on the device before kick-off) was right, Kiran's wrong. Sana sees her points, but
    # her league's table counts only what she called as herself.
    predictions.score(db, db.get_one(Question, q4.id), "b", datetime.now(timezone.utc))
    db.commit()
    assert {(c["id"], c["points"]) for c in predictions.today(db, "acct-sana")["settled"]} == {(q1.id, 50), (q4.id, 50)}
    table = predictions.table(db, db.get_one(League, gang.id))
    assert [(r["name"], r["points"], r["played"]) for r in table] == [("Kiran", 0, 1), ("Sana", 0, 0)]
    # Deleting the account takes its own; the device's pick left in the split stays.
    predictions.create(db, "acct-sana", "Just me", "Sana")
    db.commit()
    accounts.delete_account(db, 1)
    db.commit()
    assert sorted(p.fan_id for p in db.scalars(select(Pick))) == ["acct-kiran", "device", "other"]
    [kept] = db.scalars(select(League)).all()  # the one Kiran's in stays, with nobody as its maker; the other went
    assert (kept.name, kept.owner) == ("Sana's lot", None) and db.scalars(select(LeagueMember.fan_id)).all() == ["acct-kiran"]


def test_the_admin_panel_counts_leagues_invites_and_fans_who_came_back(db, redis):
    now = datetime.now(timezone.utc)
    settled_question(db, "g:1", now - timedelta(days=2), {"a": "w", "b": "w"}, "w")
    settled_question(db, "g:2", now, {"a": "b"}, "w")
    three = predictions.create(db, "acct-1", "Three", "One")
    for fan in ("acct-2", "acct-3"):
        predictions.join(db, fan, three.code, fan)
    predictions.create(db, "acct-4", "Alone", "Four")
    predictions.was_shared(db, "acct-1", three.code)
    predictions.invite(db, three.code, "acct-9")
    db.commit()
    assert predictions.numbers(db) == {"leagues": 2, "leagues_3": 1, "invites_shared": 1, "invites_opened": 1, "pickers": 2, "picks": 3, "game_day_returns": 1}
