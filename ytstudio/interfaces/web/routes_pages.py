"""Today, ideas backlog, asset/script editor, insights, trends."""

import json
import shlex
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from ytstudio.container import get_container
from ytstudio.domain.metrics import median_views
from ytstudio.domain.models import AssetStatus, RetentionPoint, Video
from ytstudio.domain.video import VOICES, script_language, spoken_text, voice_for, voices_for
from ytstudio.interfaces.web.app import Page, asset_format, back, csrf, page, queries, render
from ytstudio.interfaces.web.routes_auth import cookie

router = APIRouter()
PERIODS = (7, 28, 90, 365)


def started(url: str, result: Any) -> Response:
    msg = {"running": "started", "skipped": "busy", "done": "done_already"}.get(str(result.status), "already")
    return back(url, msg, result.run_id)


@router.get("/")
def home() -> Response:
    return RedirectResponse("/insights", status_code=303)


@router.get("/today")
def today(request: Request, p: Page = Depends(page)) -> Response:
    if not p.active:
        return render(p, "empty.html")
    q = queries()
    run_id = request.query_params.get("run")
    with get_container().uow() as uow:
        schedule = uow.schedules.get(p.channel_id)
    return render(
        p,
        "today.html",
        data=q.latest_brief(p.user.id, p.channel_id),
        schedule=schedule,
        run=(q.run(p.user.id, int(run_id)) if run_id and run_id.isdigit() else None)
        or q.latest_run(p.user.id, p.channel_id),
    )


@router.post("/today/run")
def run_now(p: Page = Depends(csrf)) -> Response:
    return started("/today", get_container().scheduling.run_now(p.user.id, p.channel_id))


def channel_language(channel_id: str) -> str:
    with get_container().uow() as uow:
        return str(uow.schedules.get(channel_id).language)


