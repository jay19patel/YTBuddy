import json
import logging
import os
import shutil
import tempfile
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from sqlmodel import delete, select

import jobs
from db import AnalyticsCache, Channel, Settings, Snapshot, get_session, get_settings, init_db
from youtube_api import PERIODS, SCOPES, friendly_error, youtube

# Google allows http only for localhost redirects; oauthlib needs this flag for that.
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

BASE = Path(__file__).parent
CLIENT_SECRET = BASE / "YTStudioCred.json"
REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:8000/oauth2callback")
PRIVACY = ("private", "unlisted", "public")
CURRENCIES = ("USD", "INR")
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    jobs.start()
    yield
    jobs.scheduler.shutdown(wait=False)


def ago(dt: datetime | None) -> str:
    if not dt:
        return "never"
    secs = int((dt - datetime.now(timezone.utc)).total_seconds())
    m = abs(secs) // 60
    span = "under a minute" if m < 1 else f"{m} min" if m < 60 else f"{m // 60} h {m % 60} min" if m < 1440 else f"{m // 1440} days"
    return f"in {span}" if secs > 0 else f"{span} ago"


app = FastAPI(title="YTStudio", lifespan=lifespan)
templates = Jinja2Templates(directory=BASE / "templates")
templates.env.filters["num"] = lambda n, digits=0: f"{float(n or 0):,.{digits}f}"
templates.env.filters["short"] = lambda n: next(
    (f"{n / d:.1f}".rstrip("0").rstrip(".") + u for d, u in ((1e9, "B"), (1e6, "M"), (1e3, "K")) if abs(n) >= d), f"{n:,}"
)
templates.env.filters["dur"] = lambda s: f"{int(s or 0) // 60}:{int(s or 0) % 60:02d}"
templates.env.filters["ago"] = ago
templates.env.filters["clock"] = lambda dt: dt.astimezone().strftime("%-d %b, %H:%M") if dt else "-"
pending_flows: dict[str, Flow] = {}  # state -> flow, keeps the PKCE verifier for the callback


def import_old_tokens():
    """One-time move from the old tokens/<channel_id>.json files into the database."""
    with get_session() as db:
        for path in (BASE / "tokens").glob("*.json"):
            data = json.loads(path.read_text())
            if not db.get(Channel, data["id"]):
                db.add(Channel(id=data["id"], title=data["title"], thumb=data["thumb"], creds=json.dumps(data["creds"])))
        db.commit()


init_db()
import_old_tokens()


def accounts() -> list[Channel]:
    with get_session() as db:
        return list(db.exec(select(Channel).order_by(Channel.title)))


def changes(channels: list[Channel]) -> dict[str, dict]:
    """Change since the latest snapshot before today, per channel."""
    today = date.today()
    with get_session() as db:
        past = db.exec(select(Snapshot).where(Snapshot.day < today, Snapshot.day >= today - timedelta(days=60)).order_by(Snapshot.day))
        base = {s.channel_id: s for s in past}  # ordered by day, so the latest one wins
    return {
        c.id: {"since": base[c.id].day, "subscribers": c.subscribers - base[c.id].subscribers, "views": c.views - base[c.id].views}
        for c in channels
        if c.id in base
    }


def active_id(request: Request) -> str | None:
    ids = [a.id for a in accounts()]
    active = request.cookies.get("channel")
    return active if active in ids else (ids[0] if ids else None)


def render(request: Request, status_code: int = 200, **ctx):
    active = active_id(request)
    accs = accounts()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "accounts": accs,
            "changes": changes(accs),
            "today": date.today(),
            "active": active,
            "has_secret": CLIENT_SECRET.exists(),
            "stats_job": jobs.status()[0],
            **ctx,
        },
        status_code=status_code,
    )


@app.get("/")
def index(request: Request):
    return render(request)


@app.get("/auth")
def auth():
    flow = Flow.from_client_secrets_file(CLIENT_SECRET, SCOPES, redirect_uri=REDIRECT_URI)
    # prompt=select_account+consent: lets you pick a different Google account/channel each time
    url, state = flow.authorization_url(access_type="offline", prompt="select_account consent")
    pending_flows[state] = flow
    return RedirectResponse(url)


@app.get("/oauth2callback")
def oauth2callback(request: Request, state: str):
    flow = pending_flows.pop(state, None)
    if not flow:
        return render(request, error="Login session expired, try again.")
    flow.fetch_token(authorization_response=str(request.url))
    creds = flow.credentials
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    items = yt.channels().list(part="snippet", mine=True).execute().get("items", [])
    if not items:
        return render(request, error="This Google account has no YouTube channel. Create one on youtube.com first.")
    ch = items[0]
    with get_session() as db:
        db.merge(Channel(
            id=ch["id"],
            title=ch["snippet"]["title"],
            thumb=ch["snippet"]["thumbnails"]["default"]["url"],
            creds=creds.to_json(),
        ))
        db.commit()
    for job_id in jobs.JOBS:  # fill the sidebar and analytics for the new channel right away
        jobs.run_now(job_id)
    resp = RedirectResponse("/")
    resp.set_cookie("channel", ch["id"])
    return resp


@app.get("/use/{channel_id}")
def use(request: Request, channel_id: str):
    # Stay on the same page (Publish or Analytics); path only, so no redirect off-site.
    path = urlparse(request.headers.get("referer", "/")).path
    resp = RedirectResponse(path if path in ("/", "/analytics") else "/")
    resp.set_cookie("channel", channel_id)
    return resp


