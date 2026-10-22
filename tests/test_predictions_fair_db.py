"""Calling games fairly, against real rows (predictions.py): friends' picks stay theirs till the question closes, and
an open question's split moves too coarsely to give one away (BUG-105); a pick from a fan the request itself made up
is that fan's, but counts in nobody else's split or points (BUG-106); a game with no start time takes no pick once
it's begun (BUG-107); and a league's page keeps its calls still open, however many its members have settled lately
(BUG-108). Against the throwaway database tests/test_predictions_db.py uses (skipped without one)."""

from __future__ import annotations

import asyncio
import gzip
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from sports_follow import admin, predictions, server, share
from sports_follow.adapters import lichess_chess
from sports_follow.config import FAN_COOKIE
from sports_follow.models import Event, EventState, Pick, Question

from .test_api import KEY, Redis as ApiRedis
from .test_new_adapters import CARLSEN
from .test_predictions_db import URL, db, redis, request, running, settled_question  # noqa: F401  (the fixtures)

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


def crowd(db, q: Question, tag: str = "crowd", **picks: int) -> None:  # noqa: F811
    """Fans who aren't in the league picking: so many of each answer, each a new fan (<tag>-<answer>-<n>)."""
    for option, n in picks.items():
        for i in range(n):
            predictions.pick(db, f"{tag}-{option}-{i}", q.id, option)
    db.commit()


def league_of(db, *names: str) -> str:  # noqa: F811
    """A league of these friends (their account fan ids are acct-<name>), its invite code."""
    made = predictions.create(db, f"acct-{names[0].lower()}", "The gang", names[0])
    for name in names[1:]:
        predictions.join(db, f"acct-{name.lower()}", made.code, name)
    db.commit()
    return made.code


def call_of(db, fan: str, code: str, qid: int) -> dict:  # noqa: F811
    db.expire_all()
    return next(item for item in predictions.league(db, fan, code)["questions"] if item["id"] == qid)


def split(item: dict) -> list[int | None]:
    return [o.get("share") for o in item["options"]]


# ---------------------------------------------------------------- BUG-105


def test_friends_picks_cant_be_worked_out_before_it_closes_and_nobody_sees_an_open_split_unpicked(db, redis):  # noqa: F811
    now = datetime.now(timezone.utc)
    q = open_question(db, now + timedelta(hours=3))
    crowd(db, q, w=7, draw=7, b=6)
    code = league_of(db, "Asha", "Ravi", "Meera")
    predictions.pick(db, "acct-asha", q.id, "w")
    db.commit()
    before = call_of(db, "acct-asha", code, q.id)
    predictions.pick(db, "acct-ravi", q.id, "b")
    db.commit()
    after = call_of(db, "acct-asha", code, q.id)
    # Asha sees that Ravi has picked, never what: not by his name, nor by how the split or its count moved.
    assert [(f["name"], f.get("option")) for f in after["friends"]] == [("Asha", "w"), ("Ravi", None)] and after["waiting"] == ["Meera"]
    assert after["picks"] != 22 and before["picks"] != 21  # never the exact count while it's open
    assert (split(after), after["picks"], after["worth"]) == (split(before), before["picks"], before["worth"])
    assert all(s is not None for s in split(after))
    # The split moves once enough more have picked that one friend's pick is lost among them.
    crowd(db, q, "later", draw=10)
    moved = call_of(db, "acct-asha", code, q.id)
    assert split(moved) != split(after) and moved["picks"] != 32

    # Nobody who hasn't picked sees how it splits while it's open: not its link preview, its picture or its tags.
    public = predictions.public(db, q.id)
    assert public is not None and all("share" not in o for o in public["options"]) and public["picks"] != 32
    assert share.of_question(public).picture.word == "Who wins?"
    preview = asyncio.run(server._page_preview(f"match/{q.match}", request(f"q={q.id}")))  # pyright: ignore[reportPrivateUsage]
    assert preview is not None and "%" not in preview.title and "%" not in preview.picture.word

    # It starts: every friend's pick shows, and the split and its count as they are.
    db.get_one(Question, q.id).locks_at = now - timedelta(minutes=1)
    db.commit()
    closed = call_of(db, "acct-asha", code, q.id)
    assert [(f["name"], f.get("option")) for f in closed["friends"]] == [("Asha", "w"), ("Ravi", "b")] and closed["waiting"] == []
    assert closed["picks"] == 32 and split(closed) == [25, 53, 22] and [o["picks"] for o in closed["options"]] == [8, 17, 7]
    shown = predictions.public(db, q.id)
    assert shown is not None and split(shown) == [25, 53, 22] and share.of_question(shown).picture.word == "53%"


# ---------------------------------------------------------------- BUG-106


