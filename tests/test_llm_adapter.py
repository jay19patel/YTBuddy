"""LiteLLM adapter: fallback, repair, cache, budget, cost records. No network: LiteLLM's mock_response."""

import json
from typing import ClassVar

import pytest
from pydantic import BaseModel

from tests.fakes import DictCache, NoLimit
from ytstudio.adapters.llm import LiteLLMAdapter, build_router, extract_json
from ytstudio.domain.errors import BudgetExceeded, LLMOutputInvalid, TransientError
from ytstudio.domain.events import BudgetReached
from ytstudio.domain.llm import EffectiveLLM
from ytstudio.ports import LLMRequest, PromptTemplate


class Out(BaseModel):
    VERSION: ClassVar[str] = "1"
    answer: str


class Prompts:
    def get(self, name):
        return PromptTemplate(name, "1.abc+v", "sys", "user")

    def render(self, name, variables):
        return self.get(name), "system text", f"user {variables.get('q', '')}"


def cfg(primary, fallback=None):
    models = ["gemini/gemini-flash-latest"] + (["mistral/mistral-small-latest"] if fallback is not None else [])
    params = {
        "aliases": {"fast": {"models": models}},
        "defaults": {"num_retries": 0, "timeout_s": 5},
        "prices": {
            "gemini/gemini-flash-latest": {"input": 0.1, "output": 0.5},
            "mistral/mistral-small-latest": {"input": 2.0, "output": 10.0},
        },
        "rate_limits": {"gemini": {"rate": 100, "burst": 100}},
    }
    router = build_router(params)
    # inject mock responses per deployment
    for dep in router.model_list:
        dep["litellm_params"]["mock_response"] = primary if dep["model_name"] == "fast" else fallback
    return params, router


@pytest.fixture
def adapter_factory(container, user_and_channel):
    published = []

    def make(primary, fallback=None, cache=None, keys=None):
        params, router = cfg(primary, fallback)
        saved = (
            {"gemini": "AIza-test-key", "mistral": "test-key"} if keys is None else keys
        )  # keys saved on the LLM page
        eff = EffectiveLLM(aliases={"fast": list(params["aliases"]["fast"]["models"])}, keys=saved)
        return LiteLLMAdapter(
            lambda user_id: eff,
            lambda _eff: router,
            params,
            Prompts(),
            container.uow,
            cache or DictCache(),
            NoLimit(),
            published.append,
            3600,
            claim_once=lambda k: True,
        ), published

    return make


def req(user_id, critical=True, regenerate=False, q="hi"):
    return LLMRequest(
        alias="fast",
        template="t",
        variables={"q": q},
        user_id=user_id,
        step="s",
        critical=critical,
        regenerate=regenerate,
    )


def calls(container):
    from sqlmodel import select

    from ytstudio.adapters.persistence import tables as t
    from ytstudio.adapters.persistence.db import session_for

    with session_for(container.s.database_url) as s:
        return list(s.exec(select(t.LLMCall)))


def test_valid_json_is_parsed_cached_and_costed(adapter_factory, container, user_and_channel):
    llm, _ = adapter_factory('```json\n{"answer": "42"}\n```')
    assert llm.generate(req(user_and_channel[0]), Out).answer == "42"
    assert llm.generate(req(user_and_channel[0]), Out).answer == "42"  # cache hit
    rows = calls(container)
    assert [r.cache_hit for r in rows] == [False, True]
    assert rows[0].template_version == "1.abc+v" and rows[0].alias == "fast" and rows[0].cost_usd > 0
    assert rows[1].cost_usd == 0


def test_regenerate_bypasses_cache(adapter_factory, container, user_and_channel):
    llm, _ = adapter_factory('{"answer": "a"}')
    llm.generate(req(user_and_channel[0]), Out)
    llm.generate(req(user_and_channel[0], regenerate=True), Out)
    assert [r.cache_hit for r in calls(container)] == [False, False]


def test_fallback_model_answers_when_primary_fails(adapter_factory, user_and_channel, container):
    llm, _ = adapter_factory(Exception("primary down"), '{"answer": "from fallback"}')
    assert llm.generate(req(user_and_channel[0]), Out).answer == "from fallback"
    assert "mistral" in calls(container)[-1].model


def test_router_giving_up_is_one_transient_error(adapter_factory, user_and_channel):
    llm, _ = adapter_factory(Exception("down"))
    with pytest.raises(TransientError):
        llm.generate(req(user_and_channel[0]), Out)


def test_one_repair_then_fail(adapter_factory, user_and_channel, container):
    llm, _ = adapter_factory("not json at all")
    with pytest.raises(LLMOutputInvalid):
        llm.generate(req(user_and_channel[0]), Out)
    assert [r.step for r in calls(container)] == ["s", "s:repair"]


