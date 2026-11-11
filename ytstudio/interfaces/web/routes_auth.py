"""Login (Google, allowlisted emails) and channel connect/disconnect. OAuth state + PKCE verifier are stored
server-side, single-use, 10-minute expiry, and bound to the browser that started the flow."""

import secrets
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response

from ytstudio.adapters.youtube import CHANNEL_SCOPES, LOGIN_SCOPES
from ytstudio.application.use_cases import NotAllowed
from ytstudio.config import get_settings
from ytstudio.container import get_container
from ytstudio.domain.errors import DomainError
from ytstudio.interfaces.web.app import Page, back, csrf, page, sessions, templates

router = APIRouter()
NONCE = "ytnonce"


def cookie(resp: Response, name: str, value: str, max_age: int) -> None:
    resp.set_cookie(name, value, max_age=max_age, httponly=True, samesite="lax", secure=get_settings().secure_cookies)


def has_client() -> bool:
    return bool(get_settings().google_client()[0])


def start(purpose: str, user_id: int | None) -> Response:
    s = sessions()
    nonce = secrets.token_urlsafe(24)
    begun = get_container().oauth.start(LOGIN_SCOPES if purpose == "login" else CHANNEL_SCOPES)
    s.save_state(begun.state, purpose, begun.code_verifier, nonce, user_id)
    resp = RedirectResponse(begun.url, status_code=303)
    cookie(resp, NONCE, nonce, 600)
    return resp


@router.get("/login")
def login(request: Request) -> Response:
    if sessions().get(request.cookies.get("ytsid")):
        return RedirectResponse("/today", status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"user": None, "has_client": has_client(), "redirect_uri": get_settings().redirect_uri}
    )


@router.get("/login/start")
def login_start() -> Response:
    return start("login", None)


@router.get("/connect")
def connect(p: Page = Depends(page)) -> Response:
    return start("connect", p.user.id)


@router.get("/auth")
def legacy_auth() -> Response:
    return RedirectResponse("/connect", status_code=303)  # old "+ Add channel" bookmarks


@router.get("/oauth2callback")
def oauth2callback(request: Request, state: str = "", error: str = "") -> Response:
    def fail(msg: str, status: int = 400) -> Response:
        return templates.TemplateResponse(
            request, "error.html", {"error": msg, "user": None, "retry": "/login"}, status_code=status
        )

    if error:
        return fail("Google sign-in was cancelled." if error == "access_denied" else f"Google said: {error}")
    pending = sessions().pop_state(state, request.cookies.get(NONCE))
    if not pending:
        return fail("Login session expired or came from another browser. Try again.")
    c = get_container()
    scopes = LOGIN_SCOPES if pending.purpose == "login" else CHANNEL_SCOPES
    result = c.oauth.finish(scopes, str(request.url), state, pending.code_verifier)

    if pending.purpose == "login":
        try:
            user = c.accounts.login(result.email)
        except NotAllowed as e:
            return fail(str(e), 403)
        resp = RedirectResponse("/today", status_code=303)
        cookie(resp, "ytsid", sessions().create(user.id), 14 * 86400)
        resp.delete_cookie(NONCE)
        return resp

    current = sessions().get(request.cookies.get("ytsid"))
    if not current or current.user_id != pending.user_id:  # connect must finish as the user who started it
        return fail("Sign in to YTStudio first, then add the channel.", 403)
    try:
        channel = c.accounts.connect(current.user_id, result.token)
    except DomainError as e:
        return fail(str(e))
    from ytstudio.interfaces.worker import detect_profile

    detect_profile.apply_async(args=(channel.id,), queue="llm")
    resp = back("/settings", "connected")
    cookie(resp, "channel", channel.id, 365 * 86400)
    resp.delete_cookie(NONCE)
    return resp


@router.post("/logout")
def logout(request: Request, p: Page = Depends(csrf)) -> Response:
    sessions().delete(request.cookies.get("ytsid"))
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("ytsid")
    return resp


@router.get("/use/{channel_id}")
def use(request: Request, channel_id: str, p: Page = Depends(page)) -> Response:
    # Stay on the same page; path only, so no redirect off-site.
    path = urlparse(request.headers.get("referer", "/")).path
    resp = RedirectResponse(path if path.startswith("/") and not path.startswith("//") else "/today", status_code=303)
    if any(ch["id"] == channel_id for ch in p.channels):
        cookie(resp, "channel", channel_id, 365 * 86400)
    return resp


@router.post("/channels/{channel_id}/disconnect")
def disconnect(channel_id: str, p: Page = Depends(csrf)) -> Response:
    get_container().accounts.disconnect(p.user.id, channel_id)
    return back("/today", "disconnected")
