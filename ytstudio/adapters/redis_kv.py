"""Redis-backed cache, lock, rate limiter and quota budget.

Key prefixes (all in the app Redis DB, REDIS_URL):
  yt:cache:   API response cache          yt:llm:     LLM response cache
  yt:lock:    pipeline locks              yt:rl:      token buckets
  yt:quota:   YouTube units per Pacific day   yt:beat:    beat heartbeat
"""

import json
import time
from datetime import datetime
from typing import Any

import redis

from ytstudio.domain.errors import QuotaBudgetBlocked, QuotaExceeded
from ytstudio.domain.schedule import next_quota_reset, quota_day
from ytstudio.ports import Clock

RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end
"""

# Token bucket: KEYS[1]=bucket; ARGV = rate/s, capacity, now (s). Returns 1 if a token was taken.
BUCKET = """
local s = redis.call('hmget', KEYS[1], 't', 'ts')
local rate, cap, now = tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3])
local tokens = tonumber(s[1]) or cap
local ts = tonumber(s[2]) or now
tokens = math.min(cap, tokens + (now - ts) * rate)
local ok = 0
if tokens >= 1 then tokens = tokens - 1; ok = 1 end
redis.call('hset', KEYS[1], 't', tokens, 'ts', now)
redis.call('expire', KEYS[1], math.ceil(cap / rate) + 60)
return ok
"""


def client(url: str) -> redis.Redis:
    return redis.Redis.from_url(url, decode_responses=True)


class RedisCache:
    def __init__(self, r: redis.Redis, prefix: str = "yt:cache:"):
        self.r, self.prefix = r, prefix

    def get(self, key: str) -> Any | None:
        raw = self.r.get(self.prefix + key)
        return json.loads(raw) if raw is not None else None

    def set(self, key: str, value: Any, ttl_s: int) -> None:
        self.r.set(self.prefix + key, json.dumps(value, default=str), ex=ttl_s)


class RedisLock:
    def __init__(self, r: redis.Redis):
        self.r = r
        self._release = r.register_script(RELEASE)

    def acquire(self, key: str, ttl_s: int, owner: str) -> bool:
        return bool(self.r.set(f"yt:lock:{key}", owner, nx=True, ex=ttl_s))

    def release(self, key: str, owner: str) -> bool:
        # Compare-and-delete: never free a lock someone else took after ours expired.
        return bool(self._release(keys=[f"yt:lock:{key}"], args=[owner]))

    def holder(self, key: str) -> str | None:
        v = self.r.get(f"yt:lock:{key}")
        return v.decode() if isinstance(v, bytes) else v


class RedisRateLimiter:
    def __init__(self, r: redis.Redis):
        self._take = r.register_script(BUCKET)

    def acquire(self, bucket: str, rate_per_s: float, capacity: int, timeout_s: float = 30) -> bool:
        deadline = time.monotonic() + timeout_s
        while True:
            if self._take(keys=[f"yt:rl:{bucket}"], args=[rate_per_s, capacity, time.time()]):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(1 / rate_per_s, 1.0))


class RedisQuotaBudget:
    """Counts YouTube Data API units per Pacific-time day, before each call is made."""

    def __init__(self, r: redis.Redis, clock: Clock, limit: int, noncritical_pct: int):
        self.r, self.clock, self.limit, self.pct = r, clock, limit, noncritical_pct

    def _key(self) -> str:
        return f"yt:quota:{quota_day(self.clock.now())}"

    def used_today(self) -> int:
        return int(self.r.get(self._key()) or 0)

    def spend(self, units: int, critical: bool) -> None:
        now: datetime = self.clock.now()
        cap = self.limit if critical else self.limit * self.pct // 100
        key = self._key()
        used = self.r.incrby(key, units)
        self.r.expire(key, 2 * 86400)
        if used > cap:
            self.r.decrby(key, units)  # the call is not made, so the units are not spent
            err = QuotaExceeded if critical else QuotaBudgetBlocked
            raise err(next_quota_reset(now), f"YouTube quota budget: {used - units}/{cap} units used today")

    def exhausted(self) -> None:
        """Google said quotaExceeded: block everything until the reset."""
        self.r.set(self._key(), self.limit, ex=2 * 86400)


class Heartbeat:
    def __init__(self, r: redis.Redis):
        self.r = r

    def beat(self, name: str, at: datetime) -> None:
        self.r.set(f"yt:beat:{name}", at.isoformat(), ex=7 * 86400)

    def last(self, name: str) -> datetime | None:
        raw = self.r.get(f"yt:beat:{name}")
        return datetime.fromisoformat(str(raw)) if raw else None
