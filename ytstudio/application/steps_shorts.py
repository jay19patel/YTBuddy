"""Shorts pipeline: transcript -> chapters/EDL -> candidates -> render (one task each) -> covers -> meta -> queue."""

import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ytstudio.application.pipeline import Context, Step, register
from ytstudio.application.schemas import ShortMetaOut, ShortPicksOut
from ytstudio.application.steps_daily import LLMStep, dump
from ytstudio.domain.events import ShortsReady
from ytstudio.domain.models import AssetStatus, Cue, Queue

MIN_S, MAX_S = 15.0, 58.0


def cues_of(ctx: Context) -> list[Cue]:
    return [Cue(**c) for c in ctx.out("transcribe_fallback")["cues"]]


def candidate_windows(
    cues: list[Cue], retention: list[tuple[float, float]], duration: float, limit: int = 40
) -> list[dict[str, Any]]:
    """Self-contained 15-58 s windows that start at a cue and end on a sentence end, scored by how much
    better than typical the audience retained that stretch (relativeRetentionPerformance), when known."""
    out: list[dict[str, Any]] = []
    for i, first in enumerate(cues):
        text: list[str] = []
        for last in cues[i:]:
            length = last.end - first.start
            if length > MAX_S:
                break
            text.append(last.text)
            if length >= MIN_S and last.text.rstrip().endswith((".", "?", "!", "।")):
                rel = [r for x, r in retention if duration and first.start <= x * duration <= last.end]
                score = sum(rel) / len(rel) if rel else 0.0
                out.append(
                    {
                        "start": round(first.start, 2),
                        "end": round(last.end, 2),
                        "text": " ".join(text),
                        "retention_score": round(score, 3),
                    }
                )
                break
    out.sort(key=lambda w: -w["retention_score"])
    picked: list[dict[str, Any]] = []
    for w in out:  # drop windows overlapping a better one
        if all(w["end"] <= p["start"] or w["start"] >= p["end"] for p in picked):
            picked.append(w)
        if len(picked) >= limit:
            break
    return sorted(picked, key=lambda w: w["start"])


@register
class GetTranscript(Step):
    """Captions API first (own video, force-ssl scope). The whisper step fills in when this finds none."""

    name = "get_transcript"
    queue = Queue.IO

    def run(self, ctx: Context) -> dict[str, Any]:
        video_id = ctx.inputs.get("video_id")
        cues = self.deps.videos.captions(ctx.channel.id, video_id) if video_id else None
        return {"cues": [asdict(c) for c in cues] if cues else None, "source": "captions" if cues else None}


@register
class TranscribeFallback(Step):
    name = "transcribe_fallback"
    queue = Queue.MEDIA
    needs = ("get_transcript",)

    def run(self, ctx: Context) -> dict[str, Any]:
        got = ctx.out("get_transcript")
        if got["cues"]:
            return dict(got)
        master = self.deps.storage.get(ctx.inputs["storage_key"])
        lang = ctx.schedule.language
        cues = self.deps.transcriber.transcribe(master, None if lang == "hinglish" else lang)
        return {"cues": [asdict(c) for c in cues], "source": "whisper"}


@register
class BuildChapters(Step):
    """Chapters and a dead-air edit decision list from the final transcript (vendored tools)."""

    name = "build_chapters"
    queue = Queue.IO
    needs = ("transcribe_fallback",)

    def run(self, ctx: Context) -> dict[str, Any]:
        cues = cues_of(ctx)
        return {"chapters": self.deps.chapters.build(cues), "edit_list": self.deps.edl.build(cues)}


@register
class PickShortCandidates(LLMStep):
    name = "pick_short_candidates"
    needs = ("transcribe_fallback",)

    def run(self, ctx: Context) -> dict[str, Any]:
        cues = cues_of(ctx)
        duration = self.deps.media.duration(self.deps.storage.get(ctx.inputs["storage_key"]))
        retention: list[tuple[float, float]] = []
        if vid := ctx.inputs.get("video_id"):
            points = self.deps.analytics.retention(ctx.channel.id, vid)
            retention = [(p.ratio, p.relative) for p in points if p.relative is not None]
        windows = candidate_windows(cues, retention, duration)
        if not windows:
            return {"picks": [], "windows": 0}
        out = self.ask(ctx, "smart", "short_candidates", {"windows": windows}, ShortPicksOut)
        picks = []
        for p in out.picks:  # trust the window bounds, not numbers the model typed
            if 0 <= p.window < len(windows):
                w = windows[p.window]
                picks.append(
                    {
                        "start": w["start"],
                        "end": w["end"],
                        "text": w["text"],
                        "first_line": p.first_line,
                        "reason": p.reason,
                        "retention_score": w["retention_score"],
                    }
                )
        return {"picks": picks, "windows": len(windows)}


