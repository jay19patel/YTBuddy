"""Today, ideas backlog, asset/script editor, insights, trends."""

import json
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from ytstudio.container import get_container
from ytstudio.domain.metrics import median_views
from ytstudio.domain.models import AssetStatus, RetentionPoint, Video
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


def by_format(rows: list[dict[str, Any]], fmt: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Rows of one type, and how many of each type there are (for the Long videos / Shorts toggle)."""
    counts = {"long": 0, "short": 0}
    for r in rows:
        counts[asset_format(r["data"])] += 1
    return [r for r in rows if asset_format(r["data"]) == fmt], counts


@router.get("/ideas")
def ideas(request: Request, p: Page = Depends(page)) -> Response:
    if not p.active:
        return render(p, "empty.html")
    status = request.query_params.get("status") or None
    fmt = "short" if request.query_params.get("format") == "short" else "long"
    rows, counts = by_format(queries().assets(p.user.id, p.channel_id, ["idea"], status=status), fmt)
    scripted = {r["parent_id"]: r["id"] for r in reversed(queries().assets(p.user.id, p.channel_id, ["script"]))}
    return render(p, "ideas.html", ideas=rows, status=status, fmt=fmt, counts=counts, scripted=scripted)


@router.post("/ideas/new")
def new_idea(
    title: str = Form(""),
    details: str = Form(""),
    effort: str = Form("standard"),
    format: str = Form(""),
    mode: str = Form("save"),
    p: Page = Depends(csrf),
) -> Response:
    c = get_container()
    idea = c.review.add_idea(p.user.id, p.channel_id, title, details, effort, format)
    assert idea.id is not None
    if mode == "script":  # write the script right away
        return started(f"/assets/{idea.id}", c.review.develop(p.user.id, idea.id))
    return back(f"/ideas?format={asset_format(idea.data)}", "idea_added")


@router.get("/scripts")
def scripts(request: Request, p: Page = Depends(page)) -> Response:
    if not p.active:
        return render(p, "empty.html")
    status = request.query_params.get("status") or None
    fmt = "short" if request.query_params.get("format") == "short" else "long"
    rows, counts = by_format(queries().assets(p.user.id, p.channel_id, ["script"], status=status), fmt)
    return render(p, "scripts.html", scripts=rows, status=status, fmt=fmt, counts=counts)


@router.post("/ideas/{idea_id}/develop")
def develop(idea_id: int, p: Page = Depends(csrf)) -> Response:
    return started(f"/assets/{idea_id}", get_container().review.develop(p.user.id, idea_id))


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
    run_id = request.query_params.get("run")
    resp = render(
        p,
        "asset.html",
        a=a,
        developed=developed,
        run=(q.run(p.user.id, int(run_id)) if run_id and run_id.isdigit() else None) or q.run_for_asset(p.user.id, a),
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
        dest = {"idea": "/ideas", "script": "/scripts"}.get(gone.kind, "/today")
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
def regenerate(asset_id: int, next: str = Form("/today"), p: Page = Depends(csrf)) -> Response:
    result = get_container().review.regenerate(p.user.id, asset_id)
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
