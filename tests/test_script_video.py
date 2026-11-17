"""Script to video: Piper voice-over (faked here) and a white screen with black subtitles."""

import json
from datetime import timedelta

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


def in_hd(container, user_id, script_id):
    """Make the video (a quick draft), then the same video in HD, as the creator would before uploading."""
    container.review.make_video(user_id, script_id)
    with container.uow() as uow:
        n = uow.assets.get(script_id, user_id).data["video_n"]
    container.review.make_hd(user_id, script_id, n)


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
    container.review.make_video(user_id, long_script.id, voice="chatterbox-hi")
    container.review.make_video(user_id, container.other_short.id, voice="nonsense")
    with container.uow() as uow:
        assert uow.runs.get(res.run_id).status == RunStatus.DONE
        short, long_ = uow.assets.get(short_script.id, user_id), uow.assets.get(long_script.id, user_id)
    assert container.media["media"].videos[-3:] == [(3, True), (3, False), (3, True)]
    # the LLM storyboard reaches the renderer, numbering the narrator's sentences across the whole script
    assert [sc["sentences"] for sc in container.media["media"].stories[-2]] == [[0], [1], [2]]
    spoken = [voice for _, voice in container.media["narrator"].spoken]
    # a Hindi voice cannot read an English script, and an unknown voice means automatic: both get the English default
    assert spoken == ["chatterbox-en", "chatterbox-en", "chatterbox-en"]
    for a in (short, long_):
        assert a.data["video_key"].startswith("videos/") and a.data["audio_key"].startswith("audio/")
        assert container.storage.exists(a.data["video_key"]) and container.storage.exists(a.data["audio_key"])
        assert a.data["video_seconds"] > 0


def test_each_video_is_a_version_drawn_as_a_draft_then_the_same_video_in_hd(container, user_and_channel, db_url, yt):
    user_id, _, short_script = scripts(container, user_and_channel)
    media = container.media["media"]
    client, token = logged_in(db_url, user_id)

    def data():
        with container.uow() as uow:
            return uow.assets.get(short_script.id, user_id).data

    container.review.make_video(user_id, short_script.id)
    one = data()
    assert media.drafts[-1] and one["video_n"] == 1 and not one["video_hd"]  # a quick draft first
    r = client.post(f"/assets/{short_script.id}/upload", data={"csrf": token, "privacy": "private"})
    assert "Make it in HD first" in r.text and not yt.uploaded  # a draft is never uploaded

    container.clock.t += timedelta(seconds=5)
    container.review.make_video(user_id, short_script.id)
    two = data()
    assert two["video_n"] == 2 and [v["n"] for v in two["videos"]] == [1, 2]
    assert container.storage.exists(one["video_key"])  # the first version is kept

    r = client.post(f"/scripts/{short_script.id}/videos/1/use", data={"csrf": token}, follow_redirects=False)
    assert r.headers["location"] == f"/assets/{short_script.id}?m=edited#video"
    assert data()["video_key"] == one["video_key"] and len(media.videos) == 2  # nothing was made again

    r = client.post(f"/scripts/{short_script.id}/videos/1/hd", data={"csrf": token}, follow_redirects=False)
    assert "m=started" in r.headers["location"]
    hd = data()
    timeline, voice, draft = media.redrawn[-1]
    stored = json.loads(container.storage.get(one["videos"][0]["timeline_key"]).read_text())
    assert timeline == stored == {"timeline": media.videos[0][0]} and not draft  # version 1's own plan
    assert voice == container.storage.get(one["audio_key"]).read_bytes()  # its own voice: the same video, sharper
    assert hd["video_n"] == 1 and hd["video_hd"] and hd["video_key"] != one["video_key"]
    assert hd["videos"][0]["draft_key"] == one["video_key"]  # the draft stays with its version
    page = client.get(f"/assets/{short_script.id}").text
    assert "Version 2" in page and "Showing" in page and "Download HD (MP4)" in page

    for _ in range(5):  # only the newest few versions are kept, with their files
        container.clock.t += timedelta(seconds=5)
        container.review.make_video(user_id, short_script.id)
    last = data()
    assert [v["n"] for v in last["videos"]] == [3, 4, 5, 6, 7]
    assert not container.storage.exists(hd["video_key"]) and not container.storage.exists(one["audio_key"])

    client.post(f"/assets/{short_script.id}/delete", data={"csrf": token})
    for v in last["videos"]:
        assert not container.storage.exists(v["draft_key"]) and not container.storage.exists(v["timeline_key"])


