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

from sports_follow import accounts, bus, engine, live, match, notify, pipeline, predictions, server, structured
from sports_follow.adapters import ADAPTERS, KNOWN, LiveGame, pandascore_esports as ps
from sports_follow.models import Account, Event, EventPlayer, Follow, League, LeagueMember, Pick, Player, Question

from .test_match_follow import Store
from .test_new_adapters import CARLSEN, LAZAVIK, load
from .test_pandascore import FINAL, NRG, SEN, _game, _match
from .test_predictions import board

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
                       "push_subscription, prediction_question, prediction_pick, prediction_league, prediction_member, campaign RESTART IDENTITY CASCADE"))
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
    """Both sources run here, as in production: a pick needs its game's source on, to be settled."""
    for system in ("lichess_chess", "pandascore_esports"):
        if system not in ADAPTERS:
            monkeypatch.setitem(ADAPTERS, system, SimpleNamespace(system=system, sports=("chess",) if system == "lichess_chess" else ("valorant",)))


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
    assert asyncio.run(server.league_invite(code.lower(), "acct-meera"))["invite"] == {"code": code, "name": "Chess gang", "members": 1, "by": "Arjun", "member": False}
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


def test_signing_in_brings_a_devices_open_picks_and_deleting_the_account_takes_its_own(db, redis):
    now = datetime.now(timezone.utc)
    q1 = settled_question(db, "g:1", now, {}, "w")
    open_q = dict(ask="result", keys=[], sport="chess", title="Who wins?", options=[{"key": "w", "label": "w"}, {"key": "b", "label": "b"}], status="open", locks_at=now + timedelta(hours=2))
    q2, q3 = Question(game="g:2", match="m/2", **open_q), Question(game="g:3", match="m/3", **open_q)
    db.add_all([q2, q3])
    account = Account(id=1, email="sana@example.com", fan_id="acct-sana")
    db.add(account)
    db.flush()
    db.add_all([Pick(question_id=q1.id, fan_id="device", option="w", points=50), Pick(question_id=q2.id, fan_id="device", option="w"),
                Pick(question_id=q3.id, fan_id="device", option="b"), Pick(question_id=q3.id, fan_id="acct-sana", option="w")])
    db.commit()
    accounts.sign_in(db, 1, "device")
    db.commit()
    # Picks still open come over (the account's own standing where both picked); one already scored stays the
    # device's: calling a game three ways on three browsers and signing in on the right one wins nothing.
    assert {(p.question_id, p.fan_id, p.option) for p in db.scalars(select(Pick))} == {(q1.id, "device", "w"), (q2.id, "acct-sana", "w"), (q3.id, "acct-sana", "w")}
    made = predictions.create(db, "acct-sana", "Sana's lot", "Sana")
    predictions.create(db, "acct-sana", "Just me", "Sana")
    predictions.join(db, "acct-kiran", made.code, "Kiran")
    db.commit()
    accounts.delete_account(db, 1)
    db.commit()
    assert [p.fan_id for p in db.scalars(select(Pick))] == ["device"]
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