@register
class RenderShorts(Step):
    """One media task per Short (a chord in Celery)."""

    name = "render_shorts"
    queue = Queue.MEDIA
    fan_out = True
    needs = ("pick_short_candidates",)

    def items(self, ctx: Context) -> list[Any]:
        return list(range(len(ctx.out("pick_short_candidates")["picks"])))

    def run_item(self, ctx: Context, item: Any) -> Any:
        pick = ctx.out("pick_short_candidates")["picks"][item]
        key = f"shorts/{ctx.channel.id}/{ctx.run.id}/{item}.mp4"
        if self.deps.storage.exists(key):  # idempotent: a retried chord member skips finished renders
            return {"index": item, "key": key}
        master = self.deps.storage.get(ctx.inputs["storage_key"])
        cues = [Cue(**c) for c in ctx.out("transcribe_fallback")["cues"]]
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "short.mp4"
            self.deps.media.render_short(master, pick["start"], pick["end"], cues, out)
            self.deps.storage.put(out, key)
        return {"index": item, "key": key}

    def merge(self, ctx: Context, results: list[Any]) -> dict[str, Any]:
        return {"rendered": sorted(results, key=lambda r: r["index"])}


@register
class MakeCovers(Step):
    name = "make_covers"
    queue = Queue.MEDIA
    needs = ("render_shorts",)

    def run(self, ctx: Context) -> dict[str, Any]:
        picks = ctx.out("pick_short_candidates")["picks"]
        covers = []
        for r in ctx.out("render_shorts")["rendered"]:
            key = r["key"].replace(".mp4", ".jpg")
            with tempfile.TemporaryDirectory() as d:
                frame, cover = Path(d) / "f.jpg", Path(d) / "c.jpg"
                self.deps.media.frame(self.deps.storage.get(r["key"]), 1.0, frame)
                self.deps.images.cover(frame, picks[r["index"]]["first_line"], cover)
                self.deps.storage.put(cover, key)
            covers.append({"index": r["index"], "key": key})
        return {"covers": covers}


@register
class WriteShortMeta(LLMStep):
    name = "write_short_meta"
    needs = ("pick_short_candidates",)
    critical = False

    def run(self, ctx: Context) -> dict[str, Any]:
        picks = ctx.out("pick_short_candidates")["picks"]
        if not picks:
            return {"shorts": []}
        out = self.ask(ctx, "fast", "short_meta", {"picks": picks}, ShortMetaOut)
        return {"shorts": [dump(m) for m in out.shorts[: len(picks)]]}


@register
class QueueShorts(Step):
    name = "queue_shorts"
    queue = Queue.IO

    def run(self, ctx: Context) -> dict[str, Any]:
        o = ctx.outputs
        picks = o.get("pick_short_candidates", {}).get("picks", [])
        rendered = {r["index"]: r["key"] for r in o.get("render_shorts", {}).get("rendered", [])}
        covers = {c["index"]: c["key"] for c in o.get("make_covers", {}).get("covers", [])}
        meta = o.get("write_short_meta", {}).get("shorts", [])
        ids = []
        with self.deps.uow() as uow:
            for i, p in enumerate(picks):
                if i not in rendered:
                    continue
                m = meta[i] if i < len(meta) else {"title": p["first_line"][:100], "description": ""}
                a = uow.assets.add(
                    ctx.asset(
                        kind="short",
                        title=m["title"],
                        data=p
                        | m
                        | {
                            "video_key": rendered[i],
                            "cover_key": covers.get(i),
                            "media_id": ctx.inputs.get("media_id"),
                        },
                    )
                )
                ids.append(a.id)
            if ch := o.get("build_chapters"):
                uow.assets.add(ctx.asset(kind="chapters", title="Chapters and edit list", data=ch))
            uow.commit()
        if ids:
            self.deps.publish(ShortsReady(user_id=ctx.user.id, channel_title=ctx.channel.title, count=len(ids)))
        return {"asset_ids": ids}


@register
class RerenderShort(Step):
    """Trim: render one Short again with new bounds; the asset keeps its id and goes back to review."""

    name = "rerender_short"
    queue = Queue.MEDIA

    def run(self, ctx: Context) -> dict[str, Any]:
        start, end = float(ctx.inputs["start"]), float(ctx.inputs["end"])
        with self.deps.uow() as uow:
            asset = uow.assets.get(ctx.inputs["asset_id"], ctx.user.id)
        key = f"shorts/{ctx.channel.id}/trim/{ctx.run.id}.mp4"
        master = self.deps.storage.get(ctx.inputs["storage_key"])
        cues = [Cue(**c) for c in ctx.out("transcribe_fallback")["cues"]]
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "short.mp4"
            self.deps.media.render_short(master, start, end, cues, out)
            self.deps.storage.put(out, key)
        with self.deps.uow() as uow:
            asset.data = asset.data | {"start": start, "end": end, "video_key": key}
            asset.status = AssetStatus.NEEDS_REVIEW
            uow.assets.update(asset)
            uow.commit()
        return {"video_key": key}
