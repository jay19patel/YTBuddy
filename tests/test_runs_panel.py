"""The run panel on a page survives a refresh: it no longer depends on the ?run= link."""

from tests.test_characterization import logged_in
from ytstudio.domain.models import RunStatus


def idea_and_run(container, user_and_channel):
    user_id, channel = user_and_channel
    daily = container.scheduling.run_now(user_id, channel)
    with container.uow() as uow:
        idea = next(a for a in uow.assets.for_run(daily.run_id) if a.kind == "idea" and a.data["rank"] == 2)
    return user_id, idea, container.review.develop(user_id, idea.id)


def test_finished_run_is_a_quiet_one_liner_after_a_refresh(container, user_and_channel, db_url):
    user_id, idea, dev = idea_and_run(container, user_and_channel)
    client, _ = logged_in(db_url, user_id)
    page = client.get(f"/assets/{idea.id}").text  # no ?run=, like pressing refresh
    assert f"Last run #{dev.run_id} (develop): done" in page
    assert "data-run=" not in page  # a finished run is not polled, so the page cannot reload forever


def test_failed_run_shows_the_error_and_a_retry_button(container, user_and_channel, db_url):
    user_id, idea, dev = idea_and_run(container, user_and_channel)
    with container.uow() as uow:
        uow.runs.set_status(dev.run_id, RunStatus.FAILED, "write_script: boom")
        uow.commit()
    client, _ = logged_in(db_url, user_id)
    page = client.get(f"/assets/{idea.id}").text
    assert "write_script: boom" in page and "Retry from the failed step" in page and "data-run=" not in page


def test_running_run_is_polled_and_the_today_page_shows_it_too(container, user_and_channel, db_url):
    user_id, idea, dev = idea_and_run(container, user_and_channel)
    with container.uow() as uow:
        uow.runs.set_status(dev.run_id, RunStatus.RUNNING)
        uow.commit()
    client, _ = logged_in(db_url, user_id)
    assert f'data-run="{dev.run_id}"' in client.get(f"/assets/{idea.id}").text
    assert f'data-run="{dev.run_id}"' in client.get("/today").text
