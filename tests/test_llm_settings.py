"""Your AI models and keys, chosen on the LLM page: per account, keys encrypted, used by the adapter."""

import pytest
from alembic.script import ScriptDirectory
from pydantic import BaseModel
from sqlmodel import select

from tests.fakes import DictCache, NoLimit
from tests.test_characterization import logged_in
from ytstudio.adapters.llm import LiteLLMAdapter, build_router
from ytstudio.adapters.persistence import tables as t
from ytstudio.adapters.persistence.db import session_for
from ytstudio.cli import alembic_config
from ytstudio.domain.errors import PermanentError
from ytstudio.domain.llm import EffectiveLLM, clean_key, clean_models, merge
from ytstudio.ports import LLMRequest, PromptTemplate

SECRET = "AIzaSyD-test-secret-1234"


def test_there_is_one_migration_head():
    heads = ScriptDirectory.from_config(alembic_config("sqlite://")).get_heads()
    assert len(heads) == 1, f"two sessions added a migration from the same parent: {heads}"


def test_your_setting_beats_the_default():
    defaults = {"fast": ["gemini/flash"], "smart": ["gemini/pro"], "long": ["gemini/pro"]}
    eff = merge(defaults, {"fast": ["openai/mini"]}, {"gemini": "uk", "nonsense": "x", "mistral": ""})
    assert eff.aliases == {"fast": ["openai/mini"], "smart": ["gemini/pro"], "long": ["gemini/pro"]}
    assert eff.keys == {"gemini": "uk"}  # unknown providers and empty keys are ignored
    assert (eff.sources["alias:fast"], eff.sources["alias:smart"]) == ("you", "default")


def test_model_and_key_validation():
    typed = [" gemini/gemini-flash-latest ", "", "gemini/gemini-flash-latest", "mistral/small", "x/y", "a/b"]
    assert clean_models(typed) == ["gemini/gemini-flash-latest", "mistral/small", "x/y"]  # no blanks, repeats, or 4th
    for bad in ("flash", "Gemini Flash", "gemini/"):
        with pytest.raises(PermanentError, match="not a model name"):
            clean_models([bad])
    for bad in ("short", "has a space inside"):
        with pytest.raises(PermanentError, match="API key"):
            clean_key(bad)


def test_keys_are_stored_encrypted_and_read_back(container, db_url):
    store = container.llm_settings.store
    store.set_key("user:1", "gemini", SECRET)
    with session_for(db_url) as s:
        row = s.exec(select(t.LLMKeyRow)).one()
    assert SECRET not in row.secret and row.secret.startswith("v1:")
    assert store.keys("user:1") == {"gemini": SECRET} and store.keys("user:2") == {}
    store.delete_key("user:1", "gemini")
    assert store.keys("user:1") == {}


def test_each_account_has_its_own_settings_and_remove_goes_back_to_the_default(container, user_and_channel):
    me = container.accounts.owner("me@example.com")
    other = container.accounts.owner("other@example.com")
    s = container.llm_settings
    default_smart = s.effective(me.id).aliases["smart"]
    s.save(me, {"gemini": SECRET}, set(), {"smart": ["mistral/mistral-large-latest"]})
    assert s.effective(me.id).keys == {"gemini": SECRET}
    assert s.effective(me.id).aliases["smart"] == ["mistral/mistral-large-latest"]
    assert s.effective(other.id).keys == {} and s.effective(other.id).aliases["smart"] == default_smart  # not shared
    s.save(me, {}, {"gemini"}, {"smart": []})  # remove the key and clear the models
    assert s.effective(me.id).keys == {} and s.effective(me.id).aliases["smart"] == default_smart


def test_a_bad_entry_saves_nothing(container, user_and_channel):
    me = container.accounts.owner("me@example.com")
    with pytest.raises(PermanentError):
        container.llm_settings.save(me, {"gemini": SECRET}, set(), {"fast": ["not a model"]})
    assert container.llm_settings.store.keys(f"user:{me.id}") == {}  # the valid key was not saved either


class Prompts:
    def render(self, name, variables):
        return PromptTemplate(name, "1", "s", "u"), "system", "user"


def test_each_user_gets_their_own_models_and_keys(container, user_and_channel):
    me = container.accounts.owner("me@example.com")
    other = container.accounts.owner("other@example.com")
    s = container.llm_settings
    s.save(me, {"gemini": "my-key-1"}, set(), {"fast": ["gemini/model-mine"]})
    s.save(other, {"gemini": "other-key-2"}, set(), {"fast": ["gemini/model-other"]})
    built: list[EffectiveLLM] = []

    def factory(eff):
        built.append(eff)
        router = build_router({"aliases": {}, "defaults": {"num_retries": 0}}, eff=eff)
        for dep in router.model_list:  # no network: LiteLLM answers with a canned reply for each deployment
            dep["litellm_params"]["mock_response"] = '{"ok": true}'
        return router

    cfg = {"defaults": {}, "rate_limits": {"gemini": {"rate": 100, "burst": 100}}, "prices": {}}
    llm = LiteLLMAdapter(
        s.effective,
        factory,
        cfg,
        Prompts(),
        container.uow,
        DictCache(),
        NoLimit(),
        lambda e: None,
        60,
        claim_once=lambda k: True,
    )

    class Out(BaseModel):
        ok: bool

    for user in (me, other):
        llm.generate(LLMRequest(alias="fast", template="t", variables={}, user_id=user.id, step="s"), Out)
    with session_for(container.s.database_url) as db:
        used = {c.user_id: c.model for c in db.exec(select(t.LLMCall))}
    assert used == {me.id: "model-mine", other.id: "model-other"}  # LiteLLM reports the name without provider
    assert [e.keys["gemini"] for e in built] == ["my-key-1", "other-key-2"]  # a Router per configuration
    llm.generate(LLMRequest(alias="fast", template="t", variables={}, user_id=me.id, step="s", regenerate=True), Out)
    assert len(built) == 2  # unchanged settings reuse the Router


