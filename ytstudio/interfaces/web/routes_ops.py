"""Jobs, settings, health, and the pre-existing manual publish pages."""

import json
import os
import shutil
import tempfile
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response

from ytstudio.config import get_settings
from ytstudio.container import get_container
from ytstudio.domain.models import Schedule, SyncSettings
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
def jobs(p: Page = Depends(page)) -> Response:
    q, c = queries(), get_container()
    channel_id: str | None = p.active["id"] if p.active else None  # runs and dead letters of this channel only
    return render(
        p,
        "jobs.html",
        runs=q.runs(p.user.id, channel_id),
        dead=q.dead_letters(p.user.id, channel_id),
        costs=q.llm_costs(p.user.id),
        budget=p.user.daily_budget_usd,
        quota_used=c.quota.used_today(),
        quota_limit=get_settings().quota_daily_limit,
        jobs_status=jobs_status(),
        health=c.health(),
    )


@router.post("/jobs/runs/{run_id}/retry")
def retry_run(run_id: int, p: Page = Depends(csrf)) -> Response:
    result = get_container().runner.retry(run_id, p.user.id)
    return back("/jobs", "retried" if result.status == "running" else "already", result.run_id)


@router.get("/api/runs/{run_id}")
def run_status(run_id: int, p: Page = Depends(page)) -> Response:
    r = queries().run(p.user.id, run_id)
    if not r:
        raise HTTPException(404)
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


@router.get("/settings")
def settings_page(p: Page = Depends(page)) -> Response:
    c = get_container()
    schedule = None
    if p.active:
        with c.uow() as uow:
            schedule = uow.schedules.get(p.channel_id)
    s = get_settings()
    return render(
        p,
        "settings.html",
        settings=c.sync.settings(),
        jobs_status=jobs_status(),
        currencies=CURRENCIES,
        schedule=schedule,
        llm=c.llm_config["aliases"],
        proxy=bool(s.litellm_proxy_url),
        flags={"publish": s.enable_manual_publish, "ytdlp": s.enable_ytdlp, "auto_publish": s.enable_auto_publish},
    )


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


@router.get("/publish", dependencies=[Depends(publish_enabled)])
def publish(p: Page = Depends(page)) -> Response:
    return render(p, "publish.html") if p.active else render(p, "empty.html")


@router.post("/upload", dependencies=[Depends(publish_enabled)])
def upload(
    video: UploadFile = File(...),
    title: str = Form(...),
    description: str = Form(""),
    tags: str = Form(""),
    privacy: str = Form("private"),
    mode: str = Form("video"),
    p: Page = Depends(csrf),
) -> Response:
    if privacy not in PRIVACY:
        return render(p, "publish.html", status_code=400, error="Invalid privacy value.")
    is_short = mode == "short"
    if is_short and "#shorts" not in (title + description).lower():
        # Shorts = vertical video under 3 min; the tag helps YouTube classify it.
        description = f"{description}\n\n#Shorts".strip()
    with tempfile.NamedTemporaryFile(suffix=Path(video.filename or "video.mp4").suffix, delete=False) as tmp:
        shutil.copyfileobj(video.file, tmp)
    try:
        vid = get_container().youtube.upload(
            p.channel_id,
            tmp.name,
            {
                "snippet": {
                    "title": title[:100],
                    "description": description,
                    "tags": [t.strip() for t in tags.split(",") if t.strip()],
                    "categoryId": "22",
                },
                "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
            },
        )
    finally:
        os.unlink(tmp.name)
    kind = "Short" if is_short else "Video"
    return render(p, "publish.html", success=f"{kind} uploaded: https://youtu.be/{vid}")


@router.post("/live", dependencies=[Depends(publish_enabled)])
def live(
    title: str = Form(...),
    description: str = Form(""),
    privacy: str = Form("private"),
    start_in_minutes: int = Form(5),
    p: Page = Depends(csrf),
) -> Response:
    if privacy not in PRIVACY:
        return render(p, "publish.html", status_code=400, error="Invalid privacy value.")
    start = datetime.now(UTC) + timedelta(minutes=max(start_in_minutes, 0))
    info = get_container().youtube.create_live(p.channel_id, title, description, privacy, start)
    return render(
        p,
        "publish.html",
        success=f"Live scheduled: https://youtu.be/{info['id']}",
        live={"server": info["server"], "key": info["key"]},
    )
