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
    short_script = next(a for a in made if a.data["format"] == "short")
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
    assert [p for _, p in lines] == [0.2, 0.2, 0.55, 0.2, 0.55]


def test_a_short_gets_a_vertical_video_and_a_long_video_a_wide_one(container, user_and_channel):
    user_id, long_script, short_script = scripts(container, user_and_channel)
    res = container.review.make_video(user_id, short_script.id)
    container.review.make_video(user_id, long_script.id, voice="hi")
    with container.uow() as uow:
        assert uow.runs.get(res.run_id).status == RunStatus.DONE
        short, long_ = uow.assets.get(short_script.id, user_id), uow.assets.get(long_script.id, user_id)
    assert container.media["media"].videos[-2:] == [(3, True), (3, False)]
    assert container.media["narrator"].spoken[0][1] == "en" and container.media["narrator"].spoken[1][1] == "hi"
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
    r = client.post(f"/scripts/{long_script.id}/video", data={"csrf": token, "voice": "en"}, follow_redirects=False)
    assert r.status_code == 303 and "m=started" in r.headers["location"]
    after = client.get(f"/assets/{long_script.id}").text
    assert "<video" in after and "Download video (MP4)" in after and "Make the video again" in after
    assert f"/files/asset/{long_script.id}/audio?download=1" in after
    assert client.get(f"/files/asset/{long_script.id}/video").status_code == 200
    assert "Video ready" in client.get("/scripts?format=long").text
    bad = client.post(f"/scripts/{long_script.id + 9999}/video", data={"csrf": token}, follow_redirects=False)
    assert bad.status_code == 404
