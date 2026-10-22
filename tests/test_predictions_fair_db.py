"""Calling games fairly, against real rows (predictions.py): a game with no start time takes no pick once it's begun
(BUG-107); and a league's page keeps its calls still open, however many its members have settled lately (BUG-108).
Against the throwaway database tests/test_predictions_db.py uses (skipped without one)."""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from sports_follow import admin, predictions
from sports_follow.adapters import lichess_chess
from sports_follow.models import Event, EventState, Pick, Question

from .test_new_adapters import CARLSEN
from .test_predictions_db import URL, db, redis, running, settled_question  # noqa: F401  (the fixtures)

pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

OPTIONS = (("w", "Carlsen"), ("draw", "Draw"), ("b", "Lazavik"))


def open_question(db, locks: datetime, game: str = "g1", sport: str = "chess", adapter: str = "lichess_chess", event_id: int = 1) -> Question:  # noqa: F811
    """A question still to close at `locks`, polled by an event whose source runs."""
    if db.get(Event, event_id) is None:
        db.add(Event(id=event_id, key=f"{adapter}:{game}", sport=sport.title(), title="Carlsen vs Lazavik", start_utc=locks, status="scheduled",
                     live_binding={"adapter": adapter, "locator": {}, "cadence": 10}))
        db.flush()
    q = Question(game=f"{adapter}:{game}", ask="result", keys=[f"{adapter}:{game}"], event_id=event_id, sport=sport, match=f"{adapter}/{game}", title="Who wins?",
                 game_title="Carlsen vs Lazavik", options=[{"key": k, "label": label} for k, label in OPTIONS], status="open", starts_at=locks, locks_at=locks)
    db.add(q)
    db.commit()
    return q


def league_of(db, *names: str) -> str:  # noqa: F811
    """A league of these friends (their account fan ids are acct-<name>), its invite code."""
    made = predictions.create(db, f"acct-{names[0].lower()}", "The gang", names[0])
    for name in names[1:]:
        predictions.join(db, f"acct-{name.lower()}", made.code, name)
    db.commit()
    return made.code


# ---------------------------------------------------------------- BUG-107


def _no_start_board(now: datetime, final: bool):
    """A Lichess round with no start time (startsAfterPrevious: a knockout's next round), its one game to come or
    over (1-0)."""
    data = json.load(gzip.open(Path(__file__).parent / "fixtures" / "lichess" / "round-final.json.gz", "rt"))
    played = data["games"][0]
    rnd = {k: v for k, v in data["round"].items() if k != "startsAt"} | {"finished": final, "ongoing": False, "startsAfterPrevious": True}
    return lichess_chess.parse_board({**data, "round": rnd, "games": [{**played, "status": played["status"] if final else "*"}]}, CARLSEN, now)


def test_a_game_with_no_start_time_takes_no_pick_once_its_over(db, redis):  # noqa: F811
    now = datetime.now(timezone.utc)
    key = f"lichess_chess:nostart-{CARLSEN}"
    db.add(Event(id=1, key=key, sport="Chess", title="Magnus Carlsen vs Denis Lazavik", start_utc=None, status="scheduled",
                 live_binding={"adapter": "lichess_chess", "locator": {"round": "nostart", "fide": CARLSEN}, "cadence": 10}))
    db.commit()
    to_come = _no_start_board(now, final=False)
    assert to_come.status == "scheduled"
    # One asked before a start was needed: a pick needs a fresh board that says the game's still to come.
    q = Question(game="lichess_chess:nostart", ask="result", keys=[key], event_id=1, sport="chess", match=f"lichess_chess/nostart-{CARLSEN}", title="Who wins?",
                 options=[{"key": k, "label": k} for k in (CARLSEN, "draw", "lazavik")], status="open", starts_at=None, locks_at=None)
    db.add(q)
    db.commit()
    with pytest.raises(predictions.Refused) as stale:
        predictions.pick(db, "asha", q.id, CARLSEN, now)
    assert stale.value.status == 409
    db.rollback()
    db.add(EventState(event_id=1, as_of=now, status=to_come.status, score_label=to_come.score_label, clock_label=to_come.clock_label, state=to_come.state))
    db.commit()
    predictions.pick(db, "meera", q.id, "draw", now)
    db.commit()

    # It's over, and the server knows: no pick now, so none to score.
    over = _no_start_board(now, final=True)
    db.get_one(Event, 1).status = "final"
    db.add(EventState(event_id=1, as_of=now + timedelta(seconds=1), status=over.status, score_label=over.score_label, clock_label=over.clock_label, state=over.state))
    db.commit()
    with pytest.raises(predictions.Refused) as shut:
        predictions.pick(db, "asha", q.id, CARLSEN, now + timedelta(seconds=2))
    assert shut.value.status == 409
    db.rollback()
    db.expire_all()
    assert {p.fan_id for p in db.scalars(select(Pick))} == {"meera"}

    # And nothing's asked of a game with no start now: nothing would close it in time.
    _, rows = predictions.ensure(db, "lichess_chess", f"nostart-{CARLSEN}", "chess", to_come.status, to_come.state, None, "Magnus Carlsen vs Denis Lazavik", [key], 1, now)
    assert rows == [] and db.scalars(select(Question.id)).all() == [q.id]


# ---------------------------------------------------------------- BUG-108


def test_a_league_page_keeps_its_open_calls_however_many_settled_lately(db, redis):  # noqa: F811
    now = datetime.now(timezone.utc)
    code = league_of(db, "Asha", "Ravi")
    for i in range(21):  # Ravi's busy day: 21 calls settled this morning
        settled_question(db, f"g:settled-{i}", now - timedelta(hours=5, minutes=i), {"acct-ravi": "w"}, "w")
    q = open_question(db, now + timedelta(hours=3))
    for fan in ("acct-asha", "acct-ravi"):
        predictions.pick(db, fan, q.id, "w")
    # And 20 calls of a sport that's off now, closing sooner: they don't take its place either.
    for i in range(20):
        off = open_question(db, now + timedelta(hours=1), game=f"m{i}", sport="valorant", adapter="pandascore_esports", event_id=100 + i)
        predictions.pick(db, "acct-ravi", off.id, "w")
    db.commit()
    admin.set_sport("valorant", False)
    listed = predictions.league(db, "acct-asha", code)["questions"]
    assert listed[0]["id"] == q.id and listed[0]["waiting"] == [] and {item["sport"] for item in listed} == {"chess"}
    assert len([item for item in listed if item["status"] == "settled"]) >= 1  # and the settled ones after it
