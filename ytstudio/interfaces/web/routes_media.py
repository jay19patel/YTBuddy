"""Master upload (chunked, resumable), Shorts review, media files."""

import os
from pathlib import Path
from typing import Any

import anyio
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from ytstudio.config import get_settings
from ytstudio.container import get_container
from ytstudio.interfaces.web.app import Page, back, csrf, page, queries, render
from ytstudio.interfaces.web.routes_pages import started

router = APIRouter()
CHUNK_MAX = 16 * 1024 * 1024
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


def part_path(media_id: int) -> Path:
    d = get_settings().media_dir / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{media_id}.part"


@router.get("/shorts")
def shorts(request: Request, p: Page = Depends(page)) -> Response:
    if not p.active:
        return render(p, "empty.html")
    c = get_container()
    with c.uow() as uow:
        files = uow.media.list(p.user.id, p.channel_id)
    status = request.query_params.get("status") or "needs_review"
    rows = queries().assets(p.user.id, p.channel_id, ["short"], status=status if status != "all" else None)
    chapters = queries().assets(p.user.id, p.channel_id, ["chapters"], limit=3)
    run_id = request.query_params.get("run")
    return render(
        p,
        "shorts.html",
        files=files,
        shorts=rows,
        status=status,
        chapters=chapters,
        run=queries().run(p.user.id, int(run_id)) if run_id and run_id.isdigit() else None,
    )


@router.post("/api/media")
async def create_upload(request: Request, p: Page = Depends(csrf)) -> Response:
    body = await request.json()
    name, size = str(body.get("filename", ""))[:200], int(body.get("size", 0))
    if Path(name).suffix.lower() not in VIDEO_EXT:
        raise HTTPException(400, "Upload a video file (mp4, mov, mkv, webm).")
    if not 0 < size <= get_settings().max_master_bytes:
        raise HTTPException(400, "File is empty or larger than MAX_MASTER_BYTES.")
    with get_container().uow() as uow:
        m = uow.media.create(p.user.id, p.channel_id, Path(name).name, size)
        uow.commit()
    return JSONResponse({"id": m.id, "received": 0})


@router.get("/api/media/{media_id}")
def upload_status(media_id: int, p: Page = Depends(page)) -> Response:
    with get_container().uow() as uow:
        m = uow.media.get(media_id, p.user.id)
    return JSONResponse({"id": m.id, "received": m.received, "size": m.size, "status": m.status})


@router.put("/api/media/{media_id}")
async def upload_chunk(request: Request, media_id: int, offset: int, p: Page = Depends(csrf)) -> Response:
    """Resumable: the client sends from the server's `received` offset; mismatches answer 409 with the truth."""
    c = get_container()
    with c.uow() as uow:
        m = uow.media.get(media_id, p.user.id)
    if m.status != "uploading":
        raise HTTPException(409, "upload already finished")
    if offset != m.received:
        return JSONResponse({"received": m.received}, status_code=409)
    chunk = await request.body()
    if not chunk or len(chunk) > CHUNK_MAX or m.received + len(chunk) > m.size:
        raise HTTPException(400, "bad chunk size")

    def append() -> None:
        with open(part_path(m.id), "ab") as f:
            f.truncate(m.received)  # drop any tail a dropped connection left behind
            f.write(chunk)
            f.flush()
            os.fsync(f.fileno())

    await anyio.to_thread.run_sync(append)
    m.received += len(chunk)
    with c.uow() as uow:
        uow.media.update(m)
        uow.commit()
    return JSONResponse({"received": m.received})


@router.post("/api/media/{media_id}/complete")
async def complete_upload(media_id: int, p: Page = Depends(csrf)) -> Response:
    c = get_container()
    with c.uow() as uow:
        m = uow.media.get(media_id, p.user.id)
    if m.received != m.size:
        raise HTTPException(409, f"only {m.received} of {m.size} bytes received")
    key = f"masters/{m.channel_id}/{m.id}{Path(m.filename).suffix.lower()}"
    path = part_path(m.id)
    await anyio.to_thread.run_sync(c.storage.put, path, key)
    path.unlink(missing_ok=True)
    m.status, m.storage_key = "ready", key
    with c.uow() as uow:
        uow.media.update(m)
        uow.commit()
    return JSONResponse({"id": m.id, "status": m.status})


@router.post("/media/{media_id}/link")
def link_video(media_id: int, video_id: str = Form(""), p: Page = Depends(csrf)) -> Response:
    """Tie a master file to its YouTube upload, so captions/retention are used and new uploads auto-trigger."""
    vid = video_id.strip().split("v=")[-1].split("/")[-1][:20]
    c = get_container()
    with c.uow() as uow:
        m = uow.media.get(media_id, p.user.id)
        m.video_id = vid or None
        uow.media.update(m)
        uow.commit()
    return back("/shorts", "saved")


@router.post("/media/{media_id}/shorts")
def make_shorts(media_id: int, p: Page = Depends(csrf)) -> Response:
    return started("/shorts", get_container().shorts.start(p.user.id, media_id))


@router.post("/shorts/{asset_id}/trim")
def trim(asset_id: int, start: float = Form(...), end: float = Form(...), p: Page = Depends(csrf)) -> Response:
    c = get_container()
    return started("/shorts", c.shorts.trim(p.user.id, asset_id, start, end, c.clock.now()))


@router.get("/files/asset/{asset_id}/{what}")
def asset_file(asset_id: int, what: str, download: int = 0, p: Page = Depends(page)) -> Response:
    a: dict[str, Any] | None = queries().asset(p.user.id, asset_id)
    key = (a or {}).get("data", {}).get({"video": "video_key", "cover": "cover_key"}.get(what, ""))
    if not key:
        raise HTTPException(404)
    path = get_container().storage.get(key)
    name = f"short-{asset_id}{path.suffix}"
    return FileResponse(
        path, filename=name if download else None, content_disposition_type="attachment" if download else "inline"
    )
