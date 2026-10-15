"""SQLAlchemy engine and session factory."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

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
    async route would hold it on the event loop, and a stream for its whole life."""
    with session() as db:
        yield db
