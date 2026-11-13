"""FastAPI app: thin routers over use cases. Server-rendered Jinja pages, session cookie auth, CSRF on every POST."""

import asyncio
import logging
import secrets
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ytstudio.adapters.persistence.queries import Queries
from ytstudio.adapters.persistence.web_sessions import WebSessions
from ytstudio.config import get_settings
from ytstudio.container import get_container
from ytstudio.domain.errors import DomainError, NotFound
from ytstudio.logging_setup import request_id_var, setup_logging

HERE = Path(__file__).parent
log = logging.getLogger(__name__)

MESSAGES = {
    "saved": "Settings saved.",
    "deleted": "Deleted.",
    "ai_saved": "AI settings saved. Use the Test buttons to check that they work.",
    "about_saved": "About updated on YouTube.",
    "idea_added": "Idea added. Develop it into a script when you are ready.",
    "started": "Started. Progress shows below.",
    "regenerating": "Regenerating. Reload in a minute.",
    "approved": "Approved.",
    "discarded": "Discarded.",
    "edited": "Saved. Status is back to needs review.",
    "retried": "Retrying from the failed step.",
    "disconnected": "Channel removed and its Google access revoked.",
    "connected": "Channel connected. Its niche, language and region are being detected; confirm them below.",
    "already": "Already ran today for this channel; showing that run.",
    "done_already": "That is already done. The script, titles and SEO are on this page.",
    "uploaded": "Upload finished. Your file is ready: press Make Shorts next to it.",
    "master_deleted": "File deleted. Shorts already made from it are kept.",
    "short_uploaded": "Uploaded to YouTube. The Short now shows a green Uploaded tick with its link.",
    "busy": "Another run for this channel is active.",
}


class LoginRequired(Exception):
    pass


def ago(dt: datetime | None) -> str:
    if not dt:
        return "never"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    secs = int((dt - datetime.now(UTC)).total_seconds())
    m = abs(secs) // 60
    span = (
        "under a minute"
        if m < 1
        else f"{m} min"
        if m < 60
        else f"{m // 60} h {m % 60} min"
        if m < 1440
        else f"{m // 1440} days"
    )
    return f"in {span}" if secs > 0 else f"{span} ago"


def short(n: float) -> str:
    return next(
        (f"{n / d:.1f}".rstrip("0").rstrip(".") + u for d, u in ((1e9, "B"), (1e6, "M"), (1e3, "K")) if abs(n) >= d),
        f"{n:,}",
    )


templates = Jinja2Templates(directory=HERE / "templates")
# A changed file gets a new URL, so a browser never keeps serving the old CSS or JS.
templates.env.globals["static_v"] = str(int(max(f.stat().st_mtime for f in (HERE / "static").glob("*.*"))))
templates.env.filters["num"] = lambda n, digits=0: f"{float(n or 0):,.{digits}f}"
templates.env.filters["short"] = short
templates.env.filters["dur"] = lambda s: f"{int(s or 0) // 60}:{int(s or 0) % 60:02d}"
templates.env.filters["ago"] = ago
templates.env.filters["clock"] = lambda dt: (
    (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).astimezone().strftime("%-d %b, %H:%M") if dt else "-"
)


@dataclass
class Page:
    request: Request
    user: Any
    csrf: str
    channels: list[dict[str, Any]]
    active: dict[str, Any] | None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def channel_id(self) -> str:
        if not self.active:
            raise HTTPException(409, "Connect a channel first")
        return str(self.active["id"])


def sessions() -> WebSessions:
    return WebSessions(get_settings().database_url)


def queries() -> Queries:
    return Queries(get_settings().database_url)


def page(request: Request) -> Page:
    info = sessions().get(request.cookies.get("ytsid"))
    if not info:
        raise LoginRequired()
    c = get_container()
    with c.uow() as uow:
        user = uow.users.get(info.user_id)
    q = queries()
    chans = q.channels(user.id)
    wanted = request.cookies.get("channel")
    active = next((ch for ch in chans if ch["id"] == wanted), chans[0] if chans else None)
    return Page(request, user, info.csrf, chans, active)


