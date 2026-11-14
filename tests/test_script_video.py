"""Script to video: Piper voice-over (faked here) and a white screen with black subtitles."""

from tests.test_characterization import logged_in
from ytstudio.application.steps_video import spoken_lines
from ytstudio.domain.models import RunStatus


def scripts(container, user_and_channel):
    user_id, channel = user_and_channel
    run = container.scheduling.run_now(user_id, channel)
    with container.uow() as uow:
        made = [a for a in uow.assets.for_run(run.run_id) if a.kind == "script"]
    long_script = next(a for a in made if a.data["format"] == "long")
    short_script, other_short = [a for a in made if a.data["format"] == "short"][:2]
    container.other_short = other_short
    return user_id, long_script, short_script


def test_only_spoken_words_are_read_in_sentences_with_longer_pauses_between_sections():
    lines = spoken_lines(
        [
            {"spoken": "Stop overpaying rent. Do **this** instead! [pause here]\nReally?"},
            {"spoken": "किराया कम करो। रसीद माँगो।"},
            {"spoken": "  ...  "},
        ]
    )
    assert [t for t, _ in lines] == [
        "Stop overpaying rent.",
        "Do this instead!",
        "Really?",
        "किराया कम करो।",
        "रसीद माँगो।",
    ]
    assert [p for _, p in lines] == [0.28, 0.28, 0.7, 0.28, 0.7]


def test_a_short_gets_a_vertical_video_and_a_long_video_a_wide_one(container, user_and_channel):
    user_id, long_script, short_script = scripts(container, user_and_channel)
    res = container.review.make_video(user_id, short_script.id)
    container.review.make_video(user_id, long_script.id, voice="hi-IN-SwaraNeural")
    container.review.make_video(user_id, container.other_short.id, voice="nonsense")
    with container.uow() as uow:
        assert uow.runs.get(res.run_id).status == RunStatus.DONE
        short, long_ = uow.assets.get(short_script.id, user_id), uow.assets.get(long_script.id, user_id)
    assert container.media["media"].videos[-3:] == [(3, True), (3, False), (3, True)]
    spoken = [voice for _, voice in container.media["narrator"].spoken]
    assert spoken == ["en-IN-PrabhatNeural", "hi-IN-SwaraNeural", "en-IN-PrabhatNeural"]  # unknown choices are ignored
    for a in (short, long_):
        assert a.data["video_key"].startswith("videos/") and a.data["audio_key"].startswith("audio/")
        assert container.storage.exists(a.data["video_key"]) and container.storage.exists(a.data["audio_key"])
        assert a.data["video_seconds"] > 0


def test_making_the_video_again_replaces_the_old_files_and_deleting_the_script_removes_them(
    container, user_and_channel, db_url
):
    user_id, _, short_script = scripts(container, user_and_channel)
    container.review.make_video(user_id, short_script.id)
    with container.uow() as uow:
        first = uow.assets.get(short_script.id, user_id).data
    container.clock.advance(5) if hasattr(container.clock, "advance") else None
    container.review.make_video(user_id, short_script.id)
    with container.uow() as uow:
        second = uow.assets.get(short_script.id, user_id).data
    if second["video_key"] != first["video_key"]:
        assert not container.storage.exists(first["video_key"])
    client, token = logged_in(db_url, user_id)
    client.post(f"/assets/{short_script.id}/delete", data={"csrf": token})
    assert not container.storage.exists(second["video_key"]) and not container.storage.exists(second["audio_key"])


def test_script_page_has_the_generate_button_the_player_and_a_broll_list(container, user_and_channel, db_url):
    user_id, long_script, _ = scripts(container, user_and_channel)
    client, token = logged_in(db_url, user_id)
    before = client.get(f"/assets/{long_script.id}").text
    assert "Generate video" in before and "<video" not in before
    assert "B-roll shot list" in before and "https://www.pexels.com/search/videos/receipts/" in before
    assert "Indian English, man" in before and 'name="look"' not in before  # one look: the kinetic style
    r = client.post(
        f"/scripts/{long_script.id}/video",
        data={"csrf": token, "voice": "en-IN-NeerjaNeural"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and "m=started" in r.headers["location"]
    after = client.get(f"/assets/{long_script.id}").text
    assert "<video" in after and "Download video (MP4)" in after and "Make the video again" in after
    assert "Last voice: Fake voice" in after
    assert f"/files/asset/{long_script.id}/audio?download=1" in after
    assert client.get(f"/files/asset/{long_script.id}/video").status_code == 200
    assert "Video ready" in client.get("/scripts?format=long").text
    bad = client.post(f"/scripts/{long_script.id + 9999}/video", data={"csrf": token}, follow_redirects=False)
    assert bad.status_code == 404


def test_a_long_video_made_from_a_script_uploads_with_its_package_title_and_seo(
    container, user_and_channel, db_url, yt
):
    user_id, long_script, short_script = scripts(container, user_and_channel)
    client, token = logged_in(db_url, user_id)
    before = client.get(f"/assets/{long_script.id}").text
    assert "Upload to YouTube</button>" not in before and "After the video is made" in before  # no video yet
    container.review.make_video(user_id, long_script.id)
    page = client.get(f"/assets/{long_script.id}").text
    assert f'action="/assets/{long_script.id}/upload"' in page and "Upload to YouTube" in page
    assert 'value="I tracked rent for 90 days"' in page  # the package's best title
    assert "How I cut rent." in page and 'value="rent, india"' in page  # the SEO description and tags
    r = client.post(
        f"/assets/{long_script.id}/upload",
        data={
            "csrf": token,
            "title": "My long title",
            "description": "Long text",
            "tags": "a, b",
            "privacy": "unlisted",
            "next": f"/assets/{long_script.id}",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"].startswith(f"/assets/{long_script.id}?m=short_uploaded")
    body = yt.uploaded[-1]
    assert body["snippet"]["title"] == "My long title" and "#Shorts" not in body["snippet"]["description"]
    assert body["snippet"]["tags"] == ["a", "b"] and body["status"]["privacyStatus"] == "unlisted"
    done = client.get(f"/assets/{long_script.id}").text
    assert (
        "Uploaded to YouTube" in done and "https://youtu.be/vid123" in done and "Upload to YouTube</button>" not in done
    )
    with container.uow() as uow:
        saved = uow.assets.get(long_script.id, user_id)
    assert (
        saved.title == long_script.title and saved.data["youtube_title"] == "My long title"
    )  # the idea keeps its name
    again = client.post(f"/assets/{long_script.id}/upload", data={"csrf": token}, follow_redirects=False)
    assert again.status_code != 500 and len(yt.uploaded) == 1  # never uploaded twice


def test_a_short_script_video_uploads_as_a_short(container, user_and_channel, db_url, yt):
    user_id, _, short_script = scripts(container, user_and_channel)
    container.review.make_video(user_id, short_script.id)
    client, token = logged_in(db_url, user_id)
    page = client.get(f"/assets/{short_script.id}").text
    assert 'value="Stop overpaying rent"' in page and "One trick that cut my rent." in page and 'value="Shorts"' in page
    client.post(f"/assets/{short_script.id}/upload", data={"csrf": token, "privacy": "private"}, follow_redirects=False)
    assert yt.uploaded[-1]["snippet"]["description"].endswith("#Shorts")
    nothing = client.post(f"/assets/{short_script.id + 5000}/upload", data={"csrf": token}, follow_redirects=False)
    assert nothing.status_code == 404
