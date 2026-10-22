from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from sports_follow.config import DATABASE_URL
from sports_follow.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", DATABASE_URL)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=DATABASE_URL, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


# Indexes created by hand in migrations (trigram and JSONB GIN indexes) aren't in the models;
# autogenerate must not offer to drop them.
MANUAL_INDEXES = {"ix_player_alias_trgm", "ix_athlete_search_trgm", "ix_athlete_ids", "ix_team_search_trgm"}


def include_object(obj, name, type_, reflected, compare_to):
    return not (type_ == "index" and name in MANUAL_INDEXES)


# How long a migration waits for a lock before it gives up. The migrations run while the old code serves: an ALTER waiting
# behind an open transaction (a worker job's) makes every later query on its table wait behind it, and a CONCURRENTLY
# build waits for every transaction older than it, on any table. Given up, the deploy stops with nothing replaced, and
# the next one goes on from there (a build that stopped part way is dropped and made again).
LOCK_TIMEOUT = os.environ.get("SPORTS_FOLLOW_MIGRATION_LOCK_TIMEOUT", "5s")


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        # For the session, so every migration's transaction has it, and an autocommit block's statements.
        connection.execute(text("SELECT set_config('lock_timeout', :wait, false)"), {"wait": LOCK_TIMEOUT})
        connection.commit()
        # Each migration in a transaction of its own. The migrations run while the old code serves (deploy/deploy.sh):
        # an ALTER's lock on its table is held until its own migration commits, not until the last of the deploy's
        # does; and one that fails leaves those before it in, for the next deploy to go on from.
        context.configure(connection=connection, target_metadata=target_metadata, include_object=include_object, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
