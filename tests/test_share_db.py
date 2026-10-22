"""Link previews against real rows (share.py, server._page_preview), as a crawler asks for them: a league's invite
(/j/<code>) and a team's page (/team/<id>), BUG-192. A preview reads what anyone with the link may see, counts no
opening and asks no Redis. These run against a throwaway database at alembic head and are skipped without one
(SPORTS_FOLLOW_TEST_DATABASE_URL, tests/test_accounts_db.py says how to make it)."""

from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from sports_follow import bus, predictions, server, share, sports
from sports_follow.models import League, Team

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")


@pytest.fixture
def db(monkeypatch):
    engine = create_engine(URL or "postgresql+psycopg://")
    with engine.begin() as c:
        c.execute(text("TRUNCATE prediction_league, prediction_member, team RESTART IDENTITY CASCADE"))
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

    monkeypatch.setattr(server, "session", unit)
    monkeypatch.setattr(bus, "sync_redis", lambda: pytest.fail("a preview asked Redis"))
    s = made()
    yield s
    s.close()
    engine.dispose()


def preview(path: str) -> share.Preview | None:
    """The preview the server writes into the page at `path` (no query)."""
    scope = {"type": "http", "method": "GET", "path": f"/{path}", "query_string": b"", "headers": [], "scheme": "https", "server": ("stann.in", 443)}
    return asyncio.run(server._page_preview(path, Request(scope)))


def test_an_invites_preview_says_the_league_who_made_it_and_how_many_are_in_it_and_counts_no_opening(db):
    league = predictions.create(db, "acct-asha", "Chess gang", "Asha")
    for fan, name in (("acct-ravi", "Ravi"), ("acct-meera", "Meera")):
        predictions.join(db, fan, league.code, name)
    db.commit()
    shown = preview(f"j/{league.code.lower()}")  # typed in lower case, as a code may be
    assert shown == share.of_invite({"code": league.code, "name": "Chess gang", "members": 3, "by": "Asha"})
    got = asyncio.run(server.invite_picture(league.code))
    assert got.status_code == 200 and got.body == share.png(shown.picture)
    db.expire_all()
    assert db.get_one(League, league.id).opened == 0  # a crawler's look isn't a fan opening the invite
    assert preview("j/ZZZZZZ") is None
    with pytest.raises(server.HTTPException) as missing:
        asyncio.run(server.invite_picture("ZZZZZZ"))
    assert missing.value.status_code == 404
    # Its maker left: still an invite, by no one's name.
    predictions.leave(db, "acct-asha", league.code)
    db.commit()
    left = preview(f"j/{league.code}")
    assert left is not None and left.picture.lines == ("You're asked to call the games together", "2 in it so far")


def test_a_teams_preview_says_its_last_result_and_a_sport_turned_off_has_none(db, monkeypatch):
    card = {"upcoming": [{"title": "Indiana Fever v Chicago Sky", "competition": "WNBA", "start_utc": "2099-06-01T23:00:00+00:00", "status": "scheduled"}],
            "recent": [{"title": "Las Vegas Aces v Indiana Fever", "result": "Won 88–80", "competition": "WNBA"}], "squad": []}
    team = Team(name="Indiana Fever", sport="basketball", competitions=["WNBA"], system="espn_basketball", external_id="wnba:5", country="United States", card=card)
    db.add(team)
    db.commit()
    shown = preview(f"team/{team.id}")
    assert shown is not None and (shown.title, shown.path) == ("Indiana Fever · Basketball", f"/team/{team.id}")
    assert shown.picture.lines == ("Last game: Las Vegas Aces v Indiana Fever", "Won 88–80")
    got = asyncio.run(server.team_picture(team.id))
    assert got.status_code == 200 and got.body == share.png(shown.picture)
    assert preview(f"team/{team.id + 1}") is None
    monkeypatch.setattr(sports, "on", lambda key: key != "basketball")  # turned off: not offered, nor previewed
    assert preview(f"team/{team.id}") is None
    with pytest.raises(server.HTTPException) as off:
        asyncio.run(server.team_picture(team.id))
    assert off.value.status_code == 404
