"""Script language (Hindi, English, Hindi + English mix) and the voice that follows the script's own words."""

from datetime import timedelta

import pytest

from tests.test_characterization import logged_in
from ytstudio.domain.video import default_voice, detect_language, script_language, voice_for, voices_for

HINDI = "किराया ज्यादा मत दो, यह करो।"
HINGLISH = "Rent pe har mahine paise waste ho rahe hain? Ruko, yeh dekho."
ENGLISH = "Stop overpaying rent. Ask for receipts every month."


@pytest.mark.parametrize(
    ("text", "language"),
    [
        (HINDI, "hi"),
        (HINGLISH, "hinglish"),
        (ENGLISH, "en"),
        ("AI tools ka use karo aur time bachao", "hinglish"),
        ("Rent pe घर का किराया कम करो", "hi"),  # mostly Devanagari with an English word
        ("", "en"),
    ],
)
def test_the_language_of_a_text_is_detected(text, language):
    assert detect_language(text) == language


def test_a_voice_that_cannot_read_the_script_is_swapped_for_one_that_can():
    assert voice_for(ENGLISH, "hi-IN-SwaraNeural") == "en-IN-PrabhatNeural"  # Hindi voice on English words
    assert voice_for(HINDI, "en-IN-NeerjaNeural") == "hi-IN-MadhurNeural"  # English voice on Devanagari
    assert voice_for(HINDI, "hi-IN-SwaraNeural") == "hi-IN-SwaraNeural"  # a fitting choice is kept
    assert voice_for(HINGLISH, "en-IN-NeerjaNeural") == "en-IN-NeerjaNeural"
    assert voice_for(HINGLISH, "") == default_voice("hinglish") == "en-IN-PrabhatNeural"
    assert voice_for(HINDI, "nonsense") == "hi-IN-MadhurNeural"
    assert {v.language for v in voices_for(HINDI)} == {"hi"} and {v.language for v in voices_for(ENGLISH)} == {"en"}
    # Chatterbox reads Hindi, and Hinglish in Roman letters with a Hindi accent; English scripts are not offered that
    assert "chatterbox-hi" in {v.id for v in voices_for(HINDI)}
    assert {"chatterbox-en", "chatterbox-hinglish"} <= {v.id for v in voices_for(HINGLISH)}
    assert "chatterbox-hinglish" not in {v.id for v in voices_for(ENGLISH)}
    assert voice_for(HINGLISH, "chatterbox-hinglish") == "chatterbox-hinglish"


def test_a_script_is_in_the_language_chosen_for_it_else_the_language_of_its_words_else_the_channels():
    sections = [{"spoken": HINDI}]
    assert script_language({"language": "hinglish", "script": {"sections": sections}}, "en") == "hinglish"
    assert script_language({"idea": {"language": "hi"}}, "en") == "hi"
    assert script_language({"script": {"sections": sections}}, "en") == "hi"  # nothing chosen: read it from the words
    assert script_language({}, "hinglish") == "hinglish" and script_language({}, "klingon") == "en"


def test_the_language_picked_for_an_idea_is_what_its_script_and_voice_are_in(container, user_and_channel):
    user_id, channel = user_and_channel
    idea = container.review.add_idea(user_id, channel, "Rent trick", "One point", format="short", language="hi")
    assert idea.data["language"] == "hi"
    run = container.review.develop(user_id, idea.id)
    with container.uow() as uow:
        script = next(a for a in uow.assets.for_run(run.run_id) if a.kind == "script")
    assert container.llm.calls[-1].variables["language"] == "Hindi (Devanagari script)"
    assert script.data["language"] == "hi" and script.data["script"]["sections"][0]["spoken"] == HINDI
    for asked in ("", "en-IN-NeerjaNeural", "hi-IN-SwaraNeural"):  # automatic, a wrong voice, a fitting voice
        container.review.make_video(user_id, script.id, asked)
        container.clock.t += timedelta(seconds=5)  # a video made in the same second would count as the same job
    spoken = [voice for _, voice in container.media["narrator"].spoken]
    assert spoken == ["hi-IN-MadhurNeural", "hi-IN-MadhurNeural", "hi-IN-SwaraNeural"]


