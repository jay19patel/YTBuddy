"""Jobs, settings, health, and the pre-existing manual publish pages."""

import json
import os
import shutil
import tempfile
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, Response

from ytstudio.adapters.youtube import friendly_error
from ytstudio.config import get_settings
from ytstudio.container import get_container
from ytstudio.domain.errors import DomainError
from ytstudio.domain.metadata import hashtag_list
from ytstudio.domain.models import Channel, Schedule, SyncSettings
from ytstudio.domain.schedule import parse_local_datetime
from ytstudio.interfaces.web.app import Page, back, csrf, page, queries, render

router = APIRouter()
CURRENCIES = ("USD", "INR")
PRIVACY = ("private", "unlisted", "public")
JOBS = {
    "sync_stats": ("Channel stats", "Subscribers, views and videos for the sidebar."),
    "sync_analytics": ("Analytics", "Views, watch time, top videos and revenue for every period."),
}


def jobs_status() -> list[dict[str, Any]]:
    c = get_container()
    st = c.sync.settings()
    minutes = {"sync_stats": st.stats_minutes, "sync_analytics": st.analytics_minutes}
    enabled = {"sync_stats": st.stats_enabled, "sync_analytics": st.analytics_enabled}
    out = []
    for job_id, (name, about) in JOBS.items():
        raw = c.redis.get(f"yt:beat:job:{job_id}:last")
        last = json.loads(raw) if raw else None
        if last:
            last["started_at"] = datetime.fromisoformat(last["started_at"])
        nxt = last["started_at"] + timedelta(minutes=minutes[job_id]) if last else c.clock.now()
        out.append(
            {
                "id": job_id,
                "name": name,
                "about": about,
                "minutes": minutes[job_id],
                "last": last,
                "running": bool(c.redis.exists(f"yt:lock:job:{job_id}")),
                "queued": bool(c.redis.exists(f"yt:beat:job:{job_id}:queued")),
                "paused": not enabled[job_id],
                "next_run": max(nxt, c.clock.now()),
            }
        )
    return out


# ----- jobs -----


