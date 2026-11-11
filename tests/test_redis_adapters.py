"""Contract tests against a real Redis (logical DB 15). Skipped when Redis is not running."""

import pytest
import redis

from tests.fakes import NOW, FakeClock
from ytstudio.adapters.redis_kv import Heartbeat, RedisCache, RedisLock, RedisQuotaBudget, RedisRateLimiter, client
from ytstudio.domain.errors import QuotaBudgetBlocked, QuotaExceeded


@pytest.fixture
def r():
    c = client("redis://localhost:6379/15")
    try:
        c.ping()
    except redis.ConnectionError:
        pytest.skip("Redis not running")
    c.flushdb()
    yield c
    c.flushdb()


def test_cache_roundtrip(r):
    cache = RedisCache(r)
    assert cache.get("k") is None
    cache.set("k", {"a": [1, 2]}, 60)
    assert cache.get("k") == {"a": [1, 2]} and r.ttl("yt:cache:k") > 0


def test_lock_owner_token_and_safe_release(r):
    lock = RedisLock(r)
    assert lock.acquire("UC:daily", 60, "run-1")
    assert not lock.acquire("UC:daily", 60, "run-2")  # contention
    assert not lock.release("UC:daily", "run-2")  # someone else can't free it
    assert lock.release("UC:daily", "run-1")
    assert lock.acquire("UC:daily", 60, "run-2")


def test_token_bucket(r):
    rl = RedisRateLimiter(r)
    assert all(rl.acquire("b", rate_per_s=0.01, capacity=3, timeout_s=0) for _ in range(3))
    assert not rl.acquire("b", rate_per_s=0.01, capacity=3, timeout_s=0)


def test_quota_budget_blocks_noncritical_first(r):
    q = RedisQuotaBudget(r, FakeClock(), limit=1000, noncritical_pct=80)
    q.spend(700, critical=False)
    with pytest.raises(QuotaBudgetBlocked) as e:
        q.spend(101, critical=False)  # would pass 800
    assert e.value.reset_at > NOW
    q.spend(300, critical=True)
    assert q.used_today() == 1000
    with pytest.raises(QuotaExceeded):
        q.spend(1, critical=True)
    assert q.used_today() == 1000  # refused calls are not counted


def test_quota_exhausted_blocks_everything(r):
    q = RedisQuotaBudget(r, FakeClock(), limit=1000, noncritical_pct=80)
    q.exhausted()
    with pytest.raises(QuotaExceeded):
        q.spend(1, critical=True)
    assert r.ttl(next(iter(r.scan_iter("yt:quota:*")))) > 0


def test_heartbeat(r):
    hb = Heartbeat(r)
    assert hb.last("tick") is None
    hb.beat("tick", NOW)
    assert hb.last("tick") == NOW
