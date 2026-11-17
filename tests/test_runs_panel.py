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


def test_idea_page_says_what_to_do_next_and_where_the_script_is(container, user_and_channel, db_url):
    user_id, channel = user_and_channel
    idea = container.review.add_idea(user_id, channel, "A saved idea", "", format="long")  # saved, not developed
    client, token = logged_in(db_url, user_id)
    before = client.get(f"/assets/{idea.id}").text
    assert "Next step: write the script" in before and "Script ready" not in before
    r = client.post(f"/ideas/{idea.id}/develop", data={"csrf": token}, follow_redirects=False)
    assert r.status_code == 303 and "m=started" in r.headers["location"]
    with container.uow() as uow:
        script = next(a for a in uow.assets.for_run(int(r.headers["location"].split("run=")[1])) if a.kind == "script")
    after = client.get(f"/assets/{idea.id}").text
    assert "Script ready" in after and f'href="/assets/{script.id}#video"' in after and "make the video" in after
    assert "Next step: write the script" not in after
    again = client.post(f"/ideas/{idea.id}/develop", data={"csrf": token}, follow_redirects=False)
    assert "m=done_already" in again.headers["location"]  # not "already ran today"
    assert "already done" in client.get(again.headers["location"]).text


def test_channel_tabs_reach_today_and_scripts(container, user_and_channel, db_url):
    client, _ = logged_in(db_url, user_and_channel[0])
    tabs = client.get("/insights").text.split('class="tabs"')[1].split("</nav>")[0]
    assert 'href="/today"' in tabs and 'href="/scripts"' in tabs
