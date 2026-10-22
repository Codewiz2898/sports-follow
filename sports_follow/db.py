"""SQLAlchemy engine and session factory."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import DATABASE_URL


def _engine(connections: int):
    # With every pooled connection in use, a checkout gives up after a few seconds rather than holding its
    # request (and thread) for SQLAlchemy's default 30.
    # hide_parameters: a failed statement's error message leaves out its values (a fan's id), since it's
    # logged with its stack and recorded on its trace (logs.py, tracing.py).
    return create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5, max_overflow=connections - 5, pool_timeout=5, future=True, hide_parameters=True)


engine = _engine(15)  # the API: routes take a connection for a query or two, streams none
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def size_pool(connections: int) -> None:
    """Give this process `connections` pooled connections, before it uses any. The worker asks for one
    per thread that can reach the database, so none of its jobs waits on the pool."""
    global engine
    old, engine = engine, _engine(connections)
    SessionLocal.configure(bind=engine)
    old.dispose()


@contextmanager
def session() -> Iterator[Session]:
    """A unit of work: commits on success, rolls back on error."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency, for sync routes only: the session lives until the response is sent, so an
    async route would hold it on the event loop, and a stream for its whole life. A sync route commits it
    as it returns (released), so its connection goes back before the answer is made."""
    with session() as db:
        yield db


def released(route: Callable[..., Any]) -> Callable[..., Any]:
    """A sync route that commits the sessions it was given (get_db's) on its own thread as it returns, so their
    pooled connections are back before FastAPI checks its answer, on another of the same threads, and sends it.
    Else a burst of requests stood still: every connection held by an answer waiting for a thread, every thread
    by a request waiting for a connection, until the pool gave up (BUG-264). A route that raises isn't committed:
    get_db rolls it back, as before. Async routes and streams are returned as they are."""
    if inspect.iscoroutinefunction(route) or inspect.isgeneratorfunction(route) or inspect.isasyncgenfunction(route):
        return route

    @wraps(route)
    def run(*args: Any, **kwargs: Any) -> Any:
        answer = route(*args, **kwargs)
        for value in kwargs.values():
            if isinstance(value, Session):
                value.commit()
        return answer

    return run
