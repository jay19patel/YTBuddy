"""LLM adapter over the LiteLLM Router.

- Code asks for an alias (fast/smart/long). Which models and keys it maps to is resolved per user: the user's own
  settings, else the global settings, else the defaults in config/llm.toml (see domain/llm.py). Keys come only
  from those saved settings, never from the environment.
- The Router owns provider retries, timeouts and fallbacks. When it gives up we raise TransientError once,
  so Celery's retry is the only outer retry and the two never multiply.
- Output is validated against a Pydantic schema; one repair call, then LLMOutputInvalid.
- Responses are cached in Redis by alias + prompt hash + schema version.
- Every call (cache hits too) is recorded with model, tokens, cost, latency, run and step.
"""

import hashlib
import json
import logging
import re
import time
import tomllib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from ytstudio.domain.errors import BudgetExceeded, LLMOutputInvalid, PermanentError, TransientError
from ytstudio.domain.events import BudgetReached, Event
from ytstudio.domain.llm import PROVIDERS, EffectiveLLM, provider_of
from ytstudio.logging_setup import redact
from ytstudio.ports import LLMRequest, PromptRepository, RateLimiter, T, UowFactory

log = logging.getLogger(__name__)


def load_llm_config(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text())


def build_router(cfg: dict[str, Any], eff: EffectiveLLM | None = None) -> Any:
    """A LiteLLM Router for one effective configuration. Without `eff` it uses the defaults in config/llm.toml."""
    from litellm import Router

    d = cfg.get("defaults", {})
    model_list: list[dict[str, Any]] = []
    fallbacks: list[dict[str, list[str]]] = []
    aliases = {a: s["models"] for a, s in cfg["aliases"].items()} if eff is None else eff.aliases
    for alias, models in aliases.items():
        names = []
        for i, model in enumerate(models):
            name = alias if i == 0 else f"{alias}#{i}"
            params = {"model": model} | cfg["aliases"].get(alias, {}).get("params", {})
            if eff and (key := eff.keys.get(provider_of(model))):
                params["api_key"] = key  # saved on the LLM page
            model_list.append({"model_name": name, "litellm_params": params})
            names.append(name)
        if len(names) > 1:
            fallbacks.append({alias: names[1:]})
    return Router(
        model_list=model_list,
        fallbacks=fallbacks,
        num_retries=d.get("num_retries", 2),
        timeout=d.get("timeout_s", 180),
        allowed_fails=d.get("allowed_fails", 3),
        cooldown_time=d.get("cooldown_s", 60),
    )


def extract_json(text: str) -> str:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.M)
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else text


def schema_version(schema: type[BaseModel]) -> str:
    return str(getattr(schema, "VERSION", "1"))


