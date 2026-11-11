import os
from datetime import date, datetime
from pathlib import Path

from sqlmodel import Field, Session, SQLModel, create_engine

DB_PATH = os.getenv("YTSTUDIO_DB", Path(__file__).parent / "ytstudio.db")
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})


class Channel(SQLModel, table=True):
    id: str = Field(primary_key=True)
    title: str
    thumb: str
    creds: str  # OAuth token JSON; this makes ytstudio.db a secret, never commit it
    subscribers: int = 0
    views: int = 0
    videos: int = 0
    stats_at: datetime | None = None


class Snapshot(SQLModel, table=True):
    """One row per channel per day, so the sidebar can show what changed since the last visit."""

    channel_id: str = Field(primary_key=True, index=True)
    day: date = Field(primary_key=True)
    subscribers: int
    views: int
    videos: int


class AnalyticsCache(SQLModel, table=True):
    """Analytics fetched by the background job, so pages never wait on YouTube."""

    channel_id: str = Field(primary_key=True)
    days: int = Field(primary_key=True)
    data: str  # JSON: totals, daily, top, revenue... or {"error": ...} / {"needs_reconnect": true}
    fetched_at: datetime


class Settings(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)  # single row
    stats_minutes: int = 5
    analytics_minutes: int = 60  # YouTube only updates analytics about once a day
    stats_enabled: bool = True
    analytics_enabled: bool = True
    currency: str = "USD"


def init_db():
    SQLModel.metadata.create_all(engine)
    with engine.begin() as conn:  # run history used to be stored; it isn't any more
        conn.exec_driver_sql("DROP TABLE IF EXISTS jobrun")


def get_session():
    return Session(engine)


def get_settings() -> Settings:
    with get_session() as db:
        if not (s := db.get(Settings, 1)):
            s = Settings()
            db.add(s)
            db.commit()
            db.refresh(s)
        return s
