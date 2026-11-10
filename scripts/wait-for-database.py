"""Wait for the configured database and the current migration heads, without secrets in logs."""

import os
import time
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url


def main():
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    heads = set(ScriptDirectory.from_config(config).get_heads())
    url = os.environ.get("HEATSHIELD_DATABASE_URL", "sqlite:////data/heatshield.db")
    parsed = make_url(url)
    if parsed.drivername == "postgresql":
        parsed = parsed.set(drivername="postgresql+psycopg")
    connect_args = {"connect_timeout": 3} if parsed.get_backend_name() == "postgresql" else {}
    engine = create_engine(parsed, pool_pre_ping=True, connect_args=connect_args)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                if inspect(connection).has_table("alembic_version"):
                    versions = set(
                        connection.execute(
                            text("SELECT version_num FROM alembic_version")
                        ).scalars()
                    )
                    if versions == heads:
                        print("Database and schema are ready.", flush=True)
                        engine.dispose()
                        return
        except Exception:
            # Driver exceptions often contain connection URLs; never print them.
            pass
        time.sleep(1)
    engine.dispose()
    raise SystemExit("Database/schema not ready within 60 seconds; check API migration logs.")


if __name__ == "__main__":
    main()
