"""Alembic environment; HEATSHIELD_DATABASE_URL selects the target database."""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import make_url

from heatshield import identity, workflow_models  # noqa: F401
from heatshield.database import Base, Database

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata
database_url = os.getenv("HEATSHIELD_DATABASE_URL", config.get_main_option("sqlalchemy.url"))
url = make_url(database_url)
if url.drivername == "postgresql":
    url = url.set(drivername="postgresql+psycopg")


def run_migrations_offline():
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    database = Database(url.render_as_string(hide_password=False))
    try:
        with database.engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                render_as_batch=connection.dialect.name == "sqlite",
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        database.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
