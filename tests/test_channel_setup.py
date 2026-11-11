"""The Settings AI tool: a few plain words in, About text + keywords + channel profile out. Nothing saved."""

import pytest

from tests.test_characterization import logged_in
from ytstudio.domain.errors import PermanentError
from ytstudio.domain.metadata import clean_description, format_keywords

BRIEF = "I make Python and AI project videos for beginners in Hindi. Full projects, no theory."


def test_keywords_follow_youtubes_format_and_limit():
    assert format_keywords(["Python", "#python", "ai projects", "AI Projects", 'say "hi"', "", "  "]) == (
        'Python "ai projects" "say hi"'
    )
    many = format_keywords([f"keyword{i}" for i in range(200)])
    assert len(many) <= 500 and many.startswith("keyword0 keyword1")


def test_description_is_cut_at_a_word_not_in_the_middle():
    text = ("word " * 300).strip()
    out = clean_description(text)
    assert len(out) <= 1000 and out.endswith("word") and clean_description("short") == "short"


def test_draft_applies_the_ai_answer_but_saves_nothing(container, user_and_channel):
    user_id, channel = user_and_channel
    container.sync.sync_uploads(channel)  # connecting a channel does this; the AI then sees the real uploads
    drafted_channel, drafted = container.profiles.from_brief(user_id, channel, BRIEF)
    assert drafted_channel.about.startswith("Python and AI projects")
    assert drafted_channel.keywords == 'Python "ai projects" fastapi "hindi coding"'  # deduped, quoted, no '#'
    assert (drafted.language, drafted.region, drafted.target_minutes) == ("hinglish", "IN", 12)
    assert drafted.custom_topics == ["python projects", "ai app tutorial"] and "Channel analysis" in drafted.voice_md
    with container.uow() as uow:  # the stored copies are untouched
        assert uow.channels.get(channel).about == "Money tips" and uow.schedules.get(channel).voice_md == ""
    request = container.llm.calls[-1]
    assert request.template == "channel_setup" and request.regenerate and request.variables["brief"] == BRIEF
    assert "Video own0" in request.variables["titles"]  # the channel's real uploads are given as evidence
    assert request.variables["top"][0]["title"] == "Video own0" and request.variables["top"][0]["multiple"] > 4


def test_too_short_a_description_is_refused_before_any_ai_call(container, user_and_channel):
    with pytest.raises(PermanentError, match="few words"):
        container.profiles.from_brief(*user_and_channel, "python")
    assert not container.llm.calls


def test_settings_page_fills_the_boxes_and_saves_nothing(container, user_and_channel, db_url):
    client, token = logged_in(db_url, user_and_channel[0])
    assert "Describe your channel" in client.get("/settings").text
    r = client.post("/settings/generate", data={"csrf": token, "brief": BRIEF})
    assert r.status_code == 200 and "Nothing is saved yet" in r.text
    for shown in (
        "Python and AI projects for beginners",
        "&#34;ai projects&#34;",
        "Hindi-speaking college students",
        "Short tutorials do best.",
        BRIEF,
    ):
        assert shown in r.text, shown
    with container.uow() as uow:
        assert uow.channels.get(user_and_channel[1]).about == "Money tips"  # still the old text
        assert uow.schedules.get(user_and_channel[1]).niche == ""


def test_settings_generate_needs_csrf_and_a_real_description(container, user_and_channel, db_url):
    client, token = logged_in(db_url, user_and_channel[0])
    assert client.post("/settings/generate", data={"brief": BRIEF}).status_code == 403
    r = client.post("/settings/generate", data={"csrf": token, "brief": "hi"})
    assert r.status_code == 400 and "few words" in r.text and not container.llm.calls
