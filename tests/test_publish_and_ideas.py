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


def test_ideas_are_on_the_scripts_page_there_is_no_separate_ideas_page(container, user_and_channel, db_url):
    user_id, channel = user_and_channel
    client, token = logged_in(db_url, user_id)
    r = client.get("/ideas?format=short", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/scripts?format=short"  # old links still land
    assert 'href="/ideas"' not in client.get("/scripts").text  # no Ideas item in the sidebar
    idea = container.review.add_idea(user_id, channel, "Saved for later", format="long")
    page = client.get("/scripts").text
    assert "Saved for later" in page and "No script yet" in page and "My idea</span>" in page
    assert f'action="/ideas/{idea.id}/develop"' in page and "Write the script" in page
    r = client.post(f"/ideas/{idea.id}/develop", data={"csrf": token}, follow_redirects=False)
    opened = client.get(r.headers["location"], follow_redirects=False)  # follows the run, then opens the script
    assert opened.headers["location"].startswith("/assets/")
    page = client.get("/scripts").text
    assert "No script yet" not in page and "Saved for later" in page  # now listed once, as its script


def test_create_your_own_script_on_the_scripts_page_opens_the_script_when_it_is_written(
    container, user_and_channel, db_url
):
    user_id, _ = user_and_channel
    client, token = logged_in(db_url, user_id)
    listing = client.get("/scripts?format=short").text
    assert 'href="/scripts/new?format=short"' in listing and "Create a Short" in listing
    form = client.get("/scripts/new?format=short").text
    assert 'action="/scripts/new"' in form and 'value="short" checked' in form and 'name="details"' in form
    notes = "FFmpeg, Remotion aur mera pipeline, ek command mein video"
    r = client.post(
        "/scripts/new",
        data={"csrf": token, "title": "Python se video automate karo", "details": notes, "format": "short"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"].startswith("/scripts/new/")
    done = client.get(r.headers["location"], follow_redirects=False)  # the run finished: straight to the script
    assert done.status_code == 303 and done.headers["location"].startswith("/assets/")
    script_page = client.get(done.headers["location"]).text
    assert "Python se video automate karo" in script_page and "Generate video" in script_page
    assert container.llm.calls[-1].variables["idea"]["angle"] == notes  # your notes are what the script follows
    assert "Python se video automate karo" in client.get("/scripts?format=short").text
    assert client.get("/scripts/new/999999").status_code == 404


def test_create_your_own_script_shows_progress_while_the_script_is_being_written(container, user_and_channel, db_url):
    user_id, channel = user_and_channel
    idea = container.review.add_idea(user_id, channel, "Not written yet", format="long")
    client, _ = logged_in(db_url, user_id)
    page = client.get(f"/scripts/new/{idea.id}").text  # no script yet: the idea and the progress, no form
    assert "Not written yet" in page and "opens here by itself" in page and 'action="/scripts/new"' not in page


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
    long_page, short_page = client.get("/scripts?format=long").text, client.get("/scripts?format=short").text
    assert long_page.count('<article class="card') == 2 and short_page.count('<article class="card') == 5
    assert 'aria-current="page"><i data-lucide="clapperboard"></i>Long videos<span class="n">2</span>' in long_page
    assert 'Shorts<span class="n">5</span>' in long_page and "Auto</span>" in long_page
    assert client.get("/scripts").text.count('<article class="card') == 2  # long videos first
    assert "No script yet" not in short_page  # every daily idea was written: each shows once, as its script
    today = client.get("/today").text
    assert "Long videos today" in today and "Shorts today" in today and today.count("Open the script") == 7
    container.review.add_idea(user_id, channel, "My short", format="short")
    mine = client.get("/scripts?format=short").text
    assert "My short" in mine and "My idea</span>" in mine and "Write the script" in mine


def test_publish_page_writes_text_and_thumbnail_and_uploads_them_by_the_same_rules(
    container, user_and_channel, db_url, yt
):
    client, token = logged_in(db_url, user_and_channel[0])
    page = client.get("/publish").text
    assert "Write with AI" in page and 'name="hashtags"' in page and 'name="thumb_key"' in page
    h = {"X-CSRF-Token": token}
    short = client.post("/publish/write", data={"about": "too short"}, headers=h)
    assert short.status_code == 400 and "a line or two" in short.json()["detail"]
    out = client.post(
        "/publish/write", data={"about": "Python on a new Mac, for beginners", "mode": "video"}, headers=h
    )
    w = out.json()
    assert w["title"] == "Python Setup on Mac in 5 Minutes" and w["hashtags"] == ["#python", "#coding"]
    assert w["tags"] == ["python setup mac", "python"]  # '#' and duplicates dropped
    assert w["thumb_key"].startswith("thumbs/UCme/upload-") and client.get(w["thumb_url"]).status_code == 200
    assert container.media["images"].thumbs[-1] == ("5 Min Setup", "setup", "yellow", "laptop", False)
    redo = client.post(
        "/publish/thumbnail", data={"title": w["title"], "text": "Do This First", "old": w["thumb_key"]}, headers=h
    ).json()
    assert container.media["images"].thumbs[-1][0] == "Do This First"
    if redo["thumb_key"] != w["thumb_key"]:
        assert not container.storage.exists(w["thumb_key"])  # the draft it replaced is gone
    own = client.post(
        "/publish/thumbnail/own",
        files={"image": ("t.png", b"png", "image/png")},
        data={"old": redo["thumb_key"]},
        headers=h,
    ).json()
    assert own["thumb_key"].startswith("thumbs/UCme/upload-")
    assert client.get("/files/thumb?key=thumbs/UCother/upload-1.jpg").status_code == 404
    r = upload(client, token, title=w["title"], description="My text", tags="a, a, b", hashtags="python, #coding",
               thumb_key=own["thumb_key"], mode="short")  # fmt: skip
    body = yt.uploaded[-1]["snippet"]
    assert r.status_code == 200 and yt.thumbs == ["vid123"]
    assert body["description"].startswith("My text\n\n#python #coding #Shorts\n\n© ") and body["tags"] == ["a", "b"]
    assert "AI-generated" not in body["description"]  # your own footage: no AI-voice credits
    stolen = upload(client, token, thumb_key="thumbs/UCother/upload-1.jpg")
    assert stolen.status_code == 400 and "another channel" in stolen.text
