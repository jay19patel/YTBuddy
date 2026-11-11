"""Characterization tests: behaviours of the original app (commit 9f05e75), checked against the new code.

Intentional changes, each from docs/REVIEW.md:
- every page needs a login now (S4); OAuth state is server-side and bound to the browser (S8)
- "remove channel" is a CSRF-protected POST that also revokes the Google token (S6)
- tokens are stored encrypted (S1)
"""

import json
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from sqlmodel import select

from ytstudio.adapters.persistence import tables as t
from ytstudio.adapters.persistence.db import session_for
from ytstudio.adapters.persistence.web_sessions import WebSessions
from ytstudio.adapters.youtube import GoogleOAuth
from ytstudio.domain.models import SyncSettings
from ytstudio.interfaces.web.app import create_app


def logged_in(db_url: str, user_id: int) -> tuple[TestClient, str]:
    client = TestClient(create_app())
    ws = WebSessions(db_url)
    cookie = ws.create(user_id)
    client.cookies.set("ytsid", cookie)
    info = ws.get(cookie)
    assert info
    return client, info.csrf


def test_pages_render(container, user_and_channel, db_url):
    client, _ = logged_in(db_url, user_and_channel[0])
    for path in (
        "/today",
        "/insights",
        "/settings",
        "/ideas",
        "/shorts",
        "/trends",
        "/jobs",
        "/publish",
        "/notifications",
    ):
        r = client.get(path)
        assert r.status_code == 200, path


def test_pages_need_login(container):
    r = TestClient(create_app()).get("/today", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_connect_redirect_has_state_pkce_offline_consent(container, user_and_channel, db_url):
    container.o["oauth"] = GoogleOAuth("cid", "secret", "http://testserver/oauth2callback")
    container.__dict__.pop("oauth", None)
    client, _ = logged_in(db_url, user_and_channel[0])
    r = client.get("/connect", follow_redirects=False)
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["access_type"] == ["offline"]
    assert q["prompt"] == ["select_account consent"]
    assert q["code_challenge_method"] == ["S256"]
    with session_for(db_url) as s:
        row = s.get(t.OAuthState, q["state"][0])
    assert row and row.purpose == "connect" and row.code_verifier
    assert "ytnonce" in r.cookies


def test_callback_unknown_state_shows_error(container):
    r = TestClient(create_app()).get("/oauth2callback?state=nope&code=x")
    assert r.status_code == 400 and "expired" in r.text


def test_callback_success_stores_channel(container, user_and_channel, db_url, monkeypatch):
    from ytstudio.interfaces import worker

    monkeypatch.setattr(worker.detect_profile, "apply_async", lambda **kw: None)
    client, _ = logged_in(db_url, user_and_channel[0])
    r = client.get("/connect", follow_redirects=False)
    assert "st1" in r.headers["location"]
    r = client.get("/oauth2callback?state=st1&code=c", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/settings")
    assert r.cookies.get("channel") == "UCme"
    assert container.vault.load("UCme").refresh_token == "rt"


def test_sync_stats_updates_channels_and_snapshot(container, user_and_channel, yt, db_url):
    yt.infos["UCme"].subscribers, yt.infos["UCme"].title = 5, "New"
    assert container.sync.sync_stats() == "Updated 1 channel(s)."
    with session_for(db_url) as s:
        c = s.get(t.Channel, "UCme")
        assert (c.title, c.subscribers) == ("New", 5)
        assert len(list(s.exec(select(t.Snapshot)))) == 1


def test_sync_analytics_error_recorded_for_all_periods(container, user_and_channel, yt, db_url, monkeypatch):
    from ytstudio.domain.errors import PermanentError

    def boom(*a, **k):
        raise PermanentError("boom")

    monkeypatch.setattr(yt, "channel_report", boom)
    try:
        container.sync.sync_analytics()
        raise AssertionError("should raise")
    except PermanentError:
        pass
    with session_for(db_url) as s:
        rows = list(s.exec(select(t.AnalyticsCache)))
    assert sorted(r.days for r in rows) == [7, 28, 90, 365]
    assert all(json.loads(r.data) == {"error": "boom"} for r in rows)


def test_settings_are_clamped(container, user_and_channel):
    container.sync.save_settings(SyncSettings(0, 99999, True, True, "EUR"), ("USD", "INR"))
    s = container.sync.settings()
    assert (s.stats_minutes, s.analytics_minutes, s.currency) == (1, 1440, "USD")


def test_remove_deletes_channel_and_revokes(container, user_and_channel, db_url):
    client, token = logged_in(db_url, user_and_channel[0])
    r = client.post("/channels/UCme/disconnect", data={"csrf": token}, follow_redirects=False)
    assert r.status_code == 303
    with session_for(db_url) as s:
        assert s.get(t.Channel, "UCme") is None
    assert container.oauth.revoked == ["rt"]