@app.get("/remove/{channel_id}")
def remove(channel_id: str):
    with get_session() as db:
        if ch := db.get(Channel, channel_id):
            db.delete(ch)
        db.exec(delete(Snapshot).where(Snapshot.channel_id == channel_id))
        db.exec(delete(AnalyticsCache).where(AnalyticsCache.channel_id == channel_id))
        db.commit()
    return RedirectResponse("/")


@app.post("/upload")
def upload(
    request: Request,
    channel_id: str = Form(...),
    video: UploadFile = File(...),
    title: str = Form(...),
    description: str = Form(""),
    tags: str = Form(""),
    privacy: str = Form("private"),
    mode: str = Form("video"),
):
    if privacy not in PRIVACY:
        return render(request, error="Invalid privacy value.")
    is_short = mode == "short"
    if is_short and "#shorts" not in (title + description).lower():
        # Shorts = vertical video under 3 min; the tag helps YouTube classify it.
        description = f"{description}\n\n#Shorts".strip()

    yt = youtube(channel_id)
    suffix = Path(video.filename or "video.mp4").suffix
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        shutil.copyfileobj(video.file, tmp)
    try:
        body = {
            "snippet": {
                "title": title[:100],
                "description": description,
                "tags": [t.strip() for t in tags.split(",") if t.strip()],
                "categoryId": "22",
            },
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
        }
        media = MediaFileUpload(tmp.name, chunksize=8 * 1024 * 1024, resumable=True)
        req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
        resp = None
        while resp is None:
            _, resp = req.next_chunk()
    finally:
        os.unlink(tmp.name)

    kind = "Short" if is_short else "Video"
    return render(request, success=f"{kind} uploaded: https://youtu.be/{resp['id']}")


@app.post("/live")
def live(
    request: Request,
    channel_id: str = Form(...),
    title: str = Form(...),
    description: str = Form(""),
    privacy: str = Form("private"),
    start_in_minutes: int = Form(5),
):
    if privacy not in PRIVACY:
        return render(request, error="Invalid privacy value.")
    yt = youtube(channel_id)
    start = (datetime.now(timezone.utc) + timedelta(minutes=max(start_in_minutes, 0))).isoformat()

    broadcast = yt.liveBroadcasts().insert(
        part="snippet,status,contentDetails",
        body={
            "snippet": {"title": title, "description": description, "scheduledStartTime": start},
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
            # Goes live as soon as OBS starts sending, ends when OBS stops.
            "contentDetails": {"enableAutoStart": True, "enableAutoStop": True},
        },
    ).execute()
    stream = yt.liveStreams().insert(
        part="snippet,cdn",
        body={
            "snippet": {"title": title},
            "cdn": {"ingestionType": "rtmp", "resolution": "variable", "frameRate": "variable"},
        },
    ).execute()
    yt.liveBroadcasts().bind(part="id,contentDetails", id=broadcast["id"], streamId=stream["id"]).execute()

    ingest = stream["cdn"]["ingestionInfo"]
    return render(
        request,
        success=f"Live scheduled: https://youtu.be/{broadcast['id']}",
        live={"server": ingest["ingestionAddress"], "key": ingest["streamName"]},
    )


@app.get("/analytics")
def analytics(request: Request, days: int = 28):
    channel_id = active_id(request)
    if not channel_id:
        return RedirectResponse("/")
    days = days if days in PERIODS else 28
    with get_session() as db:
        cached = db.get(AnalyticsCache, (channel_id, days))
    job = jobs.status()[1]
    if not cached:
        return render(request, view="analytics", days=days, job=job, not_synced=True)
    data = json.loads(cached.data)
    if "error" in data:
        data = {"sync_error": data.pop("error")}
    return render(request, view="analytics", days=days, job=job, fetched_at=cached.fetched_at, **data)


@app.get("/settings")
def settings_page(request: Request, saved: bool = False):
    return render(
        request, view="settings", settings=get_settings(), jobs_status=jobs.status(),
        currencies=CURRENCIES, success="Settings saved." if saved else None,
    )


@app.post("/settings")
def save_settings(
    stats_minutes: int = Form(...),
    analytics_minutes: int = Form(...),
    stats_enabled: bool = Form(False),
    analytics_enabled: bool = Form(False),
    currency: str = Form("USD"),
):
    with get_session() as db:
        get_settings()  # make sure the row exists
        s = db.get(Settings, 1)
        old_currency = s.currency
        s.stats_minutes = min(max(stats_minutes, 1), 1440)
        s.analytics_minutes = min(max(analytics_minutes, 5), 1440)
        s.stats_enabled, s.analytics_enabled = stats_enabled, analytics_enabled
        s.currency = currency if currency in CURRENCIES else "USD"
        db.add(s)
        db.commit()
        currency_changed = s.currency != old_currency
    jobs.apply_settings()
    if currency_changed:  # cached revenue is in the old currency
        jobs.run_now("sync_analytics")
    return RedirectResponse("/settings?saved=1", status_code=303)


@app.post("/settings/run/{job_id}")
def run_job_now(job_id: str):
    if job_id in jobs.JOBS:
        jobs.run_now(job_id)
    return RedirectResponse("/settings", status_code=303)


@app.exception_handler(Exception)
def on_error(request: Request, exc: Exception):
    # Show API errors (quota, live not enabled, expired token...) on the page instead of a bare 500.
    return render(request, status_code=500, error=friendly_error(exc))


def main():
    import uvicorn

    uvicorn.run("main:app", host="localhost", port=8000, reload=True)


if __name__ == "__main__":
    main()