def test_the_page_saves_without_ever_showing_the_key(container, user_and_channel, db_url):
    client, token = logged_in(db_url, user_and_channel[0])
    form = {"csrf": token, "key_gemini": SECRET, "smart_1": "gemini/gemini-pro-latest", "smart_2": "mistral/m"}
    r = client.post("/llm/save", data=form, follow_redirects=False)
    assert r.status_code == 303 and SECRET not in r.text
    page = client.get("/llm").text
    assert page.count('action="/llm/save"') == 1  # one box, not two
    assert "Global" not in page and "Your AI models" not in page
    assert SECRET not in page and "saved, ends …1234" in page and "gemini/gemini-pro-latest" in page
    with session_for(db_url) as s:
        assert SECRET not in s.exec(select(t.LLMKeyRow)).one().secret
    bad = client.post("/llm/save", data={"csrf": token, "fast_1": "nonsense"})
    assert bad.status_code == 400 and "not a model name" in bad.text


def test_another_account_sees_and_changes_only_its_own(container, user_and_channel, db_url):
    mine, my_token = logged_in(db_url, user_and_channel[0])
    mine.post("/llm/save", data={"csrf": my_token, "key_gemini": SECRET})
    other = container.accounts.owner("other@example.com")
    theirs, their_token = logged_in(db_url, other.id)
    assert "saved, ends" not in theirs.get("/llm").text  # my saved key is not theirs
    assert theirs.post("/llm/save", data={"csrf": their_token, "key_mistral": "their-own-key-5678"}).status_code in (
        200,
        303,
    )
    assert container.llm_settings.store.keys(f"user:{other.id}") == {"mistral": "their-own-key-5678"}
    assert theirs.post("/llm/global", data={"csrf": their_token}).status_code in (404, 405)  # no global box any more
    assert theirs.post("/llm/save", data={"key_gemini": SECRET}).status_code == 403  # no csrf token


def test_test_button_and_model_list_refresh(container, user_and_channel, db_url):
    client, token = logged_in(db_url, user_and_channel[0])
    r = client.post("/llm/test", data={"csrf": token, "alias": "smart"})
    assert r.status_code == 200 and "works: fake/smart answered" in r.text
    container.llm_settings.lister = lambda provider, key: [f"{provider}/live-model-1"]
    client.post("/llm/save", data={"csrf": token, "key_gemini": SECRET})
    r = client.post("/llm/models", data={"csrf": token})
    assert "gemini: 1 models" in r.text and 'value="gemini/live-model-1"' in r.text
    assert SECRET not in r.text and not any(SECRET in k for k in container.cache.d)  # the key is never a cache key


def test_the_llm_link_sits_under_sign_out_and_the_page_has_no_channel_tabs(container, user_and_channel, db_url):
    client, _ = logged_in(db_url, user_and_channel[0])
    today = client.get("/today").text
    assert today.index("Sign out") < today.index('href="/llm"') < today.index("</nav>")  # in the sidebar footer
    page = client.get("/llm").text
    assert 'class="tabs"' not in page and "Channel:" not in page and "/settings/llm" not in page
    settings_page = client.get("/settings").text
    assert 'open <a href="/llm">LLM</a>' in settings_page and "AI models and keys</h1>" not in settings_page


def test_the_llm_page_works_before_any_channel_is_connected(container, db_url):
    owner = container.accounts.owner("me@example.com")
    client, _ = logged_in(db_url, owner.id)
    assert client.get("/llm").status_code == 200  # you can set your keys before connecting a channel


def test_an_openai_compatible_provider_is_reached_through_its_own_address():
    from ytstudio.domain.llm import litellm_params

    model = "bazaarlink/qwen/qwen3.7-flash:free"
    assert clean_models([model]) == [model]  # slashes, dots and a ":free" suffix are fine
    assert litellm_params(model) == {
        "model": "openai/qwen/qwen3.7-flash:free",
        "api_base": "https://api.bazaarlink.ai/v1",
    }
    assert litellm_params("gemini/gemini-flash-latest") == {"model": "gemini/gemini-flash-latest"}
    eff = EffectiveLLM(aliases={"smart": [model]}, keys={"bazaarlink": "sk-bl-test-key"})
    dep = build_router({"aliases": {}, "defaults": {}}, eff=eff).model_list[0]["litellm_params"]
    assert (dep["model"], dep["api_base"], dep["api_key"]) == (
        "openai/qwen/qwen3.7-flash:free",
        "https://api.bazaarlink.ai/v1",
        "sk-bl-test-key",
    )


def test_model_list_of_an_openai_compatible_provider(monkeypatch):
    import httpx

    from ytstudio.adapters.llm_models import list_models

    seen = {}

    def fake_get(url, headers, params, timeout):
        seen.update(url=url, auth=headers["Authorization"])
        return httpx.Response(200, json={"data": [{"id": "qwen/qwen3.7-flash:free"}, {"id": "a/b"}]})

    monkeypatch.setattr(httpx, "get", fake_get)
    assert list_models("bazaarlink", "sk-bl-test") == ["bazaarlink/a/b", "bazaarlink/qwen/qwen3.7-flash:free"]
    assert seen == {"url": "https://api.bazaarlink.ai/v1/models", "auth": "Bearer sk-bl-test"}