def test_a_pick_from_a_fan_the_request_made_up_is_theirs_but_counts_in_nobody_elses_split_or_points(db, redis, monkeypatch):  # noqa: F811
    monkeypatch.setitem(server.state, "redis", ApiRedis())
    monkeypatch.setattr(server, "cookie_key", lambda: KEY)
    now = datetime.now(timezone.utc)
    q = open_question(db, now + timedelta(hours=3))
    crowd(db, q, w=7, draw=7, b=6)

    def put(option: str, fan: str | None = None, address: str = "81.2.69.142") -> httpx.Response:
        async def go() -> httpx.Response:
            transport = httpx.ASGITransport(app=server.app, client=(address, 50000))
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                headers = {"Cookie": f"{FAN_COOKIE}={server.sign_fan(fan)}"} if fan else {}
                return await client.put(f"/api/predictions/{q.id}/pick", json={"option": option}, headers=headers)

        answer = asyncio.run(go())
        assert answer.status_code == 200, answer.text
        return answer

    # Three picks with no cookie: three new fans, each with their pick (and the cookie that keeps it theirs).
    made_up = []
    for _ in range(3):
        answer = put("draw")
        assert answer.json()["question"]["mine"] == "draw"
        made_up.append(server._signed(answer.cookies[FAN_COOKIE])[0])  # pyright: ignore[reportPrivateUsage, reportOptionalSubscript]
    put("b", fan="a-fan-with-a-cookie")  # a fan whose cookie came with the pick: they count
    predictions.pick(db, "crowd-w-0", q.id, "w", counted=False)  # and a pick that counted once always does
    db.commit()
    db.expire_all()
    assert {p.fan_id for p in db.scalars(select(Pick).where(Pick.option == "draw"))} >= set(made_up)

    db.get_one(Question, q.id).locks_at = now - timedelta(minutes=1)
    db.commit()
    [closed] = predictions.shown(db, made_up[0], [db.get_one(Question, q.id)], now)
    assert closed["picks"] == 21 and [o["picks"] for o in closed["options"]] == [7, 7, 7] and closed["mine"] == "draw"
    predictions.score(db, db.get_one(Question, q.id), "draw", now)
    db.commit()
    db.expire_all()
    scored = {p.fan_id: p.points for p in db.scalars(select(Pick).where(Pick.option == "draw"))}
    # A third of the fans who count called it: 67 each, the made-up fans' own picks included; not 58, as 10 of 24.
    assert scored == {**{f"crowd-draw-{i}": 67 for i in range(7)}, **{fan: 67 for fan in made_up}}



def test_cookies_from_one_address_count_only_a_few_picks_a_question_however_they_were_got(db, redis, monkeypatch):  # noqa: F811
    """The app's page gives any device a fan cookie (BUG-152), so a cookie is no proof of a fan: a script could fetch
    one per fake fan and stuff a split. From one address only COUNTED_PER_ADDRESS fans' picks on a question count (an
    account's always do); the rest are theirs, scored, and move nobody's numbers. A fan counted once stays counted."""
    monkeypatch.setitem(server.state, "redis", ApiRedis())
    monkeypatch.setattr(server, "cookie_key", lambda: KEY)
    now = datetime.now(timezone.utc)
    q = open_question(db, now + timedelta(hours=3))
    other = open_question(db, now + timedelta(hours=3), game="g2", event_id=2)

    def put(qid: int, option: str, fan: str, address: str = "81.2.69.142") -> None:
        async def go() -> httpx.Response:
            transport = httpx.ASGITransport(app=server.app, client=(address, 50000))
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.put(f"/api/predictions/{qid}/pick", json={"option": option}, headers={"Cookie": f"{FAN_COOKIE}={server.sign_fan(fan)}"})

        answer = asyncio.run(go())
        assert answer.status_code == 200, answer.text
        assert answer.json()["question"]["mine"] == option

    cap = server.COUNTED_PER_ADDRESS
    for i in range(cap + 10):
        put(q.id, "b", f"script-{i}")  # one address, a cookie each
    put(q.id, "b", "elsewhere", address="81.2.69.160")  # another address
    put(q.id, "w", "script-0")  # a counted fan changing their pick
    put(other.id, "b", f"script-{cap + 5}")  # another question: its own few
    db.expire_all()
    counted = dict(db.execute(select(Pick.fan_id, Pick.counted).where(Pick.question_id == q.id)).all())
    assert sum(counted.values()) == cap + 1 and counted["elsewhere"] and counted["script-0"] and not counted[f"script-{cap + 5}"]
    assert db.scalar(select(Pick.counted).where(Pick.question_id == other.id))


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


def test_signing_in_counts_a_devices_open_picks_and_leaves_a_closed_ones_split_as_it_stood(db, redis):  # noqa: F811
    """A pick from a fan the request made up (counted False) moved into an account (merge, BUG-091): on a question
    still open it counts from now, as an account's does; on one closed it stays as it was, so a split already shown
    doesn't move."""
    now = datetime.now(timezone.utc)
    still = open_question(db, now + timedelta(hours=3))
    gone = open_question(db, now + timedelta(hours=3), game="g2", event_id=2)
    for q in (still, gone):
        predictions.pick(db, "device", q.id, "w", counted=False)
    gone.locks_at = now - timedelta(minutes=1)
    db.commit()
    predictions.merge(db, "device", "acct-asha")
    db.commit()
    db.expire_all()
    rows = {qid: (counted, late) for qid, counted, late in db.execute(select(Pick.question_id, Pick.counted, Pick.moved_late).where(Pick.fan_id == "acct-asha"))}
    assert rows == {still.id: (True, False), gone.id: (False, True)}
