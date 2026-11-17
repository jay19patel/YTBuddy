from datetime import UTC, datetime, time, timedelta

from tests.fakes import NOW, vid
from ytstudio.domain.metrics import relative_performance, retention_cliffs, top_and_bottom, velocity
from ytstudio.domain.schedule import local_day, next_quota_reset, next_run, quota_day


def test_next_run_same_day_and_next_day():
    after = datetime(2026, 11, 11, 1, 0, tzinfo=UTC)  # 06:30 in Kolkata
    assert next_run(after, "Asia/Kolkata", time(7, 0), "0123456") == datetime(2026, 11, 11, 1, 30, tzinfo=UTC)
    assert next_run(after + timedelta(hours=1), "Asia/Kolkata", time(7, 0), "0123456") == datetime(
        2026, 11, 12, 1, 30, tzinfo=UTC
    )


def test_next_run_respects_weekdays():
    wed = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)  # a Wednesday
    assert next_run(wed, "UTC", time(7, 0), "0").weekday() == 0  # next Monday
    assert next_run(wed, "UTC", time(7, 0), "") is None


def test_next_run_dst_spring_forward_gap_moves_later():
    # 2026-03-08 02:30 does not exist in New York; it runs at 03:30 EDT (07:30 UTC)
    after = datetime(2026, 3, 8, 0, 0, tzinfo=UTC)
    assert next_run(after, "America/New_York", time(2, 30), "6") == datetime(2026, 3, 8, 7, 30, tzinfo=UTC)


def test_next_run_dst_fall_back_runs_once_at_first_occurrence():
    # 2026-11-01 01:30 happens twice in New York; first one is EDT (05:30 UTC)
    after = datetime(2026, 11, 1, 0, 0, tzinfo=UTC)
    first = next_run(after, "America/New_York", time(1, 30), "6")
    assert first == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert next_run(first, "America/New_York", time(1, 30), "6") == datetime(2026, 11, 8, 6, 30, tzinfo=UTC)


def test_quota_day_and_reset_use_pacific_time():
    t = datetime(2026, 11, 11, 7, 0, tzinfo=UTC)  # 23:00 Pacific on the 10th
    assert str(quota_day(t)) == "2026-11-10"
    assert next_quota_reset(t) == datetime(2026, 11, 11, 8, 0, tzinfo=UTC)
    assert str(local_day(t, "Asia/Kolkata")) == "2026-11-11"


def test_relative_performance_and_top_bottom():
    vids = [vid(str(i), "c", v) for i, v in enumerate([100, 200, 300, 400, 5000])]
    ranked = relative_performance(vids)
    assert ranked[0][0].id == "4" and ranked[0][1] == round(5000 / 300, 2)
    top, bottom = top_and_bottom(vids, 2)
    assert [v.id for v, _ in top] == ["4", "3"] and [v.id for v, _ in bottom] == ["0", "1"]
    assert top_and_bottom([], 3) == ([], [])
    assert relative_performance([vid("x", "c", 0)]) == [(vid("x", "c", 0), 0.0)]


def test_velocity_and_cliffs():
    assert velocity(1000, NOW - timedelta(days=4), NOW) == 250
    cliffs = retention_cliffs([(0, 1.0), (0.1, 0.6), (0.2, 0.55), (0.3, 0.3)])
    assert cliffs[0] == {"at_ratio": 0, "drop": 0.4}  # where the steepest drop starts
    assert len(cliffs) == 3


def test_events_have_titles_bodies_and_links():
    from ytstudio.domain import events as ev

    for e in (
        ev.BriefReady(1, "Ch", 3),
        ev.ShortsReady(1, "Ch", 2),
        ev.JobFailed(1, 3, "step", "x" * 900),
        ev.ChannelNeedsReconnect(1, "Ch"),
        ev.BudgetReached(1, 2.5),
        ev.BeatStale(1, 15),
    ):
        assert e.title and e.link.startswith("/") and isinstance(e.body, str)
    assert len(ev.JobFailed(1, 3, "s", "x" * 900).body) == 500
    assert ev.Event(1).title == "Event" and ev.Event(1).body == "" and ev.Event(1).link == "/"


def test_upload_description_and_video_tags():
    from ytstudio.domain.metadata import clean_video_text, hashtag_list, upload_description, video_tags

    d = upload_description("Save rent.", ["rent tips", "#Rent", "Shorts"], "My Channel", made_here=True)
    assert d.startswith("Save rent.\n\n#renttips #Rent #Shorts\n\n© My Channel.")
    assert "AI-generated voice" in d and "Fluent Emoji" in d and d.endswith("acting on it.")
    own = upload_description("Mine.\n\n#Rent", ["#rent", "#money"], "X", made_here=False)
    assert own.startswith("Mine.\n\n#Rent\n\n#money\n\n© X.") and "AI-generated" not in own
    assert upload_description(d, ["#more"], "My Channel", True).count("©") == 1  # pasted back: no second footer
    assert hashtag_list("rent, #money  tips") == ["#rent", "#money", "#tips"]
    assert clean_video_text("a <b> c", 100) == "a b c"
    assert video_tags(["#rent", "Rent", "save money", "x" * 600]) == ["rent", "save money"]
    assert sum(len(t) + 3 for t in video_tags([f"tag number {i}" for i in range(100)])) <= 500


def test_english_words_are_said_the_same_way_every_time_by_the_hindi_voice():
    from ytstudio.domain.speech import pronounce

    assert pronounce("Maine Python aur AI se banaya.") == "Maine पायथन aur ए आई se banaya."
    assert pronounce("Ye version 1 hai, v2.0 aayega.") == "Ye वर्ज़न वन hai, वी टू पॉइंट ज़ीरो aayega."
    assert pronounce("Step 3: APIs aur SEO.") == "स्टेप थ्री: ए पी आईज़ aur एस ई ओ."
    assert pronounce("step 21 mein 90% log") == "स्टेप ट्वेंटी वन mein 90% log"  # other numbers: the Devanagari copy
    assert pronounce("किराया AI से कम करो।") == "किराया ए आई से कम करो।"


def test_long_videos_are_2_to_3_minutes():
    from ytstudio.domain.video import long_minutes, long_words

    assert [long_minutes(m) for m in (1, 2, 3, 8)] == [2, 2, 3, 3]
    assert long_words(8) == (350, 420) and long_words(2) == (210, 280)
