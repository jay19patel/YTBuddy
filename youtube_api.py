"""Everything that talks to Google: credentials, channel stats, analytics."""

import json
import re
from datetime import date, timedelta

from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from db import Channel, get_session

ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
MONETARY_SCOPE = "https://www.googleapis.com/auth/yt-analytics-monetary.readonly"
SCOPES = ["https://www.googleapis.com/auth/youtube", ANALYTICS_SCOPE, MONETARY_SCOPE]
PERIODS = (7, 28, 90, 365)


def load_creds(channel_id: str) -> Credentials:
    with get_session() as db:
        ch = db.get(Channel, channel_id)
        if not ch:
            raise RuntimeError("Channel not connected. Click 'Add channel'.")
        # No scopes arg: keep what was actually granted, so refresh never asks for more.
        creds = Credentials.from_authorized_user_info(json.loads(ch.creds))
        if not creds.valid:
            creds.refresh(GoogleRequest())
            ch.creds = creds.to_json()
            db.add(ch)
            db.commit()
        return creds


def youtube(channel_id: str):
    return build("youtube", "v3", credentials=load_creds(channel_id), cache_discovery=False)


def fetch_stats(channel_ids: list[str], using: str) -> dict[str, dict]:
    """Public stats for up to 50 channels in one call; any connected channel's login can read them."""
    items = youtube(using).channels().list(part="snippet,statistics", id=",".join(channel_ids[:50])).execute()
    return {i["id"]: i for i in items.get("items", [])}


def rows(report: dict) -> list[dict]:
    names = [h["name"] for h in report["columnHeaders"]]
    return [dict(zip(names, r)) for r in report.get("rows", [])]


def fetch_analytics(channel_id: str, days: int, currency: str) -> dict:
    creds = load_creds(channel_id)
    if ANALYTICS_SCOPE not in (creds.scopes or []):
        return {"needs_reconnect": True}

    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    ya = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
    end = date.today()
    start = end - timedelta(days=days - 1)

    def query(**kw):
        return rows(ya.reports().query(ids="channel==MINE", startDate=str(start), endDate=str(end), **kw).execute(num_retries=2))

    totals = query(metrics="views,estimatedMinutesWatched,averageViewDuration,subscribersGained,subscribersLost,likes,comments,shares")
    daily = query(metrics="views,subscribersGained", dimensions="day", sort="day")
    top = query(metrics="views,estimatedMinutesWatched,averageViewDuration,likes", dimensions="video", sort="-views", maxResults=10)
    if top:
        found = yt.videos().list(part="snippet", id=",".join(r["video"] for r in top)).execute()["items"]
        titles = {v["id"]: v["snippet"]["title"] for v in found}
        for r in top:
            r["title"] = titles.get(r["video"], r["video"])

    # Non-partner channels get a 403, a 500, or all-zero rows depending on the query,
    # so "monetized" means: revenue data came back and it isn't empty.
    revenue, revenue_daily, monetized = [], [], None  # None = revenue permission not granted
    if MONETARY_SCOPE in creds.scopes:
        try:
            revenue = query(
                metrics="estimatedRevenue,estimatedAdRevenue,estimatedRedPartnerRevenue,grossRevenue,cpm,playbackBasedCpm,monetizedPlaybacks",
                currency=currency,
            )
            revenue_daily = query(metrics="estimatedRevenue", dimensions="day", sort="day", currency=currency)
        except HttpError as e:
            if e.resp.status != 403 and e.resp.status < 500:
                raise
        monetized = bool(revenue and (revenue[0]["monetizedPlaybacks"] or revenue[0]["estimatedRevenue"]))

    return {
        "totals": totals[0] if totals else {},
        "daily": daily,
        "top": top,
        "monetized": monetized,
        "revenue": revenue[0] if revenue else {},
        "revenue_daily": revenue_daily,
        "currency": currency,
    }


def friendly_error(exc: Exception) -> str:
    text = str(exc)
    if isinstance(exc, HttpError) and "accessNotConfigured" in text:
        api = "YouTube Analytics API" if "youtubeanalytics" in text else "YouTube Data API v3"
        link = re.search(r"visiting (\S+)", text)
        return (
            f"{api} is turned off in your Google Cloud project. Turn it on here, wait 2-5 minutes, then reload: "
            + (link[1] if link else "https://console.cloud.google.com/apis/library")
        )
    if isinstance(exc, HttpError) and "quotaExceeded" in text:
        return "Daily YouTube API quota used up. It resets at midnight Pacific time. Sync less often in Settings."
    if "invalid_grant" in text:
        return "Google access for this channel expired or was revoked. Remove the channel and add it again."
    return text
