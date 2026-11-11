"""Ask a provider which models a key can use, so the Settings page can offer real names to pick from."""

import httpx

from ytstudio.domain.errors import PermanentError, TransientError


def list_models(provider: str, key: str) -> list[str]:
    """Model names as LiteLLM spells them (provider/model), newest first where the provider says so."""
    urls = {
        "gemini": "https://generativelanguage.googleapis.com/v1beta/models",
        "mistral": "https://api.mistral.ai/v1/models",
        "openai": "https://api.openai.com/v1/models",
        "anthropic": "https://api.anthropic.com/v1/models",
    }
    headers: dict[str, str] = {}
    params: dict[str, str | int] = {}
    if provider == "gemini":
        headers["x-goog-api-key"] = key  # in a header, so it never lands in a logged URL
        params["pageSize"] = 200
    elif provider == "anthropic":
        headers |= {"x-api-key": key, "anthropic-version": "2023-06-01"}
    else:
        headers["Authorization"] = f"Bearer {key}"
    try:
        resp = httpx.get(urls[provider], headers=headers, params=params, timeout=20)
    except httpx.HTTPError as e:
        raise TransientError(f"Could not reach {provider}: {type(e).__name__}") from e
    if resp.status_code in (400, 401, 403):
        raise PermanentError(f"{provider} rejected the key (HTTP {resp.status_code}).")
    if resp.status_code >= 400:
        raise TransientError(f"{provider} answered HTTP {resp.status_code}.")
    body = resp.json()
    if provider == "gemini":
        names = [
            m["name"].removeprefix("models/")
            for m in body.get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]
    else:
        names = [m["id"] for m in body.get("data", [])]
    return sorted({f"{provider}/{n}" for n in names})
