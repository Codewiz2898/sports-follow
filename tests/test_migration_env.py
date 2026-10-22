"""How migrations run (migrations/env.py), against a throwaway database at alembic head: each is its own transaction,
so one that fails leaves those before it in. Two made-up migrations run with the repository's env.py, in a schema of
the test's own: the database's own tables and alembic_version aren't touched. Skipped without one; see
tests/test_players.py for setting it up."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

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
