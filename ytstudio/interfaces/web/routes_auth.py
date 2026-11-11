"""Google sign-in (mandatory, Gmail-only) and channel connect/disconnect.

Login flow:
  GET /login  →  Google OAuth (openid + email scopes)  →  GET /oauth2callback
  Callback fetches the user's verified Gmail address, looks up or creates the
  UserRow, and issues a 14-day session cookie.  No PIN is involved.

Channel-connect flow (after login):
  GET /connect  →  Google OAuth (YouTube channel scopes)  →  GET /oauth2callback
  Callback adds the channel and redirects to /settings.

OAuth state + PKCE verifier are stored server-side, single-use, 10-minute
expiry, and bound to the browser that started the flow.
"""

import secrets
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response

from ytstudio.adapters.youtube import CHANNEL_SCOPES
from ytstudio.application.use_cases import NotAllowed
from ytstudio.config import get_settings
from ytstudio.container import get_container
from ytstudio.domain.errors import DomainError
from ytstudio.interfaces.web.app import Page, back, csrf, page, sessions, templates

router = APIRouter()
NONCE = "ytnonce"

# Scopes used only to identify the signed-in Google account (no YouTube access).
LOGIN_SCOPES = ["openid", "email"]


def cookie(resp: Response, name: str, value: str, max_age: int) -> None:
    resp.set_cookie(name, value, max_age=max_age, httponly=True, samesite="lax", secure=get_settings().secure_cookies)


def has_client() -> bool:
    return bool(get_settings().google_client()[0])


def _start_oauth(purpose: str, scopes: list[str], user_id: int | None) -> Response:
    """Begin an OAuth flow, store server-side state, redirect to Google."""
    s = sessions()
    nonce = secrets.token_urlsafe(24)
    begun = get_container().oauth.start(scopes)
    s.save_state(begun.state, purpose, begun.code_verifier, nonce, user_id)
    resp = RedirectResponse(begun.url, status_code=303)
    cookie(resp, NONCE, nonce, 600)
    return resp


def _login_error(request: Request, error: str, status: int = 400) -> Response:
    """Render the login page with an error banner."""
    return templates.TemplateResponse(
        request,
        "login.html",
        {"user": None, "error": error},
        status_code=status,
    )


# ---------------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------------


@router.get("/login")
def login(request: Request) -> Response:
    """Redirect to Google immediately; there is no manual login form."""
    if sessions().get(request.cookies.get("ytsid")):
        return RedirectResponse("/insights", status_code=303)
    if not has_client():
        return _login_error(request, "Google OAuth client not configured. Add YTStudioCred.json and restart.", 503)
    return _start_oauth("login", LOGIN_SCOPES, None)


@router.get("/connect")
def connect(p: Page = Depends(page)) -> Response:
    """Add a YouTube channel (requires being logged in already)."""
    return _start_oauth("connect", CHANNEL_SCOPES, p.user.id)


@router.get("/auth")
def legacy_auth() -> Response:
    return RedirectResponse("/connect", status_code=303)  # old "+ Add channel" bookmarks


# ---------------------------------------------------------------------------
# Shared OAuth callback
# ---------------------------------------------------------------------------


@router.get("/oauth2callback")
def oauth2callback(request: Request, state: str = "", error: str = "") -> Response:
    def fail(msg: str, status: int = 400) -> Response:
        return templates.TemplateResponse(
            request, "error.html", {"error": msg, "user": None, "retry": "/login"}, status_code=status
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
        return fail("Login session expired or came from another browser. Please try again.")

    c = get_container()

    # ---- LOGIN flow: identify the user via Google, create/find their account ----
    if pending.purpose == "login":
        result = c.oauth.finish(LOGIN_SCOPES, str(request.url), state, pending.code_verifier)
        email = result.email
        if not email:
            return fail(
                "Google did not return a verified email address. "
                "Make sure the account has a verified Gmail and try again."
            )
        # Look up or create the user by their Gmail address.
        try:
            user = c.accounts.owner(email)
        except NotAllowed as e:
            return fail(str(e), 403)
        resp = RedirectResponse("/insights", status_code=303)
        cookie(resp, "ytsid", sessions().create(user.id), 14 * 86400)
        resp.delete_cookie(NONCE)
        return resp

    # ---- CONNECT flow: add a YouTube channel for the already-logged-in user ----
    if pending.purpose == "connect":
        current = sessions().get(request.cookies.get("ytsid"))
        if not current or current.user_id != pending.user_id:
            return fail("Session mismatch. Please log in first, then add the channel.", 403)
        result = c.oauth.finish(CHANNEL_SCOPES, str(request.url), state, pending.code_verifier)
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

    return fail(f"Unknown OAuth purpose: {pending.purpose!r}")


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


@router.post("/logout")
def logout(request: Request, p: Page = Depends(csrf)) -> Response:
    sessions().delete(request.cookies.get("ytsid"))
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("ytsid")
    return resp


# ---------------------------------------------------------------------------
# Channel switcher
# ---------------------------------------------------------------------------


@router.get("/use/{channel_id}")
def use(request: Request, channel_id: str, p: Page = Depends(page)) -> Response:
    # Stay on the same page; path only, so no redirect off-site.
    path = urlparse(request.headers.get("referer", "/")).path
    if not path.startswith("/") or path.startswith(("//", "/assets/", "/insights/video/")):
        path = "/insights"  # a detail page belongs to one channel; the other channel's list page is the right place
    resp = RedirectResponse(path, status_code=303)
    if any(ch["id"] == channel_id for ch in p.channels):
        cookie(resp, "channel", channel_id, 365 * 86400)
    return resp


@router.post("/channels/{channel_id}/disconnect")
def disconnect(channel_id: str, p: Page = Depends(csrf)) -> Response:
    get_container().accounts.disconnect(p.user.id, channel_id)
    return back("/insights", "disconnected")
