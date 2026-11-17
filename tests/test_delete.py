"""Deleting ideas (and what was written for them), scripts, packages and SEO."""

import pytest

from tests.test_characterization import logged_in
from ytstudio.domain.errors import NotFound, PermanentError


def daily(container, user_and_channel):
    user_id, channel = user_and_channel
    run = container.scheduling.run_now(user_id, channel)
    with container.uow() as uow:
        # ideas by rank (1-2 long, 3-7 Shorts); a script, package or SEO by the idea it belongs to
        return {
            (a.kind, a.data.get("rank") if a.parent_id is None or a.kind == "idea" else a.parent_id): a
            for a in uow.assets.for_run(run.run_id)
        }, user_id


def exists(container, user_id, asset_id):
    with container.uow() as uow:
        try:
            uow.assets.get(asset_id, user_id)
        except NotFound:
            return False
    return True


def test_deleting_an_idea_removes_its_script_package_and_seo_only(container, user_and_channel, db_url):
    made, user_id = daily(container, user_and_channel)
    idea_one, idea_two = made[("idea", 1)], made[("idea", 2)]  # the daily script/package/seo hang under idea 1
    client, token = logged_in(db_url, user_id)
    r = client.post(f"/assets/{idea_one.id}/delete", data={"csrf": token, "next": "/scripts"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/scripts")
    assert not exists(container, user_id, idea_one.id)
    for kind in ("script", "package", "seo"):
        assert not exists(container, user_id, made[(kind, idea_one.id)].id), kind
    assert exists(container, user_id, idea_two.id) and exists(container, user_id, made[("brief", None)].id)
    page = client.get("/scripts").text + client.get("/scripts?format=short").text
    assert (
        f'href="/assets/{made[("script", idea_one.id)].id}"' not in page and f'href="/assets/{idea_one.id}"' not in page
    )
    assert client.get("/today").status_code == 200  # the brief page copes with its script being gone


def test_deleting_a_script_keeps_the_idea(container, user_and_channel, db_url):
    made, user_id = daily(container, user_and_channel)
    client, token = logged_in(db_url, user_id)
    script = made[("script", made[("idea", 1)].id)]
    client.post(f"/assets/{script.id}/delete", data={"csrf": token, "next": "/scripts"})
    assert not exists(container, user_id, script.id) and exists(container, user_id, made[("idea", 1)].id)
    assert f'href="/assets/{script.id}"' not in client.get("/scripts").text


def test_deleting_from_the_asset_page_lands_on_the_list_not_a_404(container, user_and_channel, db_url):
    made, user_id = daily(container, user_and_channel)
    idea = made[("idea", 3)]
    client, token = logged_in(db_url, user_id)
    r = client.post(
        f"/assets/{idea.id}/delete", data={"csrf": token, "next": f"/assets/{idea.id}"}, follow_redirects=False
    )
    assert r.headers["location"].startswith("/scripts")


def test_the_button_warns_about_what_goes_with_the_idea(container, user_and_channel, db_url):
    user_id, channel = user_and_channel
    idea = container.review.add_idea(user_id, channel, "Waiting for a script", format="long")
    client, _ = logged_in(db_url, user_id)
    page = client.get("/scripts").text  # an idea without a script yet is on the Scripts page
    assert (
        "Delete this idea and the script, package and SEO written for it for good?" in page
        and ">Delete</button>" in page
    )
    container.review.set_status(user_id, idea.id, "approved")  # type: ignore[arg-type]
    assert "It is approved. Delete this idea" in client.get("/scripts").text


def test_only_the_owner_can_delete_and_a_brief_cannot_be_deleted(container, user_and_channel, db_url):
    made, user_id = daily(container, user_and_channel)
    other = container.accounts.owner("other@example.com")
    stranger, stranger_token = logged_in(db_url, other.id)
    assert stranger.post(f"/assets/{made[('idea', 1)].id}/delete", data={"csrf": stranger_token}).status_code == 404
    with pytest.raises(PermanentError, match="cannot be deleted"):
        container.review.delete(user_id, made[("brief", None)].id)
    client, _ = logged_in(db_url, user_id)
    assert client.post(f"/assets/{made[('idea', 1)].id}/delete", data={}).status_code == 403  # no csrf token
    assert exists(container, user_id, made[("idea", 1)].id)


def test_deleting_a_short_keeps_the_shorts_tab_open(container, user_and_channel, db_url):
    made, user_id = daily(container, user_and_channel)
    short = made[("idea", 3)]  # ranks 3-7 are Shorts
    client, token = logged_in(db_url, user_id)
    r = client.post(
        f"/assets/{short.id}/delete", data={"csrf": token, "next": "/scripts?format=short"}, follow_redirects=False
    )
    assert r.headers["location"] == "/scripts?format=short&m=deleted"  # not "...short?m=deleted": that is the long tab
    short = made[("idea", 4)]
    r = client.post(f"/assets/{short.id}/delete", data={"csrf": token, "next": f"/assets/{short.id}"})
    assert r.url.path == "/scripts" and r.url.params["format"] == "short"  # from its own page: back to its tab
