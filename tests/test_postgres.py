"""Postgres-only behaviour: SKIP LOCKED schedule claiming and the full migration chain.
Runs when TEST_POSTGRES_URL points at an empty, disposable database (CI sets it)."""

import os
from datetime import time, timedelta

import pytest
from sqlalchemy import text

from tests.fakes import NOW
from ytstudio.adapters.persistence.db import engine_for
from ytstudio.adapters.persistence.repos import SqlUnitOfWork
from ytstudio.cli import migrate
from ytstudio.domain.models import Schedule
from ytstudio.ports import ChannelInfo

URL = os.environ.get("TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_POSTGRES_URL not set")


@pytest.fixture(scope="module")
def pg() -> str:
    assert URL
    with engine_for(URL).begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    migrate(URL)
    return URL


def test_two_dispatchers_never_claim_the_same_schedule(pg):
    with SqlUnitOfWork(pg) as uow:
        user = uow.users.add("pg@example.com")
        for i in range(4):
            uow.channels.upsert(ChannelInfo(id=f"UC{i}", title=str(i), thumb=""), user.id)
            uow.schedules.save(
                Schedule(
                    channel_id=f"UC{i}", enabled=True, run_time=time(7), next_run_utc=NOW - timedelta(minutes=i + 1)
                )
            )
        uow.commit()

    a, b = SqlUnitOfWork(pg).__enter__(), SqlUnitOfWork(pg).__enter__()
    try:
        first = a.schedules.claim_due(NOW, limit=2)  # locks two rows, transaction still open
        second = b.schedules.claim_due(NOW, limit=10)  # must skip the locked ones, not wait or duplicate
        assert len(first) == 2 and len(second) == 2
        assert not {s.channel_id for s in first} & {s.channel_id for s in second}
    finally:
        a.__exit__(None, None, None)
        b.__exit__(None, None, None)


def test_idem_key_is_unique_in_postgres(pg):
    with SqlUnitOfWork(pg) as uow:
        user = uow.users.by_email("pg@example.com")
        assert user
        r1, created1 = uow.runs.create(user.id, "UC0", "daily", "UC0:daily:2026-11-11", {})
        r2, created2 = uow.runs.create(user.id, "UC0", "daily", "UC0:daily:2026-11-11", {})
        uow.commit()
    assert created1 and not created2 and r1.id == r2.id
