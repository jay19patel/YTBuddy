"""Web layer: login, CSRF, owner scoping, resumable upload, security headers, every page renders with data."""

from fastapi.testclient import TestClient

from tests.test_characterization import logged_in
from ytstudio.adapters.persistence.web_sessions import WebSessions
from ytstudio.interfaces.web.app import create_app


def test_google_login_creates_a_session_for_an_allowed_gmail(container, db_url):
    client = TestClient(create_app())
    r = client.get("/login", follow_redirects=False)  # straight to Google; no password or PIN form
    assert r.status_code == 303 and "accounts.google.com" in r.headers["location"] and "ytnonce" in r.cookies
    r = client.get("/oauth2callback?state=st1&code=c", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/insights"
    assert WebSessions(db_url).get(r.cookies["ytsid"])
    assert "httponly" in r.headers["set-cookie"].lower()
    assert client.get("/today").status_code == 200


def test_gmail_not_on_the_list_is_refused(container, db_url):
    container.oauth.email = "stranger@example.com"
    client = TestClient(create_app())
    client.get("/login", follow_redirects=False)
    r = client.get("/oauth2callback?state=st1&code=c", follow_redirects=False)
    assert r.status_code == 403 and "ALLOWED_EMAILS" in r.text and "ytsid" not in r.cookies


def test_the_same_gmail_on_a_second_device_sees_the_same_history(container, user_and_channel, db_url):
    container.scheduling.run_now(*user_and_channel)  # a brief made from "device one"
    phone = TestClient(create_app())  # a different browser with no cookies
    phone.get("/login", follow_redirects=False)
    phone.get("/oauth2callback?state=st1&code=c", follow_redirects=False)  # fake Google signs in me@example.com
    page = phone.get("/ideas").text
    assert "Idea 0" in page and "My Channel" in page


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


def test_about_is_pushed_to_youtube_and_pulled_back(container, user_and_channel, db_url):
    client, token = logged_in(db_url, user_and_channel[0])
    form = {"description": "Python tutorials", "keywords": 'python "ai apps"'}
    assert client.post("/settings/about", data=form).status_code == 403
    assert client.post("/settings/about", data=form | {"csrf": token}, follow_redirects=False).status_code == 303
    assert container.youtube.calls[-1] == "about:UCme"
    assert "Python tutorials" in client.get("/settings").text
    container.youtube.infos["UCme"].description = "Edited in Studio"  # changed on YouTube's side
    container.sync.sync_stats()
    assert "Edited in Studio" in client.get("/settings").text
    too_long = client.post("/settings/about", data={"description": "x" * 1001, "csrf": token})
    assert too_long.status_code == 400
