from alembic import context
from sqlmodel import SQLModel

from ytstudio.adapters.persistence import tables  # noqa: F401  (registers every table)
from ytstudio.adapters.persistence.db import engine_for
from ytstudio.config import get_settings

url = context.config.get_main_option("sqlalchemy.url") or get_settings().database_url
connectable = engine_for(url)

with connectable.connect() as connection:
    context.configure(
        connection=connection,
        target_metadata=SQLModel.metadata,
        render_as_batch=url.startswith("sqlite"),  # SQLite can't ALTER most things in place
    )
    with context.begin_transaction():
        context.run_migrations()
    connection.commit()
