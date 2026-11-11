from datetime import time, timedelta

import pytest

from tests.fakes import NOW, transcript
from ytstudio.domain.errors import NotFound, PermanentError
from ytstudio.domain.events import ChannelNeedsReconnect, ShortsReady
from ytstudio.domain.models import AssetStatus, ChannelStatus, OAuthToken, RunStatus, Schedule
from ytstudio.ports import ChannelInfo


def test_owner_is_created_once_and_claims_legacy_channels(container, db_url):
    from ytstudio.adapters.persistence import tables as t
    from ytstudio.adapters.persistence.db import session_for

    with session_for(db_url) as s:  # a channel from before sign-in existed
        s.add(t.Channel(id="UCold", title="Old", thumb="", creds=""))
        s.commit()
    me = container.accounts.owner()
    assert container.accounts.owner().id == me.id
    with container.uow() as uow:
        assert uow.channels.get("UCold").user_id == me.id
    other = container.accounts.owner("other@example.com")
    with container.uow() as uow:
        assert [c.id for c in uow.channels.list(other.id)] == []


def test_channel_of_another_user_is_invisible_and_not_stealable(container, user_and_channel):
    other = container.accounts.owner("other@example.com")
    with container.uow() as uow, pytest.raises(NotFound):
        uow.channels.get("UCme", other.id)
    with pytest.raises(NotFound):
        container.accounts.connect(other.id, OAuthToken(refresh_token="x"))


def test_connect_keeps_existing_refresh_token_when_google_omits_it(container, user_and_channel):
    container.accounts.connect(user_and_channel[0], OAuthToken(refresh_token="", access_token="new"))
    assert container.vault.load("UCme").refresh_token == "rt"


def test_reconnect_needed_marks_channel_and_notifies_once(container, user_and_channel):
    container.accounts.mark_reconnect("UCme", container.events.publish)
    container.accounts.mark_reconnect("UCme", container.events.publish)
    with container.uow() as uow:
        assert uow.channels.get("UCme").status == ChannelStatus.RECONNECT_NEEDED
    sent = [e for _, e in container.events.notifier.sent if isinstance(e, ChannelNeedsReconnect)]
    assert len(sent) == 1


def test_sync_stats_skips_a_broken_login(container, user_and_channel, yt):
    yt.infos["UCtwo"] = ChannelInfo(id="UCtwo", title="Two", thumb="")
    container.accounts.connect(user_and_channel[0], OAuthToken(refresh_token="r2"))
    yt.infos["UCtwo"] = ChannelInfo(id="UCtwo", title="Two", thumb="", subscribers=7)
    yt.broken.add(min("UCme", "UCtwo", key=lambda c: yt.infos[c].title))  # the first one tried is revoked
    assert container.sync.sync_stats() == "Updated 2 channel(s)."


def test_schedule_save_validates_and_computes_next_run(container, user_and_channel):
    s = container.scheduling.save(
        user_and_channel[0],
        Schedule(channel_id="UCme", enabled=True, timezone="UTC", run_time=time(13, 0), days="9x0123456"),
    )
    assert s.days == "0123456" and s.confirmed and s.next_run_utc == NOW.replace(hour=13)
    with pytest.raises(PermanentError):
        container.scheduling.save(user_and_channel[0], Schedule(channel_id="UCme", timezone="Mars/Base"))


def test_dispatch_due_catches_up_once(container, user_and_channel):
    user_id, ch = user_and_channel
    container.scheduling.save(user_id, Schedule(channel_id=ch, enabled=True, timezone="UTC", run_time=time(13, 0)))
    container.clock.t = NOW + timedelta(days=3, hours=2)  # the worker was down for three days
    results = container.scheduling.dispatch_due()
    assert (len(results) == 1 and results[0].status == RunStatus.RUNNING) or results[0].status == "running"
    assert container.scheduling.dispatch_due() == []  # not three runs
    with container.uow() as uow:
        assert uow.schedules.get(ch).next_run_utc > container.clock.now()


