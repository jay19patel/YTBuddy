"""Which models and API keys an account uses for the LLM aliases. Pure, no I/O.

Every account chooses its own models and keys on the LLM page. A model alias nobody has set falls back to the
defaults in config/llm.toml. Keys are never read from .env."""

import re
from dataclasses import dataclass, field

from ytstudio.domain.errors import PermanentError

PROVIDERS = {"gemini": "Google Gemini", "mistral": "Mistral", "openai": "OpenAI", "anthropic": "Anthropic"}
ALIASES = {
    "fast": "Fast: niche detection, tagging",
    "smart": "Smart: ideas, scripts, titles, SEO, channel setup",
    "long": "Long: very long inputs",
}
MAX_MODELS = 3  # a main model and up to two fallbacks
MODEL = re.compile(r"[a-z0-9_-]+/[\w.:@/+-]+")


def provider_of(model: str) -> str:
    return model.split("/", 1)[0]


def clean_models(values: list[str]) -> list[str]:
    """Blank entries and repeats dropped; every other entry must look like provider/model."""
    out: list[str] = []
    for raw in values:
        value = raw.strip()
        if not value:
            continue
        if not MODEL.fullmatch(value):
            raise PermanentError(
                f"'{value}' is not a model name. Use provider/model, for example gemini/gemini-flash-latest."
            )
        if value not in out:
            out.append(value)
    return out[:MAX_MODELS]


def clean_key(raw: str) -> str:
    key = raw.strip()
    if len(key) < 8 or any(c.isspace() for c in key):
        raise PermanentError("That does not look like an API key (it is too short or has spaces).")
    return key


@dataclass
class EffectiveLLM:
    """What one account actually uses right now."""

    aliases: dict[str, list[str]]
    keys: dict[str, str]  # provider -> key saved on the LLM page
    sources: dict[str, str] = field(default_factory=dict)  # "alias:smart" -> you | default


def merge(
    defaults: dict[str, list[str]], user_aliases: dict[str, list[str]], user_keys: dict[str, str]
) -> EffectiveLLM:
    aliases: dict[str, list[str]] = {}
    sources: dict[str, str] = {}
    for alias in ALIASES:
        for source, models_by_alias in (("you", user_aliases), ("default", defaults)):
            if models_by_alias.get(alias):
                aliases[alias], sources[f"alias:{alias}"] = list(models_by_alias[alias]), source
                break
    return EffectiveLLM(
        aliases=aliases, keys={p: k for p, k in user_keys.items() if p in PROVIDERS and k}, sources=sources
    )
