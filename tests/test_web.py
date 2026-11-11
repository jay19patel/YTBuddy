"""Web layer: login, CSRF, owner scoping, resumable upload, security headers, every page renders with data."""

import pytest
from fastapi.testclient import TestClient

from tests.test_characterization import logged_in
from ytstudio.adapters.persistence.web_sessions import WebSessions
from ytstudio.interfaces.web.app import create_app


@pytest.fixture
def fresh_redis(container):
    container.redis.flushdb()
    yield
    container.redis.flushdb()


def test_pin_opens_the_site(container, db_url, fresh_redis):
    client = TestClient(create_app())
    assert "PIN" in client.get("/login").text
    r = client.post("/login", data={"pin": "242425"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/today"
    assert WebSessions(db_url).get(r.cookies["ytsid"])
    assert "httponly" in r.headers["set-cookie"].lower()
    assert client.get("/today").status_code == 200


def test_wrong_pin_is_rejected_and_rate_limited(container, db_url, fresh_redis):
    client = TestClient(create_app())
    for _ in range(5):
        assert client.post("/login", data={"pin": "000000"}).status_code == 401
    r = client.post("/login", data={"pin": "242425"})  # even the right PIN waits once locked
    assert r.status_code == 429 and "15 minutes" in r.text
    assert client.post("/login", data={"pin": "242425"}, headers={"Origin": "https://evil.example"}).status_code == 403


def test_google_login_is_gone(container):
    client = TestClient(create_app())
    assert client.get("/login/start").status_code in (404, 405)


def test_oauth_state_is_bound_to_the_browser(container, user_and_channel, db_url):
    starter, _ = logged_in(db_url, user_and_channel[0])
    starter.get("/connect", follow_redirects=False)
    other_browser = TestClient(create_app())  # no nonce cookie
    r = other_browser.get("/oauth2callback?state=st1&code=c")
    assert r.status_code == 400


def test_access_denied_explains_test_users(container):
    r = TestClient(create_app()).get("/oauth2callback?error=access_denied&state=x")
    assert r.status_code == 400 and "Test users" in r.text


def test_post_without_csrf_or_cross_origin_is_rejected(container, user_and_channel, db_url):
    client, token = logged_in(db_url, user_and_channel[0])
    assert client.post("/today/run", data={}).status_code == 403
    assert client.post("/today/run", data={"csrf": "wrong"}).status_code == 403
    r = client.post("/today/run", data={"csrf": token}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.post("/today/run", data={"csrf": token}, follow_redirects=False)
    assert r.status_code == 303 and "run=" in r.headers["location"]


def test_settings_change_needs_csrf(container, user_and_channel, db_url):
    client, token = logged_in(db_url, user_and_channel[0])
    form = {"stats_minutes": 5, "analytics_minutes": 60, "currency": "USD"}
    assert client.post("/settings", data=form).status_code == 403
    assert client.post("/settings", data=form | {"csrf": token}, follow_redirects=False).status_code == 303


def test_other_users_assets_are_not_visible(container, user_and_channel, db_url):
    container.scheduling.run_now(*user_and_channel)
    with container.uow() as uow:
        uow.runs.get(1)
        asset_id = uow.assets.for_run(1)[0].id
    me, _ = logged_in(db_url, user_and_channel[0])
    assert me.get(f"/assets/{asset_id}").status_code == 200
    other = container.accounts.owner("other@example.com")
    them, token = logged_in(db_url, other.id)
    assert them.get(f"/assets/{asset_id}").status_code == 404
    assert them.post(f"/assets/{asset_id}/status", data={"status": "approved", "csrf": token}).status_code == 404
    assert them.get("/api/runs/1").status_code == 404
    assert them.get(f"/files/asset/{asset_id}/video").status_code == 404


def test_all_pages_render_with_a_brief(container, user_and_channel, db_url):
    run = container.scheduling.run_now(*user_and_channel)
    client, _ = logged_in(db_url, user_and_channel[0])
    for path in (
        "/today",
        f"/today?run={run.run_id}",
        "/ideas",
        "/ideas?status=approved",
        "/trends",
        "/trends?min=3",
        "/jobs",
        "/settings",
        "/insights",
        "/shorts",
        "/insights/video/own0",
    ):
        r = client.get(path)
        assert r.status_code == 200, (path, r.text[:300])
    with container.uow() as uow:
        for a in uow.assets.for_run(run.run_id):
            assert client.get(f"/assets/{a.id}").status_code == 200
    assert "needs review" in client.get("/today").text
    assert client.get(f"/api/runs/{run.run_id}").json()["status"] == "done"


def test_edit_and_status_routes(container, user_and_channel, db_url):
    run = container.scheduling.run_now(*user_and_channel)
    client, token = logged_in(db_url, user_and_channel[0])
    with container.uow() as uow:
        assets = {a.kind: a for a in uow.assets.for_run(run.run_id)}
    sc = assets["script"]
    r = client.post(
        f"/assets/{sc.id}/edit", data={"csrf": token, "section_0": "My own hook words"}, follow_redirects=False
    )
    assert r.status_code == 303
    seo = assets["seo"]
    client.post(f"/assets/{seo.id}/edit", data={"csrf": token, "tags": "a, b ,", "next": "/today"})
    client.post(f"/assets/{seo.id}/status", data={"csrf": token, "status": "approved", "next": "//evil.example"})
    with container.uow() as uow:
        assert uow.assets.get(sc.id, user_and_channel[0]).data["script"]["sections"][0]["spoken"] == "My own hook words"
        s = uow.assets.get(seo.id, user_and_channel[0])
    assert s.data["tags"] == ["a", "b"] and s.status == "approved"
    r = client.post(
        "/api/hooks/score",
        json={"hooks": ["You lose 30% of rent before you see this."]},
        headers={"X-CSRF-Token": token},
    )
    assert r.json()["scores"][0]["panel_score"] > 0 and "not a prediction" in r.json()["note"]


def test_resumable_master_upload(container, user_and_channel, db_url):
    client, token = logged_in(db_url, user_and_channel[0])
    h = {"X-CSRF-Token": token}
    assert client.post("/api/media", json={"filename": "evil.exe", "size": 10}, headers=h).status_code == 400
    mid = client.post("/api/media", json={"filename": "master.mp4", "size": 10}, headers=h).json()["id"]
    assert client.put(f"/api/media/{mid}?offset=0", content=b"01234", headers=h).json()["received"] == 5
    stale = client.put(f"/api/media/{mid}?offset=0", content=b"01234", headers=h)  # a retried chunk
    assert stale.status_code == 409 and stale.json()["received"] == 5
    assert client.post(f"/api/media/{mid}/complete", headers=h).status_code == 409
    client.put(f"/api/media/{mid}?offset=5", content=b"56789", headers=h)
    assert client.post(f"/api/media/{mid}/complete", headers=h).json()["status"] == "ready"
    with container.uow() as uow:
        m = uow.media.get(mid)
    assert container.storage.get(m.storage_key).read_bytes() == b"0123456789"


def test_security_headers_and_health(container, user_and_channel, db_url):
    client, _ = logged_in(db_url, user_and_channel[0])
    r = client.get("/today")
    assert r.headers["x-frame-options"] == "DENY" and "default-src 'self'" in r.headers["content-security-policy"]
    assert "<script>" not in r.text  # no inline scripts, so the CSP can block injected ones
    assert "creds" not in r.text and "v1:" not in r.text
    health = client.get("/health")
    assert "postgres" in health.json()


def test_logout(container, user_and_channel, db_url):
    client, token = logged_in(db_url, user_and_channel[0])
    assert client.post("/logout", data={"csrf": token}, follow_redirects=False).status_code == 303
    assert client.get("/today", follow_redirects=False).headers["location"] == "/login"


def test_publish_modes_are_one_radio_group(container, user_and_channel, db_url):
    """Video, Short and Live must share a form owner, or picking Video leaves Live checked."""
    import re

    client, _ = logged_in(db_url, user_and_channel[0])
    html = client.get("/publish").text
    radios = re.findall(r'<input type="radio" name="mode" id="m-(\w+)"[^>]*>', html)
    owners = re.findall(r'<input type="radio" name="mode" id="m-\w+"[^>]*?form="(\w+)"', html)
    assert radios == ["video", "short", "live"] and owners == ["upload"] * 3