async def csrf(request: Request, p: Page = Depends(page)) -> Page:
    """Every state-changing request: same-origin check plus a per-session token (form field or header)."""
    origin = request.headers.get("origin") or request.headers.get("referer")
    if origin and urlparse(origin).netloc != urlparse(get_settings().base_url).netloc:
        raise HTTPException(403, "Cross-site request blocked")
    token = request.headers.get("x-csrf-token")
    if not token and "form" in request.headers.get("content-type", ""):
        token = str((await request.form()).get("csrf") or "")
    if not token or not secrets.compare_digest(token, p.csrf):
        raise HTTPException(403, "Missing or wrong CSRF token. Reload the page and try again.")
    return p


def render(p: Page, name: str, status_code: int = 200, **ctx: Any) -> Response:
    s = get_settings()
    msg = MESSAGES.get(p.request.query_params.get("m", ""))
    return templates.TemplateResponse(
        p.request,
        name,
        {
            "user": p.user,
            "csrf": p.csrf,
            "accounts": p.channels,
            "current": p.active,
            "flags": {"publish": s.enable_manual_publish},
            "has_client": bool(s.google_client()[0]),
            "redirect_uri": s.redirect_uri,
            "success": msg,
            "path": p.request.url.path,
            "today": datetime.now(UTC).date(),
            **ctx,
        },
        status_code=status_code,
    )


def back(url: str, msg: str | None = None, run: int | None = None) -> RedirectResponse:
    params = "&".join(x for x in (f"m={msg}" if msg else "", f"run={run}" if run else "") if x)
    return RedirectResponse(f"{url}?{params}" if params else url, status_code=303)


async def beat_watchdog() -> None:
    """Alert once when Celery Beat stops ticking (the daily runs depend on it)."""
    from ytstudio.domain.events import BeatStale

    s, alerted = get_settings(), False
    while True:
        await asyncio.sleep(300)
        try:
            c = get_container()
            health = await asyncio.to_thread(c.health)
            stale = not health["beat"]["ok"] and health["beat"].get("last_tick")
            if stale and not alerted:
                with c.uow() as uow:
                    users = uow.users.all()
                for u in users:
                    await asyncio.to_thread(c.events.publish, BeatStale(user_id=u.id, minutes=s.beat_stale_minutes))
            alerted = bool(stale)
        except Exception:
            log.exception("beat watchdog failed")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    task = asyncio.create_task(beat_watchdog())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


def create_app() -> FastAPI:
    s = get_settings()
    setup_logging(s.log_level, s.log_json)
    app = FastAPI(title="YTStudio", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.middleware("http")
    async def headers(request: Request, call_next: Any) -> Response:
        request_id_var.set(uuid.uuid4().hex[:12])
        resp: Response = await call_next(request)
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' https://*.ggpht.com https://*.ytimg.com data:; "
            "style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
            "media-src 'self' blob:; frame-ancestors 'none'",
        )
        return resp

    @app.exception_handler(LoginRequired)
    async def login_required(request: Request, _: LoginRequired) -> Response:
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": "login required"}, status_code=401)
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(NotFound)
    async def not_found(request: Request, exc: NotFound) -> Response:
        return (
            JSONResponse({"error": "not found"}, status_code=404)
            if request.url.path.startswith("/api/")
            else templates.TemplateResponse(
                request, "error.html", {"error": "Not found.", "user": None}, status_code=404
            )
        )

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError) -> Response:
        from ytstudio.adapters.youtube import friendly_error

        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": friendly_error(exc)}, status_code=400)
        return templates.TemplateResponse(
            request, "error.html", {"error": friendly_error(exc), "user": None}, status_code=400
        )

    @app.exception_handler(Exception)
    async def crash(request: Request, exc: Exception) -> Response:
        log.exception("unhandled error on %s", request.url.path)
        ref = request_id_var.get()
        return templates.TemplateResponse(
            request,
            "error.html",
            {"error": f"Something went wrong. Reference {ref} is in the server log.", "user": None},
            status_code=500,
        )

    from ytstudio.interfaces.web import routes_auth, routes_llm, routes_media, routes_ops, routes_pages

    for r in (routes_auth, routes_pages, routes_media, routes_llm, routes_ops):
        app.include_router(r.router)
    return app