@router.get("/jobs")
def jobs(request: Request, p: Page = Depends(page)) -> Response:
    """What is running, what finished and what failed, for this channel: stop, retry, open what it made."""
    q, c = queries(), get_container()
    channel_id: str | None = p.active["id"] if p.active else None
    total = q.run_count(p.user.id, channel_id)
    pages = max(1, -(-total // RUNS_PER_PAGE))
    raw = request.query_params.get("page", "1")
    at = min(pages, max(1, int(raw) if raw.isdigit() else 1))
    return render(
        p,
        "jobs.html",
        runs=q.runs(p.user.id, channel_id, RUNS_PER_PAGE, (at - 1) * RUNS_PER_PAGE),
        page_at=at,
        pages=pages,
        total=total,
        jobs_status=jobs_status(),
        health=c.health(),
    )


RUNS_PER_PAGE = 15


@router.post("/jobs/runs/{run_id}/stop")
def stop_run(run_id: int, next: str = Form("/jobs"), p: Page = Depends(csrf)) -> Response:
    stopped = get_container().runner.stop(run_id, p.user.id)
    dest = next if next.startswith("/") and not next.startswith("//") else "/jobs"
    return back(dest, "stopped" if stopped else "not_running")


@router.post("/jobs/runs/{run_id}/retry")
def retry_run(run_id: int, p: Page = Depends(csrf)) -> Response:
    result = get_container().runner.retry(run_id, p.user.id)
    return back("/jobs", "retried" if result.status == "running" else "already", result.run_id)


@router.get("/api/runs/{run_id}")
def run_status(run_id: int, p: Page = Depends(page)) -> Response:
    r = queries().run(p.user.id, run_id)
    if not r:
        raise HTTPException(404)
    live = get_container().progress_of(run_id)  # what a long step is doing now: "frame 3,210 of 9,000"
    for s in r["steps"]:
        s["live"] = live.get(s["step"]) if s["status"] in ("running", "waiting") else None
    return JSONResponse(jsonable_encoder(r), headers={"Cache-Control": "no-store"})


@router.get("/health")
def health() -> Response:
    report = get_container().health()
    ok = report["postgres"]["ok"] and report["redis"]["ok"]
    return JSONResponse(report, status_code=200 if ok else 503)


@router.get("/health/{part}")
def health_part(part: str) -> Response:
    report = get_container().health()
    if part not in report:
        raise HTTPException(404)
    return JSONResponse(report[part], status_code=200 if report[part]["ok"] else 503)


# ----- settings -----


def settings_context(p: Page, channel: Channel | None = None, schedule: Schedule | None = None) -> dict[str, Any]:
    """Everything the Settings page shows. A draft channel/schedule (not yet saved) can be passed in."""
    c = get_container()
    if p.active and not (channel and schedule):
        with c.uow() as uow:
            schedule = schedule or uow.schedules.get(p.channel_id)
            channel = channel or uow.channels.get(p.channel_id)
    s = get_settings()
    return {
        "settings": c.sync.settings(),
        "jobs_status": jobs_status(),
        "currencies": CURRENCIES,
        "schedule": schedule,
        "channel": channel,
        "flags": {"publish": s.enable_manual_publish},
    }


@router.get("/settings")
def settings_page(p: Page = Depends(page)) -> Response:
    return render(p, "settings.html", **settings_context(p))


@router.post("/settings/generate")
def generate_profile(brief: str = Form(""), p: Page = Depends(csrf)) -> Response:
    """AI drafts the About text, keywords and channel profile from the owner's plain words. Nothing is saved:
    the draft fills the forms below, and each form's own Save button keeps it."""
    try:
        channel, schedule = get_container().profiles.from_brief(p.user.id, p.channel_id, brief)
    except DomainError as e:
        return render(p, "settings.html", status_code=400, error=friendly_error(e), brief=brief, **settings_context(p))
    note = (
        "Done. Nothing is saved yet. Read the About box and the daily-brief box below, change anything you like, "
        "then press each box's save button."
    )
    return render(p, "settings.html", success=note, brief=brief, **settings_context(p, channel, schedule))


@router.post("/settings")
def save_settings(
    stats_minutes: int = Form(...),
    analytics_minutes: int = Form(...),
    stats_enabled: bool = Form(False),
    analytics_enabled: bool = Form(False),
    currency: str = Form("USD"),
    p: Page = Depends(csrf),
) -> Response:
    c = get_container()
    changed = c.sync.save_settings(
        SyncSettings(stats_minutes, analytics_minutes, stats_enabled, analytics_enabled, currency), CURRENCIES
    )
    if changed:  # cached revenue is in the old currency
        enqueue_job("sync_analytics")
    return back("/settings", "saved")


def enqueue_job(job_id: str) -> None:
    from ytstudio.interfaces.worker import run_job_now

    get_container().redis.set(f"yt:beat:job:{job_id}:queued", "1", ex=600)
    run_job_now.apply_async(args=(job_id,), queue="io")


@router.post("/settings/about")
def save_about(description: str = Form(""), keywords: str = Form(""), p: Page = Depends(csrf)) -> Response:
    """Push to YouTube first; the local copy changes only if YouTube accepted it."""
    if len(description) > 1000 or len(keywords) > 500:
        raise HTTPException(400, "YouTube allows at most 1000 characters of description and 500 of keywords.")
    c = get_container()
    c.youtube.update_about(p.channel_id, description, keywords)
    with c.uow() as uow:
        uow.channels.set_about(p.channel_id, description, keywords)
        uow.commit()
    return back("/settings", "about_saved")


@router.post("/settings/run/{job_id}")
def run_job(job_id: str, p: Page = Depends(csrf)) -> Response:
    if job_id in JOBS:
        enqueue_job(job_id)
    return back("/settings")


@router.post("/settings/schedule")
def save_schedule(
    enabled: bool = Form(False),
    timezone: str = Form("UTC"),
    run_time: str = Form("07:00"),
    days: list[str] = Form([]),
    language: str = Form("en"),
    region: str = Form("US"),
    niche: str = Form(""),
    custom_topics: str = Form(""),
    target_minutes: int = Form(8),
    voice_md: str = Form(""),
    shorts_enabled: bool = Form(False),
    p: Page = Depends(csrf),
) -> Response:
    hh, mm = (int(x) for x in run_time.split(":")[:2])
    get_container().scheduling.save(
        p.user.id,
        Schedule(
            channel_id=p.channel_id,
            enabled=enabled,
            timezone=timezone.strip(),
            run_time=time(hh, mm),
            days="".join(days),
            language=language if language in ("en", "hi", "hinglish") else "en",
            region=region.strip().upper()[:2] or "US",
            niche=niche.strip()[:300],
            custom_topics=[t.strip() for t in custom_topics.splitlines() if t.strip()][:20],
            target_minutes=min(max(target_minutes, 1), 120),
            voice_md=voice_md[:20000],
            shorts_enabled=shorts_enabled,
        ),
    )
    return back("/settings", "saved")


@router.post("/settings/detect")
def detect(p: Page = Depends(csrf)) -> Response:
    from ytstudio.interfaces.worker import detect_profile

    detect_profile.apply_async(args=(p.channel_id,), queue="llm")
    return back("/settings", "connected")


@router.post("/settings/profile")
def save_profile(
    daily_budget_usd: float = Form(0.0),
    p: Page = Depends(csrf),
) -> Response:
    c = get_container()
    with c.uow() as uow:
        u = uow.users.get(p.user.id)
        u.daily_budget_usd = min(max(daily_budget_usd, 0.0), 1000.0)
        uow.users.update(u)
        uow.commit()
    return back("/settings", "saved")


# ----- manual publish (pre-existing feature; uploads are always started by you) -----


def publish_enabled() -> None:
    if not get_settings().enable_manual_publish:
        raise HTTPException(404)


def publish_context(p: Page) -> dict[str, Any]:
    with get_container().uow() as uow:
        tz = uow.schedules.get(p.channel_id).timezone
    return {"drafts": queries().drafts(p.user.id, p.channel_id), "tz": tz}


@router.get("/publish", dependencies=[Depends(publish_enabled)])
def publish(p: Page = Depends(page)) -> Response:
    return render(p, "publish.html", **publish_context(p)) if p.active else render(p, "empty.html")


@router.post("/upload", dependencies=[Depends(publish_enabled)])
def upload(
    video: UploadFile = File(...),
    title: str = Form(...),
    description: str = Form(""),
    tags: str = Form(""),
    hashtags: str = Form(""),
    thumb_key: str = Form(""),
    privacy: str = Form("private"),
    mode: str = Form("video"),
    publish_at: str = Form(""),
    p: Page = Depends(csrf),
) -> Response:
    """A video made elsewhere, uploaded by the same rules as one made here (see Shorts._send)."""
    ctx = publish_context(p)
    when = parse_local_datetime(publish_at, ctx["tz"], datetime.now(UTC)) if publish_at.strip() else None
    is_short = mode == "short"
    with tempfile.NamedTemporaryFile(suffix=Path(video.filename or "video.mp4").suffix, delete=False) as tmp:
        shutil.copyfileobj(video.file, tmp)
    try:
        sent = get_container().shorts.upload_file(
            p.user.id,
            p.channel_id,
            Path(tmp.name),
            title,
            description,
            [t.strip() for t in tags.split(",") if t.strip()],
            hashtag_list(hashtags),
            privacy,
            when,
            is_short,
            thumb_key.strip() or None,
        )
    except DomainError as e:
        return render(p, "publish.html", status_code=400, error=str(e), **ctx)
    finally:
        os.unlink(tmp.name)
    kind = "Short" if is_short else "Video"
    planned = f" and scheduled for {publish_at.strip().replace('T', ' ')} ({ctx['tz']})" if when else ""
    note = f" The thumbnail was not set: {sent['thumb_error']}" if sent.get("thumb_error") else ""
    return render(
        p, "publish.html", success=f"{kind} uploaded{planned}: https://youtu.be/{sent['youtube_id']}.{note}", **ctx
    )


def _thumb_json(out: dict[str, Any]) -> JSONResponse:
    return JSONResponse(out | {"thumb_url": f"/files/thumb?key={out['thumb_key']}"})


@router.post("/publish/write", dependencies=[Depends(publish_enabled)])
def publish_write(
    about: str = Form(""),
    title: str = Form(""),
    mode: str = Form("video"),
    language: str = Form(""),
    old: str = Form(""),
    p: Page = Depends(csrf),
) -> Response:
    """Write with AI: title, description, tags, hashtags and a thumbnail from your words about the video."""
    try:
        out = get_container().packaging.write(p.user.id, p.channel_id, about, title, mode == "short", language, old)
    except DomainError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    return _thumb_json(out)


@router.post("/publish/thumbnail", dependencies=[Depends(publish_enabled)])
def publish_thumbnail(
    title: str = Form(""),
    about: str = Form(""),
    text: str = Form(""),
    notes: str = Form(""),
    mode: str = Form("video"),
    old: str = Form(""),
    p: Page = Depends(csrf),
) -> Response:
    """Only a new thumbnail, with your text and instructions if given."""
    try:
        out = get_container().packaging.redesign(
            p.user.id, p.channel_id, title, about, text, notes, mode == "short", old
        )
    except DomainError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    return _thumb_json(out)


@router.post("/publish/thumbnail/own", dependencies=[Depends(publish_enabled)])
def publish_own_thumbnail(image: UploadFile = File(...), old: str = Form(""), p: Page = Depends(csrf)) -> Response:
    """Your own thumbnail image instead of the drawn one."""
    with tempfile.NamedTemporaryFile(suffix=Path(image.filename or "thumb.jpg").suffix, delete=False) as tmp:
        shutil.copyfileobj(image.file, tmp)
    try:
        key = get_container().packaging.own_image(p.channel_id, Path(tmp.name), old)
    except DomainError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    finally:
        os.unlink(tmp.name)
    return _thumb_json({"thumb_key": key})


@router.get("/files/thumb")
def thumb_file(key: str, p: Page = Depends(page)) -> Response:
    """A thumbnail drawn on the Publish page; only this channel's."""
    if not key.startswith(f"thumbs/{p.channel_id}/upload-") or ".." in key:
        raise HTTPException(404)
    c = get_container()
    if not c.storage.exists(key):
        raise HTTPException(404)
    return FileResponse(c.storage.get(key))


@router.post("/live", dependencies=[Depends(publish_enabled)])
def live(
    title: str = Form(...),
    description: str = Form(""),
    privacy: str = Form("private"),
    start_in_minutes: int = Form(5),
    p: Page = Depends(csrf),
) -> Response:
    if privacy not in PRIVACY:
        return render(p, "publish.html", status_code=400, error="Invalid privacy value.", **publish_context(p))
    start = datetime.now(UTC) + timedelta(minutes=max(start_in_minutes, 0))
    info = get_container().youtube.create_live(p.channel_id, title, description, privacy, start)
    return render(
        p,
        "publish.html",
        success=f"Live scheduled: https://youtu.be/{info['id']}",
        live={"server": info["server"], "key": info["key"]},
    )