def test_repair_succeeds(adapter_factory, user_and_channel):
    llm, _ = adapter_factory('{"wrong": 1}')
    replies = iter(['{"wrong": 1}', '{"answer": "fixed"}'])
    router = llm._router(llm.resolver(user_and_channel[0]))
    real = router.completion

    def completion(**kw):
        for dep in router.model_list:
            dep["litellm_params"]["mock_response"] = next(replies)
        return real(**kw)

    router.completion = completion
    assert llm.generate(req(user_and_channel[0]), Out).answer == "fixed"


def test_budget_blocks_non_critical_and_notifies(adapter_factory, user_and_channel, container):
    llm, published = adapter_factory('{"answer": "x"}')
    llm.generate(req(user_and_channel[0], critical=False, q="no limit by default"), Out)  # budget 0 = no limit
    with container.uow() as uow:
        u = uow.users.get(user_and_channel[0])
        u.daily_budget_usd = 1e-9  # spent today is already above this
        uow.users.update(u)
        uow.commit()
    with pytest.raises(BudgetExceeded):
        llm.generate(req(user_and_channel[0], critical=False), Out)
    assert isinstance(published[0], BudgetReached)
    assert llm.generate(req(user_and_channel[0], critical=True), Out).answer == "x"  # critical steps still run


def test_extract_json():
    assert json.loads(extract_json('Sure!\n```json\n{"a": {"b": 1}}\n```')) == {"a": {"b": 1}}
    assert extract_json("nothing") == "nothing"


def test_shipped_config_uses_aliases_only():
    from ytstudio.adapters.llm import load_llm_config
    from ytstudio.config import ROOT

    c = load_llm_config(ROOT / "config" / "llm.toml")
    assert set(c["aliases"]) == {"fast", "smart", "long"}
    assert all(len(a["models"]) >= 2 for a in c["aliases"].values())


def test_shipped_config_is_gemini_and_mistral_only():
    from ytstudio.adapters.llm import load_llm_config
    from ytstudio.config import ROOT

    c = load_llm_config(ROOT / "config" / "llm.toml")
    models = {m for a in c["aliases"].values() for m in a["models"]}
    assert {m.split("/")[0] for m in models} == {"gemini", "mistral"}
    assert models <= set(c["prices"]) and set(c["rate_limits"]) == {"gemini", "mistral"}


def test_resolved_model_name_falls_back_to_alias_price(adapter_factory, user_and_channel, container, monkeypatch):
    llm, _ = adapter_factory('{"answer": "x"}')
    import litellm

    monkeypatch.setattr(litellm, "completion_cost", lambda **kw: (_ for _ in ()).throw(ValueError("unknown")))
    resp = type("R", (), {})()
    assert (
        llm._cost(resp, "gemini-9-pro-preview", (1_000_000, 0), "gemini/gemini-flash-latest") == 0.1
    )  # test config: flash input is 0.10 per 1M


def test_rejected_api_key_is_permanent_and_names_the_fix(adapter_factory, user_and_channel):
    import litellm

    from ytstudio.domain.errors import PermanentError

    bad = litellm.AuthenticationError(message="API key not valid", llm_provider="gemini", model="gemini-flash-latest")
    llm, _ = adapter_factory(bad, keys={"gemini": "AIza-a-saved-but-wrong-key"})
    with pytest.raises(PermanentError, match=r"rejected the API key.*LLM page"):  # not retried as if the network died
        llm.generate(req(user_and_channel[0]), Out)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("PaymentRequiredError", "free tier does not include it"),
        ("NotFoundError", "has no model called gemini/gemini-flash-latest"),
    ],
)
def test_billing_and_unknown_model_errors_are_permanent_and_say_what_to_do(
    adapter_factory, user_and_channel, error, expected
):
    import litellm

    from ytstudio.domain.errors import PermanentError

    boom = getattr(litellm, error)(message="x", model="gemini-flash-latest", llm_provider="gemini")
    llm, _ = adapter_factory(boom)
    with pytest.raises(PermanentError, match=expected):
        llm.generate(req(user_and_channel[0]), Out)


def test_a_missing_key_says_to_add_one_on_the_llm_page_before_any_call(adapter_factory, user_and_channel):
    from ytstudio.domain.errors import PermanentError

    llm, _ = adapter_factory('{"answer": "never reached"}', keys={})  # nothing saved for any model of the alias
    with pytest.raises(PermanentError, match=r"No Google Gemini API key is saved yet.*LLM page"):
        llm.generate(req(user_and_channel[0]), Out)
    llm, _ = adapter_factory('{"answer": "ok"}', "x", keys={"mistral": "test-key"})  # a fallback with a key is enough
    assert llm.generate(req(user_and_channel[0]), Out).answer == "ok"


def test_keys_are_never_taken_from_the_environment(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "from-the-environment")
    router = build_router({"aliases": {"fast": {"models": ["gemini/gemini-flash-latest"]}}, "defaults": {}})
    assert "api_key" not in router.model_list[0]["litellm_params"]  # only keys saved on the LLM page are passed on
