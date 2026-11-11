"""Trend providers (Strategy). Each returns candidate videos; ranking happens in the application layer.

Add a provider: write a class with `name` and `fetch(q) -> list[Video]`, register it in TREND_PROVIDERS,
then list its name under [trends] providers in config/pipelines.toml.
"""

import redis

from ytstudio.adapters.youtube import YouTubeGateway
from ytstudio.domain.models import Video
from ytstudio.domain.schedule import quota_day
from ytstudio.ports import Clock, TrendQuery

SEARCH_DAYS = 14


class MostPopularProvider:
    """YouTube's most-popular chart for the channel's region (1 unit, cached 6 h)."""

    name = "most_popular"

    def __init__(self, yt: YouTubeGateway):
        self.yt = yt

    def fetch(self, q: TrendQuery) -> list[Video]:
        return self.yt.most_popular(q.region, q.category_id, using=q.channel_id)


class _Search:
    """Topic search over the last 14 days by view count. 100 units each, so capped per channel per day."""

    name = "search"

    def __init__(self, yt: YouTubeGateway, r: redis.Redis, clock: Clock, per_day: int):
        self.yt, self.r, self.clock, self.per_day = yt, r, clock, per_day

    def queries(self, q: TrendQuery) -> list[str]:
        raise NotImplementedError

    def fetch(self, q: TrendQuery) -> list[Video]:
        out: list[Video] = []
        key = f"yt:quota:searches:{q.channel_id}:{quota_day(self.clock.now())}"
        for query in self.queries(q):
            if int(self.r.incr(key)) > self.per_day:  # cached searches are free, so count only real ones
                self.r.decr(key)
                break
            self.r.expire(key, 2 * 86400)
            out += self.yt.search(query, q.region, q.language, SEARCH_DAYS, using=q.channel_id)
        return out


class NicheSearchProvider(_Search):
    name = "niche_search"

    def queries(self, q: TrendQuery) -> list[str]:
        return [q.niche] if q.niche else []


class CustomTopicsProvider(_Search):
    name = "custom_topics"

    def queries(self, q: TrendQuery) -> list[str]:
        return [t for t in q.topics if t.strip()]
