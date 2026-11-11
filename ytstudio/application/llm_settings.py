"""Use case: choose your AI models and API keys."""

import hashlib
from collections.abc import Callable
from typing import Any

from ytstudio.domain.errors import DomainError, PermanentError
from ytstudio.domain.llm import (
    ALIASES,
    MAX_MODELS,
    PROVIDERS,
    EffectiveLLM,
    clean_key,
    clean_models,
    merge,
)
from ytstudio.domain.models import User
from ytstudio.ports import Cache, LLMSettingsStore

MODELS_TTL_S = 6 * 3600


class LLMSettings:
    def __init__(
        self,
        store: LLMSettingsStore,
        defaults: dict[str, list[str]],
        cache: Cache,
        lister: Callable[[str, str], list[str]],
        checker: Callable[[int, str], dict[str, Any]],
    ):
        self.store, self.defaults, self.cache, self.lister, self.checker = store, defaults, cache, lister, checker

    @staticmethod
    def scope(user_id: int) -> str:
        return f"user:{user_id}"

    # ----- what an account actually uses (called on every LLM request) -----

    def effective(self, user_id: int) -> EffectiveLLM:
        scope = self.scope(user_id)
        return merge(self.defaults, self.store.aliases(scope), self.store.keys(scope))

    # ----- change -----

    def save(self, user: User, keys: dict[str, str], remove: set[str], aliases: dict[str, list[str]]) -> None:
        """Everything is checked before anything is written. A blank key box keeps the saved key."""
        scope = self.scope(user.id)
        new_keys = {p: clean_key(k) for p, k in keys.items() if p in PROVIDERS and k.strip()}
        new_models = {a: clean_models(m) for a, m in aliases.items() if a in ALIASES}
        for provider, key in new_keys.items():
            self.store.set_key(scope, provider, key)
        for provider in (remove & set(PROVIDERS)) - set(new_keys):
            self.store.delete_key(scope, provider)
        self.store.set_aliases(scope, new_models)

    def check(self, user: User, alias: str) -> dict[str, Any]:
        if alias not in ALIASES:
            raise PermanentError("Unknown alias.")
        return self.checker(user.id, alias)

    # ----- model lists (what the model boxes suggest) -----

    def _models_key(self, provider: str, key: str) -> str:
        return f"llm-models:{provider}:{hashlib.sha256(key.encode()).hexdigest()[:12]}"  # never the key itself

    def refresh_models(self, user: User) -> dict[str, str]:
        """Ask each provider (that this account has a key for) which models it offers; remembered for 6 hours."""
        eff, report = self.effective(user.id), {}
        for provider, key in eff.keys.items():
            try:
                names = self.lister(provider, key)
            except DomainError as e:
                report[provider] = str(e)
                continue
            self.cache.set(self._models_key(provider, key), names, MODELS_TTL_S)
            report[provider] = f"{len(names)} models"
        return report

    def available_models(self, user_id: int) -> list[str]:
        eff = self.effective(user_id)
        names = {m for models in [*self.defaults.values(), *eff.aliases.values()] for m in models}
        for provider, key in eff.keys.items():
            names |= set(self.cache.get(self._models_key(provider, key)) or [])
        return sorted(names)

    # ----- the LLM page -----

    def view(self, user: User) -> dict[str, Any]:
        scope = self.scope(user.id)
        own_keys, own_aliases, eff = self.store.keys(scope), self.store.aliases(scope), self.effective(user.id)
        providers = [
            {
                "id": pid,
                "name": name,
                "set": pid in own_keys,
                "hint": own_keys.get(pid, "")[-4:],
                "status": "Key saved" if pid in own_keys else "No key yet",
            }
            for pid, name in PROVIDERS.items()
        ]
        aliases = [
            {
                "id": aid,
                "about": about,
                "models": own_aliases.get(aid, []),
                "used": eff.aliases.get(aid, []),
                "source": "your models" if eff.sources.get(f"alias:{aid}") == "you" else "default models",
            }
            for aid, about in ALIASES.items()
        ]
        return {
            "providers": providers,
            "aliases": aliases,
            "models": self.available_models(user.id),
            "max_models": MAX_MODELS,
        }
