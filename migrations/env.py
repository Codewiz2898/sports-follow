from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

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
MANUAL_INDEXES = {"ix_player_alias_trgm", "ix_athlete_search_trgm", "ix_athlete_ids"}


def include_object(obj, name, type_, reflected, compare_to):
    return not (type_ == "index" and name in MANUAL_INDEXES)


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, include_object=include_object)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
