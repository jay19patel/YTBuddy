"""The LLM page (sidebar, under Sign out): your AI models and keys. Keys are write-only: they are saved encrypted and
never shown again."""

from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response

from ytstudio.adapters.youtube import friendly_error
from ytstudio.container import get_container
from ytstudio.domain.errors import DomainError
from ytstudio.domain.llm import ALIASES, MAX_MODELS, PROVIDERS
from ytstudio.interfaces.web.app import Page, back, csrf, page, render

router = APIRouter()


def llm_page(p: Page, status_code: int = 200, **notice: str) -> Response:
    """The page, plus an optional success=/error= message. It belongs to the account, not to a channel."""
    ctx: dict[str, Any] = {
        "ai": get_container().llm_settings.view(p.user),
        "account_page": True,  # no channel tabs on this page
    }
    return render(p, "llm.html", status_code=status_code, **ctx, **notice)


@router.get("/llm")
def llm(p: Page = Depends(page)) -> Response:
    return llm_page(p)


@router.post("/llm/models")
def refresh_models(p: Page = Depends(csrf)) -> Response:
    report = get_container().llm_settings.refresh_models(p.user)
    detail = "; ".join(f"{name}: {result}" for name, result in report.items()) or "Save an API key first."
    return llm_page(p, success=f"Model lists updated. {detail}")


@router.post("/llm/test")
def test_alias(alias: str = Form(...), p: Page = Depends(csrf)) -> Response:
    """A tiny real request through this account's models and keys: a wrong key shows up now, not in tomorrow's run."""
    try:
        result = get_container().llm_settings.check(p.user, alias)
    except DomainError as e:
        return llm_page(p, 400, error=friendly_error(e))
    return llm_page(p, success=f"'{alias}' works: {result['model']} answered in {result['ms'] / 1000:.1f} s.")


@router.post("/llm/save")
async def save_llm(request: Request, p: Page = Depends(csrf)) -> Response:
    form = await request.form()
    keys = {prov: str(form.get(f"key_{prov}") or "") for prov in PROVIDERS}
    remove = {prov for prov in PROVIDERS if form.get(f"remove_{prov}")}
    aliases = {a: [str(form.get(f"{a}_{i}") or "") for i in range(1, MAX_MODELS + 1)] for a in ALIASES}
    try:
        get_container().llm_settings.save(p.user, keys, remove, aliases)
    except DomainError as e:
        return llm_page(p, 400, error=friendly_error(e))
    return back("/llm", "ai_saved")
