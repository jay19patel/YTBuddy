"""PIN sign-in, and channel connect/disconnect through Google. OAuth state + PKCE verifier are stored
server-side, single-use, 10-minute expiry, and bound to the browser that started the flow."""

import secrets
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response

from ytstudio.adapters.youtube import CHANNEL_SCOPES
from ytstudio.config import get_settings
from ytstudio.container import get_container
from ytstudio.domain.errors import DomainError
from ytstudio.interfaces.web.app import Page, back, csrf, page, sessions, templates

router = APIRouter()
NONCE = "ytnonce"
PIN_TRIES, PIN_WINDOW_S = 5, 15 * 60  # a 6-digit PIN must not be guessable by brute force


def cookie(resp: Response, name: str, value: str, max_age: int) -> None:
    resp.set_cookie(name, value, max_age=max_age, httponly=True, samesite="lax", secure=get_settings().secure_cookies)


def has_client() -> bool:
    return bool(get_settings().google_client()[0])


def start(purpose: str, user_id: int | None) -> Response:
    s = sessions()
    nonce = secrets.token_urlsafe(24)
    begun = get_container().oauth.start(CHANNEL_SCOPES)
    s.save_state(begun.state, purpose, begun.code_verifier, nonce, user_id)
    resp = RedirectResponse(begun.url, status_code=303)
    cookie(resp, NONCE, nonce, 600)
    return resp


def login_page(request: Request, error: str | None = None, status: int = 200) -> Response:
    return templates.TemplateResponse(
        request,
        "login.html",
        {"user": None, "error": error, "pin_set": bool(get_settings().app_pin)},
        status_code=status,
    )


@router.get("/login")
def login(request: Request) -> Response:
    if sessions().get(request.cookies.get("ytsid")):
        return RedirectResponse("/today", status_code=303)
    return login_page(request)


@router.post("/login")
def login_pin(request: Request, pin: str = Form("")) -> Response:
    s = get_settings()
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != urlparse(s.base_url).netloc:
        return login_page(request, "Cross-site request blocked.", 403)
    r = get_container().redis
    key = f"yt:rl:pin:{request.client.host if request.client else 'unknown'}"
    if int(r.get(key) or 0) >= PIN_TRIES:
        return login_page(request, "Too many wrong PINs. Wait 15 minutes and try again.", 429)
    if not s.app_pin or not secrets.compare_digest(pin.strip().encode(), s.app_pin.encode()):
        r.incr(key)
        r.expire(key, PIN_WINDOW_S)
        return login_page(request, "Wrong PIN." if s.app_pin else "Set APP_PIN in .env first.", 401)
    r.delete(key)
    user = get_container().accounts.owner()
    resp = RedirectResponse("/today", status_code=303)
    cookie(resp, "ytsid", sessions().create(user.id), 14 * 86400)
    return resp


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
            request, "error.html", {"error": msg, "user": None, "retry": "/today"}, status_code=status
        )

    if error == "access_denied":
        return fail(
            "Google refused access. If you did not press Cancel: in Google Cloud Console, OAuth consent "
            "screen, add this Gmail under Test users, then try again."
        )
    if error:
        return fail(f"Google said: {error}")
    pending = sessions().pop_state(state, request.cookies.get(NONCE))
    if not pending:
        return fail("Login session expired or came from another browser. Try again.")
    c = get_container()
    result = c.oauth.finish(CHANNEL_SCOPES, str(request.url), state, pending.code_verifier)
    current = sessions().get(request.cookies.get("ytsid"))
    if not current or current.user_id != pending.user_id:  # connect must finish as the user who started it
        return fail("Enter your PIN first, then add the channel.", 403)
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
