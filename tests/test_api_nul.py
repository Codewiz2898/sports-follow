"""A NUL character anywhere a client can put text (a JSON body, a path, a query) is refused with a 400 before any
route sees it (BUG-096). Postgres can't keep one, so a sign-in address, a clip's address or a league member's name
with one in it failed in the query as a server error: anyone could raise the API's 5xx alerts at will. The database
here is test_api's fake, refusing a NUL as Postgres does.
"""

from __future__ import annotations

from contextlib import contextmanager

import psycopg
import pytest
from sqlalchemy.dialects import postgresql

from sports_follow import clipstore, db as db_module, mail, server
from tests.test_api import PUBLIC, Api, Db, api  # noqa: F401  (api: the fixture)


def _texts(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, (list, tuple, set)):
        for v in value:
            yield from _texts(v)


class Postgres(Db):
    """test_api's database, but a NUL in any value it's given fails as psycopg fails it, before the query goes."""

    def _check(self, *values):
        if any("\x00" in text for text in _texts(values)):
            raise psycopg.DataError("PostgreSQL text fields cannot contain NUL (0x00) bytes")

    def _bound(self, stmt):
        self._check(*stmt.compile(dialect=postgresql.dialect()).params.values())

    def get(self, model, key):
        self._check(key)
        return super().get(model, key)

    def scalar(self, stmt):
        self._bound(stmt)
        return super().scalar(stmt)

    def scalars(self, stmt):
        self._bound(stmt)
        return super().scalars(stmt)

    def execute(self, stmt):
        self._bound(stmt)
        return super().execute(stmt)


@pytest.fixture
def postgres(api, monkeypatch):
    @contextmanager
    def session():
        db = Postgres(api)
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    monkeypatch.setattr(server, "session", session)
    monkeypatch.setattr(db_module, "session", session)
    monkeypatch.setattr(mail, "configured", lambda https: True)
    sent: dict[str, str] = {}
    monkeypatch.setattr(mail, "send_code", lambda email, code: sent.__setitem__(email, code))
    monkeypatch.setattr(clipstore, "STORE", clipstore.MemoryStore())
    return sent


@pytest.mark.parametrize("method, path, body", [
    ("POST", "/api/auth/email/start", {"email": "fan\x00x@example.com"}),  # the sign-in address
    ("POST", "/api/auth/email/start", {"email": "fan@example.com", "more": [{"deep": "a\x00b"}]}),  # anywhere in the body
    ("GET", "/api/clips/a%00b", None),  # a clip's address
    ("POST", "/api/clips/a%00b/report", {}),
    ("GET", "/api/clips?match=a%00b", None),  # a query
])
def test_a_nul_anywhere_is_refused_plainly_never_a_500(api, postgres, method, path, body):
    answer = api.call(method, path, body=body, address=PUBLIC)
    assert answer.status_code == 400 and "NUL" in answer.json()["detail"]
    assert postgres == {}  # no code went out


def test_text_that_only_spells_a_nul_out_is_taken(api, postgres):
    """A backslash and "u0000" typed as they are is text like any other."""
    assert api.call("POST", "/api/auth/email/start", body={"email": "fan\\u0000x@example.com"}, address=PUBLIC).status_code == 200
    assert list(postgres) == ["fan\\u0000x@example.com"]
    assert api.call("GET", "/api/clips?match=a%2500b", address=PUBLIC).status_code == 200  # "%00", spelt out