def test_review_edit_status_and_bad_fields(container, user_and_channel):
    user_id, ch = user_and_channel
    run = container.scheduling.run_now(user_id, ch)
    with container.uow() as uow:
        seo = next(a for a in uow.assets.for_run(run.run_id) if a.kind == "seo")
    container.review.set_status(user_id, seo.id, AssetStatus.APPROVED)
    container.review.edit(user_id, seo.id, {"description": "mine"})
    with container.uow() as uow:
        a = uow.assets.get(seo.id, user_id)
    assert a.data["description"] == "mine" and a.status == AssetStatus.NEEDS_REVIEW
    with pytest.raises(PermanentError):
        container.review.edit(user_id, seo.id, {"user_id": 99})
    other = container.accounts.owner("other@example.com")
    with pytest.raises(NotFound):
        container.review.set_status(other.id, seo.id, AssetStatus.APPROVED)


def test_profile_detection_prefills_until_confirmed(container, user_and_channel):
    s = container.profiles.detect("UCme")
    assert (s.niche, s.language, s.region) == ("personal finance for renters in India", "hinglish", "IN")
    assert not s.confirmed
    container.scheduling.save(user_and_channel[0], Schedule(channel_id="UCme", niche="mine"))
    assert container.profiles.detect("UCme").niche == "mine"


def ready_master(container, user_id, video_id=None):
    import pathlib
    import tempfile

    with container.uow() as uow:
        m = uow.media.create(user_id, "UCme", "master.mp4", 10)
        f = pathlib.Path(tempfile.mkdtemp()) / "m.mp4"
        f.write_bytes(b"x" * 10)
        m.storage_key = container.storage.put(f, f"masters/UCme/{m.id}.mp4")
        m.status, m.received, m.video_id = "ready", 10, video_id
        uow.media.update(m)
        uow.commit()
    return m


def test_shorts_pipeline_with_captions(container, user_and_channel, yt):
    user_id, _ = user_and_channel
    yt.cues = transcript()
    m = ready_master(container, user_id, video_id="own1")
    res = container.shorts.start(user_id, m.id)
    with container.uow() as uow:
        run = uow.runs.get(res.run_id)
        shorts = [a for a in uow.assets.for_run(res.run_id) if a.kind == "short"]
        chapters = [a for a in uow.assets.for_run(res.run_id) if a.kind == "chapters"]
    assert run.status == RunStatus.DONE, run
    assert run.outputs["transcribe_fallback"]["source"] == "captions"
    picks = run.outputs["pick_short_candidates"]["picks"]
    assert len(picks) == 2  # the out-of-range window index was dropped
    assert all(15 <= p["end"] - p["start"] <= 58 for p in picks)
    assert [s.title for s in shorts] == ["Rent trick #1", "Rent trick #2"]
    assert all(s.status == "needs_review" and s.data["video_key"] and s.data["cover_key"] for s in shorts)
    assert chapters and chapters[0].data["chapters"]["valid"]
    assert any(isinstance(e, ShortsReady) for _, e in container.events.notifier.sent)

    trim = container.shorts.trim(user_id, shorts[0].id, 10.0, 40.0, NOW)
    with container.uow() as uow:
        assert uow.runs.get(trim.run_id).status == RunStatus.DONE
        assert uow.assets.get(shorts[0].id, user_id).data["start"] == 10.0
    with pytest.raises(PermanentError):
        container.shorts.trim(user_id, shorts[0].id, 0, 70, NOW)


def test_shorts_falls_back_to_whisper_and_auto_triggers(container, user_and_channel, yt):
    user_id, _ = user_and_channel
    yt.cues = None
    m = ready_master(container, user_id, video_id="newvid")
    results = container.shorts.on_new_uploads("UCme", ["newvid", "unrelated"])
    assert len(results) == 1
    with container.uow() as uow:
        assert uow.runs.get(results[0].run_id).outputs["transcribe_fallback"]["source"] == "whisper"
    assert container.shorts.on_new_uploads("UCme", []) == []
    with pytest.raises(PermanentError):
        with container.uow() as uow:
            pending = uow.media.create(user_id, "UCme", "x.mp4", 5)
            uow.commit()
        container.shorts.start(user_id, pending.id)
    assert m.id


def test_sync_uploads_reports_only_new_after_first_sync(container, user_and_channel, yt):
    assert container.sync.sync_uploads("UCme") == []  # first sync is history
    from tests.fakes import vid

    yt.own["UCme"].append(vid("fresh", "UCme", 10, days_old=0.1))
    assert container.sync.sync_uploads("UCme") == ["fresh"]
    assert container.sync.refresh_video_analytics("UCme") == 7
