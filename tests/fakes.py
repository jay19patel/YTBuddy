"""In-memory fakes for every external port. Used by unit, use-case and end-to-end tests."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from ytstudio.domain.errors import QuotaBudgetBlocked
from ytstudio.domain.events import Event
from ytstudio.domain.models import Cue, OAuthToken, RetentionPoint, User, Video
from ytstudio.domain.schedule import next_quota_reset
from ytstudio.ports import ChannelInfo, OAuthResult, OAuthStart, TrendQuery

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, now: datetime = NOW):
        self.t = now

    def now(self) -> datetime:
        return self.t


class DictCache:
    def __init__(self) -> None:
        self.d: dict[str, Any] = {}

    def get(self, key: str) -> Any | None:
        return self.d.get(key)

    def set(self, key: str, value: Any, ttl_s: int) -> None:
        self.d[key] = value


class DictLock:
    def __init__(self) -> None:
        self.held: dict[str, str] = {}

    def acquire(self, key: str, ttl_s: int, owner: str) -> bool:
        if key in self.held:
            return False
        self.held[key] = owner
        return True

    def release(self, key: str, owner: str) -> bool:
        if self.held.get(key) == owner:
            del self.held[key]
            return True
        return False


class NoLimit:
    def acquire(self, bucket: str, rate_per_s: float, capacity: int, timeout_s: float = 30) -> bool:
        return True


class CountingQuota:
    def __init__(self, limit: int = 10_000):
        self.used, self.limit = 0, limit

    def spend(self, units: int, critical: bool) -> None:
        if self.used + units > self.limit:
            raise QuotaBudgetBlocked(next_quota_reset(NOW))
        self.used += units

    def exhausted(self) -> None:
        self.used = self.limit

    def used_today(self) -> int:
        return self.used


def vid(i: str, channel: str, views: int, days_old: float = 10, title: str = "", duration: int = 600) -> Video:
    return Video(
        id=i,
        channel_id=channel,
        channel_title=f"Chan {channel}",
        title=title or f"Video {i}",
        published_at=NOW - timedelta(days=days_old),
        views=views,
        duration_s=duration,
    )


@dataclass
class FakeYouTube:
    """VideoSource + Analytics."""

    own: dict[str, list[Video]] = field(default_factory=dict)
    others_recent: dict[str, list[int]] = field(default_factory=dict)
    infos: dict[str, ChannelInfo] = field(default_factory=dict)
    broken: set[str] = field(default_factory=set)
    cues: list[Cue] | None = None
    calls: list[str] = field(default_factory=list)
    uploaded: list[dict[str, Any]] = field(default_factory=list)  # bodies of videos uploaded through the Publish page

    def upload(self, channel_id: str, path: str, body: dict[str, Any]) -> str:
        self.uploaded.append(body)
        return "vid123"

    def update_about(self, channel_id: str, description: str, keywords: str) -> None:
        self.calls.append(f"about:{channel_id}")
        info = self.infos[channel_id]
        info.description, info.keywords = description, keywords

    def my_channel(self, token: OAuthToken) -> ChannelInfo | None:
        return list(self.infos.values())[-1] if self.infos else None  # the account just signed in

    def channel_infos(self, channel_ids: list[str], using: str | None) -> dict[str, ChannelInfo]:
        from ytstudio.domain.errors import ReconnectNeeded

        self.calls.append(f"infos:{using}")
        if using in self.broken:
            raise ReconnectNeeded(using)
        return {c: self.infos[c] for c in channel_ids if c in self.infos}

    def uploads(self, channel_id: str, since: datetime | None, limit: int = 200) -> list[Video]:
        return [v for v in self.own.get(channel_id, []) if since is None or v.published_at >= since]

    def videos(self, video_ids: list[str], using: str | None, critical: bool = True) -> list[Video]:
        return []

    def recent_views(self, channel_ids: list[str], using: str | None) -> dict[str, list[int]]:
        return {c: self.others_recent.get(c, []) for c in channel_ids}

    def captions(self, channel_id: str, video_id: str) -> list[Cue] | None:
        return self.cues

    def channel_report(self, channel_id: str, days: int, currency: str) -> dict[str, Any]:
        return {
            "totals": {"views": days * 10},
            "daily": [],
            "top": [],
            "monetized": None,
            "revenue": {},
            "revenue_daily": [],
            "currency": currency,
        }

    def video_metrics(
        self, channel_id: str, video_ids: list[str], start: date, end: date
    ) -> dict[str, dict[str, float]]:
        return {v: {"averageViewDuration": 100.0, "averageViewPercentage": 40.0} for v in video_ids}

    def retention(self, channel_id: str, video_id: str) -> list[RetentionPoint]:
        return [
            RetentionPoint(ratio=i / 20, watch_ratio=1 - i / 30, relative=1.2 if 5 <= i <= 8 else 0.9)
            for i in range(21)
        ]


class FakeTrends:
    def __init__(self, name: str, videos: list[Video]):
        self.name, self.v = name, videos

    def fetch(self, q: TrendQuery) -> list[Video]:
        return self.v


class FakeOAuth:
    def __init__(self, email: str = "me@example.com"):
        self.email, self.revoked = email, []  # type: ignore[var-annotated]

    def start(self, scopes: list[str], login_hint: str | None = None) -> OAuthStart:
        return OAuthStart(url="https://accounts.google.com/o/oauth2/auth?state=st1", state="st1", code_verifier="cv")

    def finish(self, scopes: list[str], callback_url: str, state: str, code_verifier: str) -> OAuthResult:
        assert code_verifier == "cv"
        return OAuthResult(OAuthToken(refresh_token="rt", access_token="at", scopes=scopes), self.email)

    def revoke(self, token: OAuthToken) -> None:
        self.revoked.append(token.refresh_token)


class MemVault:
    def __init__(self) -> None:
        self.d: dict[str, OAuthToken] = {}

    def save(self, channel_id: str, token: OAuthToken) -> None:
        self.d[channel_id] = token

    def load(self, channel_id: str) -> OAuthToken:
        from ytstudio.domain.errors import ReconnectNeeded

        if channel_id not in self.d:
            raise ReconnectNeeded(channel_id)
        return self.d[channel_id]


class RecordingNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Event]] = []

    def send(self, user: User, event: Event) -> None:
        self.sent.append((user.email, event))


class FakeMedia:
    def __init__(self) -> None:
        self.renders: list[tuple[float, float]] = []
        self.videos: list[tuple[int, bool]] = []

    def duration(self, path: Path) -> float:
        return 600.0

    def render_short(self, src: Path, start: float, end: float, cues: list[Cue], out: Path) -> None:
        self.renders.append((start, end))
        out.write_bytes(b"mp4")

    def frame(self, src: Path, at: float, out: Path) -> None:
        out.write_bytes(b"jpg")

    def encode_audio(self, wav: Path, out: Path) -> None:
        out.write_bytes(b"mp3")

    def render_subtitle_video(self, units: list[dict[str, Any]], audio: Path, vertical: bool, out: Path) -> None:
        self.videos.append((len(units), vertical))
        out.write_bytes(b"mp4")


class FakeNarrator:
    def __init__(self) -> None:
        self.spoken: list[tuple[list[tuple[str, float]], str]] = []

    def speak(self, lines: list[tuple[str, float]], language: str, out: Path) -> list[tuple[float, float]]:
        self.spoken.append((lines, language))
        out.write_bytes(b"wav")
        return [(i * 2.0, i * 2.0 + 1.5) for i in range(len(lines))]


class FakeImages:
    def cover(self, frame: Path, text: str, out: Path) -> None:
        out.write_bytes(b"cover")


class FakeTranscriber:
    def transcribe(self, media_path: Path, language: str | None) -> list[Cue]:
        return transcript()


def transcript(n: int = 60) -> list[Cue]:
    """A 10-minute talk: one 10 s sentence per cue."""
    return [Cue(i * 10.0, i * 10.0 + 9.5, f"Point number {i} is about saving money on rent.") for i in range(n)]


def llm_responses() -> dict[str, Any]:
    idea = {
        "title": "I tracked rent for 90 days",
        "angle": "Real numbers from my flat",
        "why_now": "Rent videos spike",
        "outlier_refs": ["t1", "invented"],
        "difference": "My own receipts",
        "builds_on": ["own1", "nope"],
        "effort": "standard",
    }
    return {
        "analyze_performance": {
            "summary": "Long tutorials beat vlogs.",
            "working": ["Tutorials (own1)"],
            "not_working": ["Vlogs"],
            "retention_notes": ["Drop at 0:40"],
        },
        "ideas": {"ideas": [idea | {"title": f"Idea {i}"} for i in range(5)]},
        "hooks": {
            "hooks": [
                "You are wasting 30% of your rent every month, and here is why.",
                "Hello and welcome back",
                "What 3 landlords told me about rent",
                "Stop paying rent before you see this",
                "Rent is amazing",
            ]
        },
        "script": {
            "title_working": "Rent",
            "estimated_minutes": 8.0,
            "sections": [
                {"heading": "Hook", "spoken": "You are wasting rent.", "beat": "hook"},
                {"heading": "Turn", "spoken": "Here's what we'll do.", "beat": "turn"},
                {"heading": "Payoff", "spoken": "Do this.", "beat": "payoff", "broll": ["receipts"]},
            ],
        },
        "short_script": {
            "hook": "Stop overpaying rent, do this instead.",
            "sections": [
                {"heading": "Hook", "spoken": "Stop overpaying rent, do this instead.", "beat": "hook"},
                {"heading": "Point", "spoken": "Ask for the receipts.", "beat": "body", "broll": ["receipts"]},
                {"heading": "Payoff", "spoken": "That saved me twelve thousand.", "beat": "payoff"},
            ],
            "title": "Stop overpaying rent",
            "description": "One trick that cut my rent.\nTry it this month.",
            "estimated_seconds": 35,
        },
        "package": {
            "pairs": [
                {"title": "I tracked rent for 90 days", "thumbnail_text": "₹12,000 GONE"},
                {"title": "Rent is a scam?", "thumbnail_text": "RENT"},
                {"title": "3 rent mistakes", "thumbnail_text": "STOP"},
            ],
            "thumbnail_brief": "Me holding receipts",
        },
        "seo": {
            "description": "How I cut rent.",
            "tags": ["rent", "india"],
            "pinned_comment": "What's your rent?",
            "target_queries": ["reduce rent", "rent tips", "save on rent"],
        },
        "channel_setup": {
            "description": "Python and AI projects for beginners, built step by step in Hindi.",
            "keywords": ["Python", "#python", "ai projects", "AI Projects", "fastapi", "hindi coding"],
            "niche": "Python and AI project tutorials for Hindi-speaking college students",
            "language": "hinglish",
            "region": "in",
            "topics": ["python projects", "ai app tutorial"],
            "target_minutes": 12,
            "voice_md": "## Who I am talking to\nCollege students\n\n## Channel analysis\nShort tutorials do best.",
        },
        "detect_niche": {
            "niche": "personal finance for renters in India",
            "language": "hinglish",
            "region": "in",
            "topics": ["rent tips", "save money"],
        },
        "short_candidates": lambda req: {
            "picks": [
                {"window": 0, "start": 0, "end": 1, "first_line": "Rent trick one", "reason": "peak"},
                {"window": 1, "start": 0, "end": 1, "first_line": "Rent trick two", "reason": "peak"},
                {"window": 999, "start": 0, "end": 1, "first_line": "bogus", "reason": "out of range"},
            ]
        },
        "short_meta": {
            "shorts": [{"title": "Rent trick #1", "description": "d1"}, {"title": "Rent trick #2", "description": "d2"}]
        },
    }