def test_script_page_has_the_generate_button_the_player(container, user_and_channel, db_url):
    user_id, long_script, _ = scripts(container, user_and_channel)
    client, token = logged_in(db_url, user_id)
    before = client.get(f"/assets/{long_script.id}").text
    assert "Generate video" in before and "<video" not in before
    assert "B-roll" not in before
    assert "Chatterbox, English" in before and 'name="look"' not in before  # one look: the kinetic style
    r = client.post(
        f"/scripts/{long_script.id}/video",
        data={"csrf": token, "voice": "chatterbox-en"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and "m=started" in r.headers["location"]
    after = client.get(f"/assets/{long_script.id}").text
    assert "<video" in after and "Download draft (MP4)" in after and "Make a new version" in after
    assert "Looks good: make it in HD" in after
    assert "Last voice: Fake voice" in after
    assert '<option value="chatterbox-en" selected>' in after  # the voice used last stays picked
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
    assert "Upload to YouTube</button>" not in before and "Generate the video first" in before  # no video yet
    in_hd(container, user_id, long_script.id)
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
    in_hd(container, user_id, short_script.id)
    client, token = logged_in(db_url, user_id)
    page = client.get(f"/assets/{short_script.id}").text
    assert 'value="Stop overpaying rent"' in page and "One trick that cut my rent." in page and 'value="Shorts"' in page
    client.post(f"/assets/{short_script.id}/upload", data={"csrf": token, "privacy": "private"}, follow_redirects=False)
    desc = yt.uploaded[-1]["snippet"]["description"]
    assert "#Shorts" in desc and "©" in desc and "AI-generated voice" in desc  # the same footer as every upload
    nothing = client.post(f"/assets/{short_script.id + 5000}/upload", data={"csrf": token}, follow_redirects=False)
    assert nothing.status_code == 404


def test_the_video_comes_with_a_thumbnail_that_can_be_made_again_alone_and_goes_to_youtube(
    container, user_and_channel, db_url, yt
):
    user_id, long_script, _ = scripts(container, user_and_channel)
    in_hd(container, user_id, long_script.id)
    with container.uow() as uow:
        first = uow.assets.get(long_script.id, user_id).data
    assert first["thumb_key"].startswith("thumbs/") and container.storage.exists(first["thumb_key"])
    assert container.media["images"].thumbs[-1] == ("Rent Trap", "trap", "blue", "fire", False)
    client, token = logged_in(db_url, user_id)
    page = client.get(f"/assets/{long_script.id}").text
    assert "Make a new thumbnail" in page and client.get(f"/files/asset/{long_script.id}/thumbnail").status_code == 200
    videos = len(container.media["media"].videos)
    container.clock.advance(5) if hasattr(container.clock, "advance") else None
    r = client.post(
        f"/scripts/{long_script.id}/thumbnail",
        data={"csrf": token, "text": "My Own Words", "notes": "use a money sticker"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and "m=started" in r.headers["location"]
    with container.uow() as uow:
        second = uow.assets.get(long_script.id, user_id).data
    assert len(container.media["media"].videos) == videos  # only the thumbnail was made again
    # the creator's text wins; a sticker that does not exist falls back to one that does
    assert second["thumb"]["text"] == "My Own Words" and second["thumb"]["sticker"] == "sparkles"
    assert second["thumb"]["highlight"] == "Words" and second["video_key"] == first["video_key"]
    if second["thumb_key"] != first["thumb_key"]:
        assert not container.storage.exists(first["thumb_key"])
    yt.thumb_refused = True  # e.g. the channel is not verified for custom thumbnails
    client.post(f"/assets/{long_script.id}/upload", data={"csrf": token, "title": "T", "privacy": "private"})
    page = client.get(f"/assets/{long_script.id}").text
    assert len(yt.uploaded) == 1 and "did not take the thumbnail" in page and "Set this thumbnail on YouTube" in page
    yt.thumb_refused = False
    r = client.post(f"/assets/{long_script.id}/youtube-thumbnail", data={"csrf": token}, follow_redirects=False)
    assert r.status_code == 303 and yt.thumbs == ["vid123"]
    assert "This thumbnail is on YouTube." in client.get(f"/assets/{long_script.id}").text
    client.post(f"/assets/{long_script.id}/delete", data={"csrf": token})
    assert not container.storage.exists(second["thumb_key"])


def test_every_script_shows_where_it_is_on_the_way_to_youtube_and_the_one_next_step(
    container, user_and_channel, db_url
):
    user_id, long_script, _ = scripts(container, user_and_channel)
    client, _ = logged_in(db_url, user_id)

    def card(**data):
        with container.uow() as uow:
            a = uow.assets.get(long_script.id, user_id)
            a.data = a.data | data
            uow.assets.update(a)
            uow.commit()
        page = client.get("/scripts?format=long").text
        return page[page.index(f'href="/assets/{long_script.id}"') :].split("</article>")[0]

    first = card()
    assert 'aria-current="step"><i data-lucide="clapperboard"></i><span>Video' in first
    assert f'action="/scripts/{long_script.id}/video"' in first and "Generate video" in first
    page = client.get(f"/assets/{long_script.id}").text
    assert 'id="video"' in page and 'id="thumbnail"' in page and 'class="journey"' in page
    assert "Make thumbnail" in card(video_key="v.mp4", video_seconds=60)
    assert f'href="/assets/{long_script.id}#upload">Upload to YouTube' in card(thumb_key="t.jpg")
    assert "https://youtu.be/abc" in card(youtube_id="abc") and "aria-current" not in card()


def test_your_youtube_details_hook_and_media_survive_a_new_script(container, user_and_channel, db_url, yt):
    user_id, long_script, _ = scripts(container, user_and_channel)
    client, token = logged_in(db_url, user_id)
    in_hd(container, user_id, long_script.id)

    r = client.post(
        f"/scripts/{long_script.id}/details",
        data={"csrf": token, "title": " My  title ", "description": "Mine", "tags": "x, y", "hashtags": "rent, #tips"},
        follow_redirects=False,
    )
    assert r.headers["location"] == f"/assets/{long_script.id}?m=edited#details"  # the query before the fragment
    page = client.get(f"/assets/{long_script.id}").text
    assert 'id="yt-title" name="title" value="My title"' in page and 'value="x, y"' in page
    assert '<input type="hidden" name="title" value="My title">' in page  # the upload sends what you saved

    hook = long_script.data["hooks"][-1]["hook"]
    client.post(f"/assets/{long_script.id}/edit", data={"csrf": token, "section_0": hook})
    with container.uow() as uow:
        a = uow.assets.get(long_script.id, user_id)
    assert a.data["script"]["sections"][0]["spoken"] == hook and a.data["video_outdated"]  # the video says old words
    assert "The script changed after this video was made" in client.get(f"/assets/{long_script.id}").text

    container.review.regenerate(user_id, long_script.id)
    with container.uow() as uow:
        again = uow.assets.get(long_script.id, user_id)
    assert again.data["history"] and again.data["yt"]["title"] == "My title"  # your text is kept
    assert again.data["video_key"] == a.data["video_key"] and again.data["thumb_key"] == a.data["thumb_key"]
    assert again.data["video_outdated"] and again.data["idea"] == a.data["idea"]  # the brief as you wrote it

    container.clock.t += timedelta(seconds=5)  # a new run, not the same one again
    container.review.make_video(user_id, long_script.id)
    with container.uow() as uow:
        assert not uow.assets.get(long_script.id, user_id).data["video_outdated"]


def test_a_few_lines_are_enough_to_create_a_video(container, user_and_channel, db_url):
    user_id, _ = user_and_channel
    client, token = logged_in(db_url, user_id)
    about = "Python se video editing automate kaise kare. Beginners ke liye, FFmpeg ke saath."
    r = client.post("/scripts/new", data={"csrf": token, "details": about, "format": "short"}, follow_redirects=False)
    assert r.status_code == 303
    with container.uow() as uow:
        idea = uow.assets.get(int(r.headers["location"].split("/")[3].split("?")[0]), user_id)
    assert idea.title == about and idea.data["angle"] == about  # its start names the idea
