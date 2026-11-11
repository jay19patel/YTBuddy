"""Performance maths over real numbers from the APIs. No predictions."""

import itertools
import statistics
from datetime import datetime

from ytstudio.domain.models import Video


def median_views(videos: list[Video]) -> float:
    return statistics.median(v.views for v in videos) if videos else 0.0


def age_days(published_at: datetime, now: datetime) -> float:
    return max((now - published_at).total_seconds() / 86400, 1 / 24)


def velocity(views: int, published_at: datetime, now: datetime) -> float:
    """Views per day since publish."""
    return views / age_days(published_at, now)


def relative_performance(videos: list[Video]) -> list[tuple[Video, float]]:
    """Each video's views as a multiple of the channel's own median, best first."""
    med = median_views(videos)
    if not med:
        return [(v, 0.0) for v in videos]
    return sorted(((v, round(v.views / med, 2)) for v in videos), key=lambda p: -p[1])


def top_and_bottom(videos: list[Video], n: int = 5) -> tuple[list[tuple[Video, float]], list[tuple[Video, float]]]:
    ranked = relative_performance(videos)
    k = min(n, len(ranked) // 2)
    return ranked[:k], ranked[len(ranked) - k :][::-1] if k else []


def retention_cliffs(points: list[tuple[float, float]], top: int = 3) -> list[dict[str, float]]:
    """Steepest single drops in a (ratio, watch_ratio) curve."""
    drops = [
        {"at_ratio": a[0], "drop": round(a[1] - b[1], 4)} for a, b in itertools.pairwise(points) if a[1] - b[1] > 0
    ]
    return sorted(drops, key=lambda d: -d["drop"])[:top]
