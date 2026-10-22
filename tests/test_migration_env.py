"""How migrations run (migrations/env.py), against a throwaway database at alembic head: each is its own transaction,
so one that fails leaves those before it in, and none waits long for a lock. Two made-up migrations run with the repository's env.py, in a schema of
the test's own: the database's own tables and alembic_version aren't touched. Skipped without one; see
tests/test_players.py for setting it up."""

from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, ProgrammingError

from sports_follow import config

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

ENV = Path(__file__).parent.parent / "migrations" / "env.py"
SCHEMA = "migration_env_test"
FIRST = '''revision = "m1"
down_revision = None
from alembic import op
def upgrade():
    op.execute("CREATE TABLE fresh (id integer)")
'''
SECOND = '''revision = "m2"
down_revision = "m1"
from alembic import op
def upgrade():
    op.execute("ALTER TABLE {table} ADD COLUMN extra integer")
'''


def test_each_migration_is_its_own_transaction(tmp_path, monkeypatch):
    # BUG-224: every pending migration of a deploy ran in one transaction, so the locks the first ones took (an ALTER's
    # on a table fans read) were held until the last committed, while the old code served; and one that failed took
    # the others back with it.
    engine = create_engine(URL or "")
    with engine.begin() as c:
        c.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        c.execute(text(f"CREATE SCHEMA {SCHEMA}"))
    scripts = tmp_path / "migrations"
    (scripts / "versions").mkdir(parents=True)
    shutil.copy(ENV, scripts / "env.py")
    (scripts / "versions" / "m1.py").write_text(FIRST)
    (scripts / "versions" / "m2.py").write_text(SECOND.format(table="missing"))  # a migration that fails
    monkeypatch.setattr(config, "DATABASE_URL", f"{URL}{'&' if '?' in (URL or '') else '?'}options=-csearch_path={SCHEMA}")
    cfg = Config()
    cfg.set_main_option("script_location", str(scripts))
    try:
        with pytest.raises(ProgrammingError):
            command.upgrade(cfg, "head")
        with engine.begin() as c:
            assert c.execute(text(f"SELECT version_num FROM {SCHEMA}.alembic_version")).scalars().all() == ["m1"]  # the first stayed in
            assert c.execute(text(f"SELECT to_regclass('{SCHEMA}.fresh')")).scalar() is not None
        (scripts / "versions" / "m2.py").write_text(SECOND.format(table="fresh"))  # fixed: the next deploy goes on from m1
        command.upgrade(cfg, "head")
        with engine.begin() as c:
            assert c.execute(text(f"SELECT version_num FROM {SCHEMA}.alembic_version")).scalars().all() == ["m2"]
    finally:
        with engine.begin() as c:
            c.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        engine.dispose()


HELD = '''revision = "m1"
down_revision = None
from alembic import op
def upgrade():
    op.execute("CREATE TABLE pieces (id integer)")
'''
ALTER = '''revision = "m2"
down_revision = "m1"
from alembic import op
def upgrade():
    op.execute("ALTER TABLE pieces ADD COLUMN extra integer")
'''
CONCURRENTLY = '''revision = "m2"
down_revision = "m1"
from alembic import op
def upgrade():
    with op.get_context().autocommit_block():
        op.drop_index("ix_pieces_id", table_name="pieces", postgresql_concurrently=True, if_exists=True)
        op.create_index("ix_pieces_id", "pieces", ["id"], postgresql_concurrently=True)
'''


@pytest.mark.parametrize("second, holding", [
    (ALTER, "SELECT * FROM pieces"),  # a read of the table, in a transaction that stays open
    (CONCURRENTLY, "INSERT INTO pieces VALUES (1)"),  # a write to the table, in a transaction that stays open
])
def test_a_migration_waiting_for_a_lock_gives_up_and_the_next_deploy_goes_on(tmp_path, monkeypatch, second, holding):
    # BUG-224: no lock_timeout, so an ALTER waiting behind an open transaction (a worker job's) queued every later query
    # on its table behind it while the old code served. BUG-318: a CONCURRENTLY build waits for the transactions writing
    # to its table, so a long one held the deploy at its migrations until GitHub cut it off, leaving the index invalid.
    # Each now gives up after the lock timeout: the deploy stops with nothing replaced, and the next one goes on.
    engine = create_engine(URL or "")
    with engine.begin() as c:
        c.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        c.execute(text(f"CREATE SCHEMA {SCHEMA}"))
    scripts = tmp_path / "migrations"
    (scripts / "versions").mkdir(parents=True)
    shutil.copy(ENV, scripts / "env.py")
    (scripts / "versions" / "m1.py").write_text(HELD)
    monkeypatch.setattr(config, "DATABASE_URL", f"{URL}{'&' if '?' in (URL or '') else '?'}options=-csearch_path={SCHEMA}")
    monkeypatch.setenv("SPORTS_FOLLOW_MIGRATION_LOCK_TIMEOUT", "1s")
    cfg = Config()
    cfg.set_main_option("script_location", str(scripts))
    holder = engine.connect()
    ended = threading.Timer(4, holder.rollback)  # without a timeout, the migration waits until the job's transaction ends
    try:
        command.upgrade(cfg, "m1")
        (scripts / "versions" / "m2.py").write_text(second)
        holder.execute(text(f"SET search_path = {SCHEMA}"))
        holder.execute(text(holding))
        ended.start()
        started = time.monotonic()
        with pytest.raises(OperationalError, match="lock timeout"):
            command.upgrade(cfg, "head")
        assert time.monotonic() - started < 3.5
        ended.cancel()
        holder.rollback()
        with engine.begin() as c:
            assert c.execute(text(f"SELECT version_num FROM {SCHEMA}.alembic_version")).scalars().all() == ["m1"]
        command.upgrade(cfg, "head")  # the next deploy, once the job is done
        with engine.begin() as c:
            assert c.execute(text(f"SELECT version_num FROM {SCHEMA}.alembic_version")).scalars().all() == ["m2"]
            invalid = c.execute(text("SELECT count(*) FROM pg_index i JOIN pg_class t ON t.oid = i.indrelid JOIN pg_namespace n ON n.oid = t.relnamespace "
                                     "WHERE n.nspname = :schema AND NOT i.indisvalid"), {"schema": SCHEMA}).scalar()
            assert invalid == 0
    finally:
        ended.cancel()
        holder.close()
        with engine.begin() as c:
            c.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        engine.dispose()
