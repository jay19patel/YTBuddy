"""YouTube gateway against recorded Google API responses (googleapiclient's HttpMockSequence). No network."""

import json
from datetime import timedelta
from unittest import mock

import pytest
from google.auth.exceptions import RefreshError
from googleapiclient.discovery import build
from googleapiclient.http import HttpMockSequence

from tests.fakes import NOW, CountingQuota, DictCache, FakeClock, MemVault
from ytstudio.adapters.youtube import YouTubeGateway, friendly_error, parse_duration, parse_srt
from ytstudio.domain.errors import PermanentError, QuotaExceeded, ReconnectNeeded, TransientError
from ytstudio.domain.models import OAuthToken

CHANNEL = {
    "items": [
        {
            "id": "UCme",
            "snippet": {"title": "Me", "thumbnails": {"default": {"url": "u"}}},
            "statistics": {"subscriberCount": "10", "viewCount": "100", "videoCount": "3"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UUme"}},
        }
    ]
}
ITEMS = {
    "items": [
        {"contentDetails": {"videoId": "v2", "videoPublishedAt": (NOW - timedelta(days=1)).isoformat()}},
        {"contentDetails": {"videoId": "v1", "videoPublishedAt": (NOW - timedelta(days=30)).isoformat()}},
    ],
    "nextPageToken": "never-followed",
}
VIDEOS = {
    "items": [
        {
            "id": "v2",
            "snippet": {"channelId": "UCme", "title": "New one", "publishedAt": "2026-11-10T10:00:00Z"},
            "statistics": {"viewCount": "55"},
            "contentDetails": {"duration": "PT1M5S"},
        }
    ]
}
QUOTA_ERR = json.dumps({"error": {"code": 403, "errors": [{"reason": "quotaExceeded"}], "message": "quotaExceeded"}})


def gateway(responses, reconnects=None):
    vault = MemVault()
    vault.save(
        "UCme",
        OAuthToken(
            refresh_token="rt",
            access_token="at",
            expiry=NOW + timedelta(days=3650),
            scopes=["https://www.googleapis.com/auth/yt-analytics.readonly"],
        ),
    )
    quota = CountingQuota()
    g = YouTubeGateway(vault, "cid", "cs", quota, DictCache(), FakeClock(), on_reconnect=(reconnects or []).append)
    http = HttpMockSequence(responses)
    g.service = lambda name, version, channel_id: build(name, version, http=http, cache_discovery=False)  # type: ignore[method-assign]
    return g, quota


def ok(body):
    return ({"status": "200"}, json.dumps(body))


def test_incremental_uploads_stop_at_since_and_count_quota():
    g, quota = gateway([ok(CHANNEL), ok(ITEMS), ok(VIDEOS)])
    vids = g.uploads("UCme", since=NOW - timedelta(days=7))
    assert [v.id for v in vids] == ["v2"] and vids[0].duration_s == 65 and vids[0].views == 55
    assert quota.used == 3  # channels + playlistItems + videos, 1 unit each


def test_quota_exceeded_is_translated_and_blocks_budget():
    g, quota = gateway([({"status": "403"}, QUOTA_ERR)])
    with pytest.raises(QuotaExceeded) as e:
        g.channel_infos(["UCme"], using="UCme")
    assert e.value.reset_at > NOW and quota.used == quota.limit


def test_server_errors_are_transient_and_4xx_permanent():
    g, _ = gateway([({"status": "503"}, "{}")])
    with pytest.raises(TransientError):
        g.channel_infos(["UCme"], using="UCme")
    g, _ = gateway([({"status": "404"}, json.dumps({"error": {"message": "nope"}}))])
    with pytest.raises(PermanentError):
        g.channel_infos(["UCme"], using="UCme")


def test_search_is_cached():
    search = {"items": [{"id": {"videoId": "s1"}}]}
    g, quota = gateway([ok(search), ok(VIDEOS), ok(VIDEOS)])
    g.search("rent", "IN", "hi", 14, using="UCme")
    g.search("rent", "IN", "hi", 14, using="UCme")
    assert quota.used == 100 + 1 + 1  # second search came from the cache


def test_retention_report_parsing():
    report = {
        "columnHeaders": [
            {"name": "elapsedVideoTimeRatio"},
            {"name": "audienceWatchRatio"},
            {"name": "relativeRetentionPerformance"},
        ],
        "rows": [[0.01, 1.0, 0.5], [0.5, 0.4, 0.6]],
    }
    g, quota = gateway([ok(report)])
    points = g.retention("UCme", "v1")
    assert points[1].watch_ratio == 0.4 and points[1].relative == 0.6 and quota.used == 0  # Analytics: own quota


def test_revoked_refresh_token_marks_reconnect():
    vault = MemVault()
    vault.save("UCme", OAuthToken(refresh_token="rt", access_token="at", expiry=NOW - timedelta(days=1)))
    seen: list[str] = []
    g = YouTubeGateway(vault, "cid", "cs", CountingQuota(), DictCache(), FakeClock(), on_reconnect=seen.append)
    refresh = mock.patch("google.oauth2.credentials.Credentials.refresh", side_effect=RefreshError("invalid_grant"))
    with refresh, pytest.raises(ReconnectNeeded):
        g.creds("UCme")
    assert seen == ["UCme"]


def test_refresh_saves_new_access_token():
    vault = MemVault()
    vault.save("UCme", OAuthToken(refresh_token="rt", access_token="old", expiry=NOW - timedelta(days=1), scopes=["s"]))
    g = YouTubeGateway(vault, "cid", "cs", CountingQuota(), DictCache(), FakeClock(), on_reconnect=lambda c: None)

    def refresh(self, request):
        self.token = "new"

    with mock.patch("google.oauth2.credentials.Credentials.refresh", refresh):
        g.creds("UCme")
    assert vault.load("UCme").access_token == "new" and vault.load("UCme").refresh_token == "rt"


def test_parsers_and_errors():
    assert parse_duration("PT1H2M3S") == 3723 and parse_duration("P1DT1S") == 86401 and parse_duration("") == 0
    cues = parse_srt("1\n00:00:01,000 --> 00:00:02,500\nHello\nthere\n\n2\n00:00:03,000 --> 00:00:04,000\nBye\n")
    assert [(c.start, c.end, c.text) for c in cues] == [(1.0, 2.5, "Hello there"), (3.0, 4.0, "Bye")]
    assert "quota" in friendly_error(QuotaExceeded(NOW)).lower()
    assert "reconnect" in friendly_error(ReconnectNeeded("UC")).lower()


def test_undecryptable_token_marks_reconnect():
    seen: list[str] = []
    g = YouTubeGateway(MemVault(), "cid", "cs", CountingQuota(), DictCache(), FakeClock(), on_reconnect=seen.append)
    with pytest.raises(ReconnectNeeded):
        g.creds("UCmissing")
    assert seen == ["UCmissing"]
