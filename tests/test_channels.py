"""Each channel is its own world: briefs, ideas, jobs, schedule and voice never mix."""

import sqlite3

import pytest

from tests.fakes import vid
from tests.test_characterization import logged_in
from ytstudio.cli import alembic_config
from ytstudio.domain.models import OAuthToken, Schedule
from ytstudio.ports import ChannelInfo


@pytest.fixture
def two(container, yt, user_and_channel):
    """One account, two channels, each with its own voice. Idea titles carry the channel name."""
    user_id = user_and_channel[0]
    yt.infos["UCtwo"] = ChannelInfo(id="UCtwo", title="Second Channel", thumb="t2", uploads_playlist="UUtwo")
    yt.own["UCtwo"] = [
        vid(f"two{i}", "UCtwo", v, days_old=3 + i) for i, v in enumerate([400, 410, 420, 430, 440, 3000])
    ]
    container.accounts.connect(user_id, OAuthToken(refresh_token="rt2", scopes=["s"]))
    base = container.llm.responses["ideas"]["ideas"][0]
    container.llm.responses["ideas"] = lambda req: {
        "ideas": [base | {"title": f"{req.variables['channel']} idea {i}"} for i in range(5)]
    }
    for channel, voice in (("UCme", "VOICE-A formal"), ("UCtwo", "VOICE-B funny")):
        container.scheduling.save(user_id, Schedule(channel_id=channel, enabled=True, voice_md=voice))
    return user_id


def test_ideas_brief_and_jobs_are_separate(container, two, db_url):
    for ch in ("UCme", "UCtwo"):
        container.scheduling.run_now(two, ch)
    client, _ = logged_in(db_url, two)
    for ch, mine, other in (
        ("UCme", "My Channel idea", "Second Channel idea"),
        ("UCtwo", "Second Channel idea", "My Channel idea"),
    ):
        client.cookies.set("channel", ch)
        for path in ("/ideas", "/today"):
            html = client.get(path).text
            assert mine in html and other not in html, (ch, path)
        other_ch = "UCtwo" if ch == "UCme" else "UCme"
        jobs = client.get("/jobs").text
        assert f"{ch}:daily" in jobs and f"{other_ch}:daily" not in jobs


def test_each_channel_uses_its_own_voice(container, two):
    for ch in ("UCme", "UCtwo"):
        container.scheduling.run_now(two, ch)
    voices = {c.variables["channel"]: c.variables["voice"] for c in container.llm.calls if c.template == "ideas"}
    assert voices == {"My Channel": "VOICE-A formal", "Second Channel": "VOICE-B funny"}


def test_asset_link_opens_under_its_own_channel(container, two, db_url):
    res = container.scheduling.run_now(two, "UCme")
    with container.uow() as uow:
        asset_id = next(a.id for a in uow.assets.for_run(res.run_id) if a.kind == "idea")
    client, _ = logged_in(db_url, two)
    client.cookies.set("channel", "UCtwo")
    r = client.get(f"/assets/{asset_id}")
    assert r.status_code == 200 and "Channel: My Channel" in r.text and "channel=UCme" in r.headers["set-cookie"]
    # switching channel on a detail page goes to the list page, not back to the other channel's asset
    back = client.get("/use/UCtwo", headers={"referer": f"http://testserver/assets/{asset_id}"}, follow_redirects=False)
    assert back.headers["location"] == "/today"


def test_saving_settings_changes_only_the_selected_channel(container, two, db_url):
    client, token = logged_in(db_url, two)
    client.cookies.set("channel", "UCtwo")
    form = {
        "csrf": token,
        "timezone": "Asia/Kolkata",
        "run_time": "09:30",
        "days": ["0", "1"],
        "language": "hi",
        "region": "in",
        "niche": "gaming",
        "voice_md": "NEW B",
        "target_minutes": 10,
    }
    assert client.post("/settings/schedule", data=form, follow_redirects=False).status_code == 303
    with container.uow() as uow:
        a, b = uow.schedules.get("UCme"), uow.schedules.get("UCtwo")
    assert (b.niche, b.voice_md, b.timezone, b.language, b.region) == ("gaming", "NEW B", "Asia/Kolkata", "hi", "IN")
    assert (a.niche, a.voice_md, a.timezone) == ("", "VOICE-A formal", "UTC")


def test_voice_migration_copies_the_old_user_voice_to_each_channel(tmp_path):
    from alembic import command

    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "0005")
    con = sqlite3.connect(tmp_path / "m.db")
    con.execute(
        "INSERT INTO user (id, email, created_at, voice_md) VALUES (1, 'a@x.com', '2026-01-01', 'my old voice')"
    )
    con.execute(
        "INSERT INTO channel (id, title, thumb, creds, subscribers, views, videos, user_id, status) "
        "VALUES ('UC1', 't', '', 'c', 0, 0, 0, 1, 'ok')"
    )
    con.execute(
        "INSERT INTO schedule (channel_id, enabled, timezone, run_time, days, language, region, niche, "
        "custom_topics, target_minutes, shorts_enabled, confirmed) "
        "VALUES ('UC1', 1, 'UTC', '07:00', '0123456', 'en', 'US', '', '[]', 8, 1, 1)"
    )
    con.commit()
    con.close()
    command.upgrade(cfg, "head")
    con = sqlite3.connect(tmp_path / "m.db")
    assert con.execute("SELECT voice_md FROM schedule WHERE channel_id='UC1'").fetchone()[0] == "my old voice"
    assert "voice_md" not in [r[1] for r in con.execute("PRAGMA table_info(user)")]