class LiteLLMAdapter:
    def __init__(
        self,
        resolver: Callable[[int], EffectiveLLM],
        router_factory: Callable[[EffectiveLLM], Any],
        cfg: dict[str, Any],
        prompts: PromptRepository,
        uow: UowFactory,
        cache: Any,
        limiter: RateLimiter,
        publish: Callable[[Event], None],
        cache_ttl_s: int,
        claim_once: Callable[[str], bool],
    ):
        self.resolver, self.router_factory = resolver, router_factory
        self._routers: dict[str, Any] = {}  # one Router per distinct configuration; changing Settings makes a new one
        self.cfg, self.prompts, self.uow = cfg, prompts, uow
        self.cache, self.limiter, self.publish, self.ttl = cache, limiter, publish, cache_ttl_s
        self.claim_once = claim_once  # Redis SET NX: notify about the budget once per user per day

    # ----- public -----

    def check(self, user_id: int, alias: str) -> dict[str, Any]:
        """A tiny real call through the alias (keys, models and fallbacks as this user has them): proves it works."""
        eff = self.resolver(user_id)
        started = time.monotonic()
        resp = self._call(eff, alias, [{"role": "user", "content": "Reply with the single word OK."}], 64)
        return {
            "model": str(getattr(resp, "model", eff.aliases[alias][0])),
            "ms": int((time.monotonic() - started) * 1000),
        }

    def generate(self, req: LLMRequest, schema: type[T]) -> T:
        eff = self.resolver(req.user_id)
        if req.alias not in eff.aliases:
            raise PermanentError(f"No model is set for '{req.alias}'. Choose one in Settings, AI models and keys.")
        tpl, system, user = self.prompts.render(req.template, req.variables)
        system += (
            "\n\nReply with one JSON object and nothing else. It must validate against this JSON Schema:\n"
            + json.dumps(schema.model_json_schema())
        )
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        key = "yt:llm:" + ":".join(
            [
                req.alias,
                "+".join(eff.aliases[req.alias]),  # a different model must not be served the old model's answer
                schema.__name__,
                schema_version(schema),
                hashlib.sha256(json.dumps(messages).encode()).hexdigest(),
            ]
        )
        meta = {
            "user_id": req.user_id,
            "run_id": req.run_id,
            "step": req.step,
            "alias": req.alias,
            "template": tpl.name,
            "template_version": tpl.version,
        }

        if not req.regenerate and (hit := self.cache.get(key)) is not None:
            self._record(meta, model="cache", cost=0.0, tokens=(0, 0), latency_ms=0, cache_hit=True)
            return schema.model_validate(hit)

        self._check_budget(req)
        text = self._complete(eff, req.alias, messages, meta)
        try:
            out = schema.model_validate_json(extract_json(text))
        except ValidationError as e:
            repair = [
                *messages,
                {"role": "assistant", "content": text},
                {
                    "role": "user",
                    "content": "That JSON did not validate:\n"
                    + str(e)[:2000]
                    + "\nReturn only the corrected JSON object.",
                },
            ]
            text = self._complete(eff, req.alias, repair, meta | {"step": f"{req.step}:repair"})
            try:
                out = schema.model_validate_json(extract_json(text))
            except ValidationError as e2:
                raise LLMOutputInvalid(
                    f"{req.template}: output failed {schema.__name__} after one repair: {str(e2)[:300]}"
                ) from e2
        self.cache.set(key, out.model_dump(mode="json"), self.ttl)
        return out

    # ----- internals -----

    def _check_budget(self, req: LLMRequest) -> None:
        if req.critical:
            return
        today = datetime.now(UTC).date()
        with self.uow() as uow:
            user = uow.users.get(req.user_id)
            spent = uow.llm_calls.spent_today(req.user_id, today)
        if user.daily_budget_usd <= 0:  # 0 = no limit (free tiers: the provider's rate limit is the real cap)
            return
        if spent >= user.daily_budget_usd:
            if self.claim_once(f"yt:llm:budget-notified:{req.user_id}:{today}"):
                self.publish(BudgetReached(user_id=req.user_id, spent_usd=spent))
            raise BudgetExceeded(f"Daily LLM budget ${user.daily_budget_usd:.2f} reached (${spent:.2f} spent)")

    def _router(self, eff: EffectiveLLM) -> Any:
        sig = hashlib.sha256(json.dumps([eff.aliases, eff.keys], sort_keys=True).encode()).hexdigest()
        if sig not in self._routers:
            if len(self._routers) >= 16:  # settings change rarely; start over rather than grow
                self._routers.clear()
            self._routers[sig] = self.router_factory(eff)
        return self._routers[sig]

    def _call(self, eff: EffectiveLLM, alias: str, messages: list[dict[str, str]], max_tokens: int) -> Any:
        provider = provider_of(eff.aliases[alias][0])
        where = "Open the LLM page (sidebar, under Sign out)"
        providers = [provider_of(m) for m in eff.aliases[alias]]
        if all(p in PROVIDERS for p in providers) and not any(eff.keys.get(p) for p in providers):
            # LiteLLM reports a missing key as a vague connection error, so say it plainly before calling
            raise PermanentError(f"No {PROVIDERS[providers[0]]} API key is saved yet. {where} and paste one.")
        try:
            return self._router(eff).completion(model=alias, messages=messages, max_tokens=max_tokens)
        except Exception as e:  # the Router already retried and fell back; this is its final answer
            name, model = provider and PROVIDERS.get(provider, provider), eff.aliases[alias][0]
            if type(e).__name__ == "AuthenticationError":  # retrying never helps for these three
                hint = (
                    " Gemini keys start with AIza (https://aistudio.google.com/apikey)." if provider == "gemini" else ""
                )
                raise PermanentError(
                    f"{name} rejected the API key for '{alias}'. {where}, and check that key.{hint}"
                ) from e
            if type(e).__name__ == "PaymentRequiredError":
                raise PermanentError(
                    f"{name} wants billing for {model}; its free tier does not include it. {where} and choose a free "
                    f"model for '{alias}' (for Gemini: gemini/gemini-flash-latest), or turn on billing with {name}."
                ) from e
            if type(e).__name__ == "NotFoundError":
                raise PermanentError(f"{name} has no model called {model}. {where} and pick one from the list.") from e
            raise TransientError(f"LLM {alias} unavailable: {type(e).__name__}: {redact(str(e)[:200])}") from e

    def _complete(self, eff: EffectiveLLM, alias: str, messages: list[dict[str, str]], meta: dict[str, Any]) -> str:
        provider = provider_of(eff.aliases[alias][0])
        # shortcut: the bucket is the primary model's provider; a fallback to another provider shares it.
        rl = self.cfg.get("rate_limits", {}).get(provider, {"rate": 1.0, "burst": 5})
        if not self.limiter.acquire(f"llm:{provider}", rl["rate"], rl["burst"], timeout_s=60):
            raise TransientError(f"local rate limit for {provider}")
        started = time.monotonic()
        resp = self._call(eff, alias, messages, self.cfg.get("defaults", {}).get("max_tokens", 16000))
        latency = int((time.monotonic() - started) * 1000)
        usage = getattr(resp, "usage", None)
        tokens = (int(getattr(usage, "prompt_tokens", 0) or 0), int(getattr(usage, "completion_tokens", 0) or 0))
        model = str(getattr(resp, "model", alias))
        self._record(
            meta,
            model=model,
            cost=self._cost(resp, model, tokens, eff.aliases[alias][0]),
            tokens=tokens,
            latency_ms=latency,
            cache_hit=False,
        )
        return str(resp.choices[0].message.content or "")

    def _cost(self, resp: Any, model: str, tokens: tuple[int, int], primary: str) -> float:
        """Configured price for the model; else LiteLLM's price map; else the alias's primary model price.
        "-latest" aliases come back under the resolved model name, so the last fallback keeps the budget honest."""
        prices = self.cfg.get("prices", {})
        price = next((p for name, p in prices.items() if name == model or name.split("/")[-1] == model), None)
        if not price:
            try:
                from litellm import completion_cost

                return float(completion_cost(completion_response=resp))
            except Exception:
                price = prices.get(primary)
        if not price:
            log.warning("no price for model %s; cost recorded as 0", model)
            return 0.0
        return (tokens[0] * price["input"] + tokens[1] * price["output"]) / 1_000_000

    def _record(
        self, meta: dict[str, Any], model: str, cost: float, tokens: tuple[int, int], latency_ms: int, cache_hit: bool
    ) -> None:
        with self.uow() as uow:
            uow.llm_calls.record(
                **meta,
                model=model,
                provider=model.split("/")[0] if "/" in model else "",
                prompt_tokens=tokens[0],
                completion_tokens=tokens[1],
                cost_usd=cost,
                latency_ms=latency_ms,
                cache_hit=cache_hit,
            )
            uow.commit()


class FakeLLM:
    """Test double: returns canned objects per template; records requests."""

    def __init__(
        self,
        responses: dict[str, Callable[[LLMRequest], dict[str, Any]] | dict[str, Any]],
        prompts: PromptRepository | None = None,
    ):
        self.responses = responses
        self.prompts = prompts  # when given, every request renders its real template, so a missing variable fails
        self.calls: list[LLMRequest] = []

    def check(self, user_id: int, alias: str) -> dict[str, Any]:
        return {"model": f"fake/{alias}", "ms": 5}

    def generate(self, req: LLMRequest, schema: type[T]) -> T:
        self.calls.append(req)
        if self.prompts:
            self.prompts.render(req.template, req.variables)
        r = self.responses[req.template]
        return schema.model_validate(r(req) if callable(r) else r)
