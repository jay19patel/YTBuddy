"""Characterization tests: what the pre-refactor app (commit 9f05e75) does today.

Written before any refactor. Google is mocked; nothing leaves the machine.
"""

import json
import os
import tempfile
from unittest import mock
from urllib.parse import parse_qs, urlparse

import pytest

os.environ["YTSTUDIO_DB"] = os.path.join(tempfile.mkdtemp(), "char.db")

from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import select  # noqa: E402

import db  # noqa: E402
import jobs  # noqa: E402
import main  # noqa: E402

FAKE_SECRET = {
    "web": {
        "client_id": "cid.apps.googleusercontent.com",
        "client_secret": "csecret",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost:8000/oauth2callback"],
    }
}


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    secret = tmp_path / "cred.json"
    secret.write_text(json.dumps(FAKE_SECRET))
    monkeypatch.setattr(main, "CLIENT_SECRET", secret)
    db.SQLModel.metadata.drop_all(db.engine)
    db.init_db()
    main.pending_flows.clear()
    yield


client = TestClient(main.app)


def creds_json() -> str:
    return json.dumps({"token": "at", "refresh_token": "rt", "client_id": "cid", "client_secret": "cs",
                       "token_uri": "https://oauth2.googleapis.com/token", "scopes": main.SCOPES,
                       "expiry": "2999-01-01T00:00:00Z"})


def add_channel(cid="UC1", title="One"):
    with db.get_session() as s:
        s.add(db.Channel(id=cid, title=title, thumb="t", creds=creds_json()))
        s.commit()


def test_pages_render():
    for path in ("/", "/analytics", "/settings"):
        assert client.get(path).status_code == 200


def test_auth_redirect_has_state_pkce_offline_consent():
    r = client.get("/auth", follow_redirects=False)
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert r.status_code == 307
    assert q["access_type"] == ["offline"]
    assert q["prompt"] == ["select_account consent"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["state"][0] in main.pending_flows


def test_callback_unknown_state_shows_error():
    r = client.get("/oauth2callback?state=nope&code=x")
    assert "Login session expired" in r.text


def test_callback_success_stores_channel():
    flow = mock.MagicMock()
    flow.credentials.to_json.return_value = creds_json()
    main.pending_flows["s1"] = flow
    yt = mock.MagicMock()
    yt.channels().list().execute.return_value = {
        "items": [{"id": "UCX", "snippet": {"title": "X", "thumbnails": {"default": {"url": "u"}}}}]
    }
    with mock.patch.object(main, "build", return_value=yt), mock.patch.object(jobs, "run_now"):
        r = client.get("/oauth2callback?state=s1&code=c", follow_redirects=False)
    assert r.status_code == 307 and r.cookies.get("channel") == "UCX"
    with db.get_session() as s:
        assert s.get(db.Channel, "UCX").title == "X"


def test_sync_stats_updates_channels_and_snapshot():
    add_channel()
    found = {"UC1": {"snippet": {"title": "New", "thumbnails": {"default": {"url": "u2"}}},
                     "statistics": {"subscriberCount": "5", "viewCount": "50", "videoCount": "2"}}}
    with mock.patch.object(jobs, "fetch_stats", return_value=found):
        assert jobs.sync_stats() == "Updated 1 channel(s)."
    with db.get_session() as s:
        c = s.get(db.Channel, "UC1")
        assert (c.title, c.subscribers, c.views, c.videos) == ("New", 5, 50, 2)
        assert len(list(s.exec(select(db.Snapshot)))) == 1


def test_sync_analytics_error_recorded_for_all_periods():
    add_channel()
    with mock.patch.object(jobs, "fetch_analytics", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            jobs.sync_analytics()
    with db.get_session() as s:
        rows = list(s.exec(select(db.AnalyticsCache)))
    assert sorted(r.days for r in rows) == [7, 28, 90, 365]
    assert all(json.loads(r.data) == {"error": "boom"} for r in rows)


def test_settings_are_clamped():
    with mock.patch.object(jobs, "apply_settings"):
        r = client.post("/settings", data={"stats_minutes": 0, "analytics_minutes": 99999, "currency": "EUR"},
                        follow_redirects=False)
    assert r.status_code == 303
    s = db.get_settings()
    assert (s.stats_minutes, s.analytics_minutes, s.currency) == (1, 1440, "USD")


def test_remove_deletes_channel_and_its_rows():
    add_channel()
    client.get("/remove/UC1", follow_redirects=False)
    with db.get_session() as s:
        assert s.get(db.Channel, "UC1") is None