def by_format(rows: list[dict[str, Any]], fmt: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Rows of one type, and how many of each type there are (for the Long videos / Shorts toggle)."""
    counts = {"long": 0, "short": 0}
    for r in rows:
        counts[asset_format(r["data"])] += 1
    return [r for r in rows if asset_format(r["data"]) == fmt], counts


@router.get("/ideas")
def ideas(request: Request) -> Response:
    """Ideas live on the Scripts page now (an idea without a script yet shows there with a Write button)."""
    fmt = "short" if request.query_params.get("format") == "short" else "long"
    return RedirectResponse(f"/scripts?format={fmt}", status_code=303)


@router.get("/scripts")
def scripts(request: Request, p: Page = Depends(page)) -> Response:
    if not p.active:
        return render(p, "empty.html")
    status = request.query_params.get("status") or None
    fmt = "short" if request.query_params.get("format") == "short" else "long"
    q = queries()
    scripts_ = q.assets(p.user.id, p.channel_id, ["script"], status=status)
    scripted = {r["parent_id"] for r in q.assets(p.user.id, p.channel_id, ["script"])}
    waiting = [i for i in q.assets(p.user.id, p.channel_id, ["idea"], status=status) if i["id"] not in scripted]
    both = sorted(scripts_ + waiting, key=lambda r: r["created_at"], reverse=True)  # scripts and ideas, newest first
    rows, counts = by_format(both, fmt)
    return render(
        p,
        "scripts.html",
        scripts=rows,
        status=status,
        fmt=fmt,
        counts=counts,
        channel_language=channel_language(p.channel_id),
    )


@router.get("/scripts/new")
def new_script_form(request: Request, p: Page = Depends(page)) -> Response:
    if not p.active:
        return render(p, "empty.html")
    fmt = "short" if request.query_params.get("format") == "short" else "long"
    return render(p, "script_new.html", fmt=fmt, idea=None, run=None, lang=channel_language(p.channel_id))


@router.post("/scripts/new")
def new_script(
    title: str = Form(""),
    details: str = Form(""),
    format: str = Form("long"),
    language: str = Form(""),
    p: Page = Depends(csrf),
) -> Response:
    """Your own idea straight to a script: saved as a manual idea (so the script has its package and SEO like any
    other), developed at once, and the page follows the run until the script opens."""
    c = get_container()
    idea = c.review.add_idea(p.user.id, p.channel_id, title, details, format=format, language=language)
    assert idea.id is not None
    return started(f"/scripts/new/{idea.id}", c.review.develop(p.user.id, idea.id))


@router.get("/scripts/new/{idea_id}")
def new_script_progress(idea_id: int, request: Request, p: Page = Depends(page)) -> Response:
    q = queries()
    idea = q.asset(p.user.id, idea_id)
    if not idea or idea["kind"] != "idea":
        raise HTTPException(404)
    if script := q.developed(p.user.id, idea["channel_id"], idea_id).get("script"):
        msg = request.query_params.get("m")  # "already done" still says so on the script; "started" is over by now
        return back(f"/assets/{script['id']}", msg if msg and msg != "started" else None)
    run_id = request.query_params.get("run")
    run = (q.run(p.user.id, int(run_id)) if run_id and run_id.isdigit() else None) or q.run_for_asset(p.user.id, idea)
    lang = script_language(idea["data"], channel_language(idea["channel_id"]))
    return render(p, "script_new.html", fmt=asset_format(idea["data"]), idea=idea, run=run, lang=lang)


@router.post("/scripts/{script_id}/video")
def make_video(script_id: int, voice: str = Form(""), p: Page = Depends(csrf)) -> Response:
    return started(f"/assets/{script_id}", get_container().review.make_video(p.user.id, script_id, voice))


@router.post("/scripts/{script_id}/thumbnail")
def make_thumbnail(script_id: int, text: str = Form(""), notes: str = Form(""), p: Page = Depends(csrf)) -> Response:
    """Only the thumbnail again; the video stays as it is."""
    return started(f"/assets/{script_id}", get_container().review.make_thumbnail(p.user.id, script_id, text, notes))


@router.post("/ideas/{idea_id}/develop")
def develop(idea_id: int, language: str = Form(""), p: Page = Depends(csrf)) -> Response:
    """Write the script for an idea; the page follows the run and opens the script when it is ready."""
    return started(f"/scripts/new/{idea_id}", get_container().review.develop(p.user.id, idea_id, language))


def upload_context(p: Page, a: dict[str, Any]) -> dict[str, Any]:
    """What the upload form starts with: a Short's own title and description; for a long video the best title from its
    package and the description, tags and hashtags from its SEO (they hang under the same idea as the script). The
    copyright line, credits and disclaimer are added when it is uploaded, the same for every video."""
    d = a["data"]
    with get_container().uow() as uow:
        tz = uow.schedules.get(a["channel_id"]).timezone
        keywords = uow.channels.get(a["channel_id"]).keywords
    if asset_format(d) == "short":
        try:
            extra = shlex.split(keywords)[:6]
        except ValueError:
            extra = []
        prefill = {
            "title": d.get("title") or a["title"],
            "description": d.get("description", ""),
            "tags": ", ".join([*d.get("tags", []), "Shorts", *extra]),
            "hashtags": " ".join(d.get("hashtags", [])),
        }
    else:
        made = queries().developed(p.user.id, a["channel_id"], a["parent_id"]) if a["parent_id"] else {}
        pairs = (made.get("package") or {}).get("data", {}).get("pairs") or []
        seo = (made.get("seo") or {}).get("data", {})
        prefill = {
            "title": pairs[0]["title"] if pairs else a["title"],
            "description": seo.get("description", ""),
            "tags": ", ".join(seo.get("tags", [])),
            "hashtags": " ".join(seo.get("hashtags", [])),
        }
    return {"prefill": prefill, "tz": tz}


@router.get("/assets/{asset_id}")
def asset(request: Request, asset_id: int, p: Page = Depends(page)) -> Response:
    a = queries().asset(p.user.id, asset_id)
    if not a:
        raise HTTPException(404)
    q = queries()
    own = next((ch for ch in p.channels if ch["id"] == a["channel_id"]), None)
    if not own:  # its channel was disconnected
        raise HTTPException(404)
    switched = not p.active or p.active["id"] != own["id"]
    p.active = own  # opened from a link or bookmark of another channel: show it under its own channel
    developed = q.developed(p.user.id, a["channel_id"], a["id"]) if a["kind"] == "idea" else {}
    extra = upload_context(p, a) if a["kind"] == "script" else {}
    default_lang = channel_language(a["channel_id"])
    if a["kind"] == "script":  # the voices that fit the script's own words, and the one chosen when left on automatic
        text = spoken_text(a["data"])
        extra |= {
            "script_voices": voices_for(text),
            "auto_voice": next(v.label for v in VOICES if v.id == voice_for(text)),
            "script_lang": script_language(a["data"], default_lang),
        }
    run_id = request.query_params.get("run")
    resp = render(
        p,
        "asset.html",
        a=a,
        developed=developed,
        channel_language=default_lang,
        run=(q.run(p.user.id, int(run_id)) if run_id and run_id.isdigit() else None) or q.run_for_asset(p.user.id, a),
        **extra,
    )
    if switched:
        cookie(resp, "channel", own["id"], 365 * 86400)
    return resp


@router.post("/assets/{asset_id}/status")
def set_status(asset_id: int, status: str = Form(...), next: str = Form("/today"), p: Page = Depends(csrf)) -> Response:
    get_container().review.set_status(p.user.id, asset_id, AssetStatus(status))
    return back(
        next if next.startswith("/") and not next.startswith("//") else "/today",
        {"approved": "approved", "discarded": "discarded"}.get(status),
    )


@router.post("/assets/{asset_id}/delete")
def delete_asset(asset_id: int, next: str = Form("/today"), p: Page = Depends(csrf)) -> Response:
    gone = get_container().review.delete(p.user.id, asset_id)
    dest = next if next.startswith("/") and not next.startswith("//") else "/today"
    if dest.startswith(f"/assets/{asset_id}"):  # that page no longer exists: go to the list it belonged to
        dest = "/scripts" if gone.kind in ("idea", "script") else "/today"
    return back(dest, "deleted")


@router.post("/assets/{asset_id}/edit")
async def edit(request: Request, asset_id: int, p: Page = Depends(csrf)) -> Response:
    form = await request.form()
    changes: dict[str, Any] = {}
    for key, value in form.multi_items():
        if key in ("csrf", "next"):
            continue
        value = str(value)
        if key in ("tags", "target_queries"):
            changes[key] = [v.strip() for v in value.split(",") if v.strip()]
        elif key in ("pairs", "script"):
            try:
                changes[key] = json.loads(value)
            except json.JSONDecodeError as e:
                raise HTTPException(400, f"{key} must be valid JSON") from e
        elif key.startswith("section_"):  # script editor: one textarea per section
            changes.setdefault("_sections", {})[int(key.split("_")[1])] = value
        elif key in ("start", "end"):
            changes[key] = float(value)
        else:
            changes[key] = value
    if sections := changes.pop("_sections", None):
        a = queries().asset(p.user.id, asset_id)
        if not a:
            raise HTTPException(404)
        script = a["data"]["script"]
        for i, text in sections.items():
            script["sections"][i]["spoken"] = text
        changes["script"] = script
    get_container().review.edit(p.user.id, asset_id, changes)
    nxt = str(form.get("next") or f"/assets/{asset_id}")
    return back(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/today", "edited")


@router.post("/assets/{asset_id}/regenerate")
def regenerate(
    asset_id: int,
    next: str = Form("/today"),
    language: str = Form(""),
    title: str = Form(""),
    details: str | None = Form(None),
    p: Page = Depends(csrf),
) -> Response:
    result = get_container().review.regenerate(p.user.id, asset_id, language, title, details)
    return started(next if next.startswith("/") and not next.startswith("//") else "/today", result)


@router.post("/api/hooks/score")
async def score_hooks(request: Request, p: Page = Depends(csrf)) -> Response:
    body = await request.json()
    hooks = [str(h)[:400] for h in body.get("hooks", []) if str(h).strip()][:10]
    return JSONResponse(
        {
            "scores": get_container().scorers["hooks"].score(hooks),
            "note": "Hook panel score: a heuristic, not a prediction.",
        }
    )


@router.get("/analytics")
def analytics_legacy() -> Response:
    return RedirectResponse("/insights", status_code=303)


@router.get("/insights")
def insights(request: Request, days: int = 28, p: Page = Depends(page)) -> Response:
    if not p.active:
        return render(p, "empty.html")
    days = days if days in PERIODS else 28
    with get_container().uow() as uow:
        cached = uow.channels.report(p.channel_id, days)
    videos = queries().videos(p.channel_id)
    med = (
        median_views(
            [
                Video(id=v["id"], channel_id="", title="", published_at=v["published_at"], views=v["views"])
                for v in videos
                if v["duration_s"] > 180
            ]
        )
        or None
    )
    for v in videos:
        v["multiple"] = round(v["views"] / med, 2) if med else None
    ctx: dict[str, Any] = {"days": days, "videos": videos, "median": med}
    if not cached:
        return render(p, "insights.html", not_synced=True, **ctx)
    data, fetched_at = cached
    if "error" in data:
        data = {"sync_error": data.pop("error")}
    return render(p, "insights.html", fetched_at=fetched_at, **ctx, **data)


@router.get("/insights/video/{video_id}")
def video_insight(video_id: str, p: Page = Depends(page)) -> Response:
    v = queries().video(p.channel_id, video_id)
    if not v:
        raise HTTPException(404)
    points = [RetentionPoint(**r) for r in (v.get("retention") or [])]
    analysis = get_container().scorers["retention"].analyze(points, v["duration_s"]) if points else None
    return render(p, "video.html", v=v, points=points, analysis=analysis)


@router.get("/trends")
def trends(request: Request, p: Page = Depends(page)) -> Response:
    if not p.active:
        return render(p, "empty.html")
    with get_container().uow() as uow:
        rows = uow.trends.for_day(p.channel_id)
    provider = request.query_params.get("provider") or ""
    try:
        min_x = float(request.query_params.get("min") or 0)
    except ValueError:
        min_x = 0.0
    shown = [r for r in rows if (not provider or r.get("provider") == provider) and r.get("multiple", 0) >= min_x]
    return render(
        p,
        "trends.html",
        rows=shown,
        total=len(rows),
        provider=provider,
        min_x=min_x,
        providers=sorted({r.get("provider", "") for r in rows}),
        day=rows[0]["day"] if rows else None,
    )