def test_an_english_script_is_regenerated_in_hindi_when_hindi_is_picked(container, user_and_channel, db_url):
    user_id, channel = user_and_channel
    idea = container.review.add_idea(user_id, channel, "Rent trick", "One point", format="short", language="en")
    run = container.review.develop(user_id, idea.id)
    with container.uow() as uow:
        script = next(a for a in uow.assets.for_run(run.run_id) if a.kind == "script")
    assert script.data["language"] == "en"
    client, token = logged_in(db_url, user_id)
    page = client.get(f"/assets/{script.id}").text
    assert 'action="/assets/' + str(script.id) + '/regenerate"' in page and '<option value="en" selected>' in page
    r = client.post(
        f"/assets/{script.id}/regenerate",
        data={"csrf": token, "language": "hi", "next": f"/assets/{script.id}"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    with container.uow() as uow:
        again = uow.assets.get(script.id, user_id)
    assert container.llm.calls[-1].variables["language"] == "Hindi (Devanagari script)"
    assert again.data["language"] == "hi" and again.data["script"]["sections"][0]["spoken"] == HINDI
    assert again.data["history"][0]["language"] == "en"  # the English version is kept in its history


def test_each_language_reaches_the_prompt_and_without_a_choice_the_channels_setting_is_used(
    container, user_and_channel
):
    user_id, channel = user_and_channel
    asked = {}
    for name, kwargs in {"channel": {}, "english": {"language": "en"}, "mix": {"language": "hinglish"}}.items():
        idea = container.review.add_idea(user_id, channel, f"Idea {name}", format="short", **kwargs)
        container.review.develop(user_id, idea.id)
        asked[name] = container.llm.calls[-1].variables["language"]
    assert asked == {
        "channel": "English",  # the test channel's own setting
        "english": "English",
        "mix": "Hinglish (Hindi in Latin script mixed with English)",
    }
    auto = container.review.add_idea(user_id, channel, "Auto idea", format="short")
    container.review.develop(user_id, auto.id, "hinglish")  # the Write button's language overrides
    assert "Hinglish" in container.llm.calls[-1].variables["language"]
    with container.uow() as uow:
        made = [a for a in uow.assets.for_run(container.llm.calls[-1].run_id) if a.kind == "script"]
    assert made[0].data["language"] == "hinglish"


def test_the_create_form_offers_the_languages_starting_on_the_channels_own(container, user_and_channel, db_url):
    user_id, _ = user_and_channel
    client, token = logged_in(db_url, user_id)
    form = client.get("/scripts/new?format=short").text
    for value in ("hi", "en", "hinglish"):
        assert f'name="language" value="{value}"' in form
    assert 'value="en" checked' in form and "Hindi + English mix" in form
    r = client.post(
        "/scripts/new",
        data={"csrf": token, "title": "Rent trick", "details": "", "format": "short", "language": "hi"},
        follow_redirects=False,
    )
    done = client.get(r.headers["location"], follow_redirects=False)
    page = client.get(done.headers["location"]).text
    assert ">Hindi</span>" in page  # the language chip of the script
    voices = page.split('name="voice"')[1].split("</select>")[0]
    assert "Voice: automatic (Hindi, man)" in voices and "Hindi, woman" in voices
    assert "Indian English" not in voices and "American English" not in voices  # only voices that can read it


def test_an_english_script_offers_english_voices_and_ideas_offer_a_language_when_you_write_them(
    container, user_and_channel, db_url
):
    user_id, channel = user_and_channel
    client, _ = logged_in(db_url, user_id)
    idea = container.review.add_idea(user_id, channel, "Waiting idea", format="short", language="hinglish")
    listing = client.get("/scripts?format=short").text
    assert f'action="/ideas/{idea.id}/develop"' in listing and 'name="language"' in listing
    assert 'value="hinglish" selected' in listing and ">Hinglish</span>" in listing
    run = container.review.develop(
        user_id, container.review.add_idea(user_id, channel, "English one", format="short").id
    )
    with container.uow() as uow:
        script = next(a for a in uow.assets.for_run(run.run_id) if a.kind == "script")
    page = client.get(f"/assets/{script.id}").text
    voices = page.split('name="voice"')[1].split("</select>")[0]
    assert "Voice: automatic (Indian English, man)" in voices and "Hindi, man" not in voices
