from functools import lru_cache

from sqlalchemy import Engine, event
from sqlmodel import Session, create_engine


@lru_cache
def engine_for(url: str) -> Engine:
    if url.startswith("sqlite"):
        eng = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(eng, "connect")
        def _wal(conn, _):
            conn.execute("PRAGMA journal_mode=WAL")  # readers don't block the background writer

        return eng
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


def session_for(url: str) -> Session:
    return Session(engine_for(url), expire_on_commit=False)
