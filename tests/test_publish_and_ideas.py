"""Your own ideas, the Scripts page, and the Publish page's auto-fill and schedule."""

from datetime import UTC, datetime

import pytest

from tests.test_characterization import logged_in
from ytstudio.domain.errors import PermanentError
from ytstudio.domain.models import Schedule
from ytstudio.domain.schedule import parse_local_datetime

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


def test_schedule_time_uses_the_channel_time_zone_and_must_be_in_the_future():
    assert parse_local_datetime("2026-11-20T18:30", "Asia/Kolkata", NOW) == datetime(2026, 11, 20, 13, 0, tzinfo=UTC)
    with pytest.raises(PermanentError, match="at least 10 minutes"):
        parse_local_datetime("2026-11-11T12:05", "UTC", NOW)
    with pytest.raises(PermanentError, match="not valid"):
        parse_local_datetime("tomorrow evening", "UTC", NOW)


def test_add_own_idea_then_develop_it_into_a_script(container, user_and_channel):
    user_id, channel = user_and_channel
    idea = container.review.add_idea(
        user_id, channel, "  Build an AI app\nwith FastAPI ", "Keep it beginner friendly", format="long"
    )
    assert idea.title == "Build an AI app with FastAPI" and idea.data["source"] == "manual"
    assert idea.data["format"] == "long"
    run = container.review.develop(user_id, idea.id)
    with container.uow() as uow:
        made = {a.kind: a for a in uow.assets.for_run(run.run_id)}
    assert {"script", "package", "seo"} <= set(made) and made["script"].parent_id == idea.id
    # the idea you wrote, not a model's idea, is what the script is built from
    assert container.llm.calls[-1].variables["idea"]["angle"] == "Keep it beginner friendly"
    with pytest.raises(PermanentError, match="not regenerated"):
        container.review.regenerate(user_id, idea.id)
    with pytest.raises(PermanentError, match="Write the idea"):
        container.review.add_idea(user_id, channel, "   ")


def test_a_short_idea_becomes_a_short_script_with_title_and_description(container, user_and_channel):
    user_id, channel = user_and_channel
    idea = container.review.add_idea(user_id, channel, "Rent trick", "One point only", format="short")
    assert idea.data["format"] == "short" and idea.data["effort"] == "short"
    run = container.review.develop(user_id, idea.id)
    with container.uow() as uow:
        made = {a.kind: a for a in uow.assets.for_run(run.run_id)}
    assert set(made) == {"script"} and made["script"].parent_id == idea.id  # no package or SEO for a Short
    data = made["script"].data
    assert data["format"] == "short" and data["title"] and data["description"] and data["hook"]["hook"]
    regen = container.review.regenerate(user_id, made["script"].id)
    with container.uow() as uow:
        again = uow.assets.get(made["script"].id, user_id)
    assert uow and regen.run_id and again.data["format"] == "short" and len(again.data["history"]) == 1
    assert again.title == "Rent trick"  # the idea's name, not the model's new Short title


def test_ideas_page_form_adds_an_idea_for_this_channel_only(container, user_and_channel, db_url):
    client, token = logged_in(db_url, user_and_channel[0])
    r = client.post("/ideas/new", data={"csrf": token, "title": "My topic", "mode": "save"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/ideas")
    assert "My topic" in client.get("/ideas").text
    r = client.post("/ideas/new", data={"csrf": token, "title": "Do it now", "mode": "script"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/assets/")
    assert "Do it now" in client.get("/scripts").text  # the script shows up on the Scripts page


def test_scripts_page_lists_the_daily_script(container, user_and_channel, db_url):
    container.scheduling.run_now(*user_and_channel)
    client, _ = logged_in(db_url, user_and_channel[0])
    page = client.get("/scripts").text
    assert "Idea 0" in page and "hook panel score" in page and "needs review" in page


def test_publish_page_offers_the_written_title_description_and_tags(container, user_and_channel, db_url):
    container.scheduling.run_now(*user_and_channel)
    client, _ = logged_in(db_url, user_and_channel[0])
    page = client.get("/publish").text
    assert "Fill from your scripts" in page and "I tracked rent for 90 days" in page
    assert "How I cut rent." in page and "india" in page and 'type="datetime-local"' in page


def upload(client, token, **extra):
    data = {"title": "T", "privacy": "public", "mode": "video"} | extra
    return client.post(
        "/upload",
        data=data,
        files={"video": ("a.mp4", b"x", "video/mp4")},
        headers={"X-CSRF-Token": token},
    )


def test_upload_now_keeps_the_visibility_you_chose(container, user_and_channel, db_url, yt):
    client, token = logged_in(db_url, user_and_channel[0])
    assert upload(client, token).status_code == 200
    assert yt.uploaded[-1]["status"] == {"privacyStatus": "public", "selfDeclaredMadeForKids": False}


def test_scheduled_upload_is_private_with_a_publish_time_in_the_channel_zone(container, user_and_channel, db_url, yt):
    user_id, channel = user_and_channel
    container.scheduling.save(user_id, Schedule(channel_id=channel, timezone="Asia/Kolkata"))
    client, token = logged_in(db_url, user_id)
    r = upload(client, token, publish_at="2026-11-20T18:30")
    assert r.status_code == 200 and "scheduled for 2026-11-20 18:30 (Asia/Kolkata)" in r.text
    assert yt.uploaded[-1]["status"] == {
        "privacyStatus": "private",  # the visibility chosen is overridden: YouTube publishes it at publishAt
        "publishAt": "2026-11-20T13:00:00Z",
        "selfDeclaredMadeForKids": False,
    }


def test_schedule_in_the_past_is_refused_and_nothing_is_uploaded(container, user_and_channel, db_url, yt):
    client, token = logged_in(db_url, user_and_channel[0])
    r = upload(client, token, publish_at="2020-01-01T10:00")
    assert r.status_code == 400 and "at least 10 minutes" in r.text and yt.uploaded == []


def test_scripts_and_ideas_pages_split_long_videos_from_shorts(container, user_and_channel, db_url):
    from tests.test_characterization import logged_in

    user_id, channel = user_and_channel
    container.scheduling.run_now(user_id, channel)  # a daily run: 2 long + 5 Shorts, every idea written out
    client, token = logged_in(db_url, user_id)
    for path in ("/scripts", "/ideas"):
        long_page, short_page = client.get(f"{path}?format=long").text, client.get(f"{path}?format=short").text
        assert long_page.count('<article class="card') == 2 and short_page.count('<article class="card') == 5, path
        assert 'aria-current="page"><i data-lucide="clapperboard"></i>Long videos<span class="n">2</span>' in long_page
        assert 'Shorts<span class="n">5</span>' in long_page and "Auto</span>" in long_page, path
        assert client.get(path).text.count('<article class="card') == 2  # long videos first
    assert "Open the script" in client.get("/ideas?format=short").text
    today = client.get("/today").text
    assert "Long videos today" in today and "Shorts today" in today and today.count("Open the script") == 7
    page = client.post(
        "/ideas/new",
        data={"csrf": token, "title": "My short", "format": "short", "mode": "save"},
        follow_redirects=False,
    )
    assert page.headers["location"].startswith("/ideas?format=short")
    mine = client.get("/ideas?format=short").text
    assert "My short" in mine and "Manual</span>" in mine and "Write the Short script" in mine
