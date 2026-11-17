"""Script to video: a voice-over and a kinetic typography video timed to it. Nothing here is published."""

import json
import logging
import re
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import Field, create_model

from pipeline import assets
from ytstudio.application import thumbnails, video_versions
from ytstudio.application.pipeline import Context, Step, register
from ytstudio.application.schemas import SpeechOut, StoryboardOut, ThumbnailOut
from ytstudio.application.steps_daily import LANG_NAMES, LLMStep
from ytstudio.config import ROOT
from ytstudio.domain.errors import PermanentError
from ytstudio.domain.models import Asset, Queue, format_of
from ytstudio.domain.speech import pronounce
from ytstudio.domain.video import CHATTERBOX_LANGUAGE, script_language, voice_for

SENTENCE_END = re.compile(r"(?<=[.!?।])\s+|\n+")
PAUSE_SENTENCE, PAUSE_SECTION = 0.28, 0.7  # a human reader breathes between thoughts
SPEECH_CHUNK = 40  # sentences per Devanagari request
PLAN_CHUNK = 30  # sentences per storyboard request, so a long video's plan fits in one reply each

log = logging.getLogger(__name__)


def spoken_lines(sections: list[dict[str, Any]]) -> list[tuple[str, float]]:
    """(sentence, pause after it). Stage directions in [brackets] and markdown marks are not read aloud."""
    lines: list[tuple[str, float]] = []
    for sec in sections:
        text = re.sub(r"\[[^\]]*\]|[*_#`]", "", str(sec.get("spoken", "")))
        sentences = [s.strip() for s in SENTENCE_END.split(text) if re.search(r"\w", s)]
        lines += [(s, PAUSE_SENTENCE) for s in sentences]
        if lines:
            lines[-1] = (lines[-1][0], PAUSE_SECTION)
    return lines


def load_script(step: Step, ctx: Context) -> Asset:
    with step.deps.uow() as uow:
        a = uow.assets.get(ctx.inputs["asset_id"], ctx.user.id)
    if a.kind != "script":
        raise PermanentError("Only a script can be turned into a video.")
    return a


@register
class PlanStoryboard(LLMStep):
    """The LLM designs the video scene by scene: colours, layouts, code windows, checklists, stickers and the follow
    button. Its plan is checked against the script when the video is drawn; any failure here means the rule-based plan
    is used instead, so the video is never lost to it."""

    name = "plan_storyboard"
    critical = False

    def run(self, ctx: Context) -> dict[str, Any]:
        a = load_script(self, ctx)
        sections = a.data.get("script", {}).get("sections", [])
        chunks: list[list[dict[str, Any]]] = []
        n = 0  # sentences are numbered as the narrator splits them, across the whole script
        for sec in sections:  # whole sections per request, up to PLAN_CHUNK sentences
            rows = [
                {"n": n + i, "beat": sec.get("beat", ""), "text": t} for i, (t, _) in enumerate(spoken_lines([sec]))
            ]
            n += len(rows)
            if rows and (not chunks or len(chunks[-1]) + len(rows) > PLAN_CHUNK):
                chunks.append([])
            if rows:
                chunks[-1] += rows
        fmt = "short" if format_of(a.data) == "short" else "long"
        scenes: list[dict[str, Any]] = []
        try:
            for i, chunk in enumerate(chunks):
                v = {
                    "sentences": chunk,
                    "first": i == 0,
                    "last": i == len(chunks) - 1,
                    "format": fmt,
                    "stickers": thumbnails.stickers(),
                    "characters": assets.faces(ROOT / "assets"),
                    "backgrounds": sorted(assets.backgrounds(ROOT / "assets")),
                }
                scenes += [s.model_dump() for s in self.ask(ctx, "smart", "storyboard", v, StoryboardOut).scenes]
        except Exception as e:
            log.warning("storyboard plan failed (%s); the video uses the rule-based plan", e)
            return {"story": None}
        return {"story": scenes}


@register
class SpeechText(LLMStep):
    """Chatterbox's Hindi model garbles Roman letters, so a Hinglish script is read from a Devanagari copy of each
    sentence. The screen keeps the script's own words; times still match because each copy is the same sentence."""

    name = "speech_text"

    def run(self, ctx: Context) -> dict[str, Any]:
        lines = [t for t, _ in spoken_lines(load_script(self, ctx).data.get("script", {}).get("sections", []))]
        if voice_for(" ".join(lines), str(ctx.inputs.get("voice") or "")) != "chatterbox-hinglish":
            return {"lines": None}
        out: list[str] = []
        fixed = [pronounce(x) for x in lines]  # names, acronyms and versions get one spelling, so they sound the same
        for i in range(0, len(fixed), SPEECH_CHUNK):
            chunk = fixed[i : i + SPEECH_CHUNK]
            exact = create_model(
                "SpeechOut", __base__=SpeechOut, lines=(list[str], Field(min_length=len(chunk), max_length=len(chunk)))
            )
            out += [pronounce(x) for x in self.ask(ctx, "fast", "speech", {"sentences": chunk}, exact).lines]
        return {"lines": out}


@register
class NarrateScript(Step):
    """Piper reads the script aloud, sentence by sentence, so the subtitles know when each one is said."""

    name = "narrate_script"
    queue = Queue.MEDIA

    def run(self, ctx: Context) -> dict[str, Any]:
        a = load_script(self, ctx)
        lines = spoken_lines(a.data.get("script", {}).get("sections", []))
        if not lines:
            raise PermanentError("This script has no spoken words to read.")
        voice = voice_for(" ".join(t for t, _ in lines), str(ctx.inputs.get("voice") or ""))  # fits the words
        speech = (ctx.outputs.get("speech_text") or {}).get("lines")
        if speech:
            said = [(x, pause) for x, (_, pause) in zip(speech, lines, strict=True)]
        elif CHATTERBOX_LANGUAGE.get(voice) == "hi":  # a Hindi script's English words, said the same way every time
            said = [(pronounce(x), pause) for x, pause in lines]
        else:
            said = lines
        with tempfile.TemporaryDirectory() as d:
            wav, mp3 = Path(d) / "voice.wav", Path(d) / "voice.mp3"
            timings, engine = self.deps.narrator.speak(
                said,
                voice,
                wav,
                lambda done, total: self.deps.progress(
                    ctx.run.id, self.name, f"Voice: sentence {done} of {total}", done / max(1, total)
                ),
            )
            self.deps.media.encode_audio(wav, mp3)
            stamp = int(self.deps.clock.now().timestamp())
            key = self.deps.storage.put(mp3, f"audio/{a.channel_id}/{a.id}-{stamp}.mp3")
        units = [
            {"text": text, "start": round(t["start"], 3), "end": round(t["end"], 3), "words": t["words"]}
            | ({"heard": t["heard"]} if t.get("heard") else {})
            for (text, _), t in zip(lines, timings, strict=True)
        ]
        seconds = round(timings[-1]["end"], 2)
        return {"audio_key": key, "seconds": seconds, "units": units, "voice": voice, "engine": engine}


def frames(step: Step, ctx: Context, what: str) -> Callable[[int, int], None]:
    """Remotion's progress, shown on the run's panel."""
    return lambda done, total: step.deps.progress(
        ctx.run.id, step.name, f"{what}: frame {done:,} of {total:,}", done / max(1, total)
    )


@register
class RenderScriptVideo(Step):
    """A quick draft of the video, kept as a new version with its voice and timeline, so the very same video can be
    drawn in full quality later (render_hd) and earlier versions stay one click away."""

    name = "render_script_video"
    queue = Queue.MEDIA
    needs = ("narrate_script",)  # plan_storyboard is optional: without it the plan is rule-based

    def run(self, ctx: Context) -> dict[str, Any]:
        a = load_script(self, ctx)
        n = ctx.out("narrate_script")
        stamp = int(self.deps.clock.now().timestamp())
        base = f"videos/{a.channel_id}/{a.id}-{stamp}"
        with tempfile.TemporaryDirectory() as d:
            out, plan = Path(d) / "draft.mp4", Path(d) / "timeline.json"
            timeline = self.deps.media.render_script_video(
                n["units"],
                self.deps.storage.get(n["audio_key"]),
                format_of(a.data) == "short",
                out,
                (ctx.outputs.get("plan_storyboard") or {}).get("story"),
                draft=True,
                on_progress=frames(self, ctx, "Drawing the draft"),
            )
            plan.write_text(json.dumps(timeline), encoding="utf-8")
            key = self.deps.storage.put(out, f"{base}-draft.mp4")
            plan_key = self.deps.storage.put(plan, f"{base}.timeline.json")
        now = self.deps.clock.now().isoformat()
        with self.deps.uow() as uow:
            fresh = uow.assets.get(a.id or 0, ctx.user.id)
            version = {
                "made_at": now,
                "voice": n["voice"],
                "engine": n["engine"],
                "seconds": n["seconds"],
                "audio_key": n["audio_key"],
                "timeline_key": plan_key,
                "draft_key": key,
                "hd_key": None,
                "hd_made_at": None,
                "script_hash": video_versions.script_hash(fresh.data),
            }
            fresh.data, gone = video_versions.added(fresh.data, version)
            uow.assets.update(fresh)
            uow.commit()
        for k in gone:  # versions past the newest few go with their files
            self.deps.storage.delete(k)
        return {"video_key": key, "seconds": n["seconds"], "n": fresh.data["video_n"]}


@register
class RenderHD(Step):
    """One version again in full quality: its own timeline and voice, so it is the very same video, only sharper.
    It becomes the version shown."""

    name = "render_hd"
    queue = Queue.MEDIA

    def run(self, ctx: Context) -> dict[str, Any]:
        a = load_script(self, ctx)
        v = video_versions.find(a.data, int(ctx.inputs["n"]))
        if v.get("hd_key"):
            return {"video_key": v["hd_key"]}
        if not v.get("timeline_key"):
            raise PermanentError("This version was made before HD drawing existed. Make the video again.")
        timeline = json.loads(self.deps.storage.get(v["timeline_key"]).read_text(encoding="utf-8"))
        stamp = int(self.deps.clock.now().timestamp())
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "hd.mp4"
            audio = self.deps.storage.get(v["audio_key"])
            self.deps.media.render_timeline(timeline, audio, out, False, frames(self, ctx, "Drawing in HD"))
            key = self.deps.storage.put(out, f"videos/{a.channel_id}/{a.id}-{stamp}-v{v['n']}-hd.mp4")
        with self.deps.uow() as uow:
            fresh = uow.assets.get(a.id or 0, ctx.user.id)
            try:
                v = video_versions.find(fresh.data, v["n"])
            except PermanentError:  # deleted while it was drawn
                self.deps.storage.delete(key)
                raise
            fresh.data = video_versions.updated(
                fresh.data, v | {"hd_key": key, "hd_made_at": self.deps.clock.now().isoformat()}
            )
            uow.assets.update(fresh)
            uow.commit()
        return {"video_key": key}


@register
class PlanThumbnail(LLMStep):
    """The LLM designs the thumbnail: the words, the one boxed word, the colour and the sticker. The creator's own text
    and instructions, when given, lead. If the model fails, a plain design from the title is drawn instead."""

    name = "plan_thumbnail"
    critical = False

    def run(self, ctx: Context) -> dict[str, Any]:
        a = load_script(self, ctx)
        d = a.data
        suggested = []
        if a.parent_id:  # a long video's package (titles + thumbnail text) hangs under the same idea
            with self.deps.uow() as uow:
                for x in uow.assets.children(a.parent_id, ctx.user.id):
                    if x.kind == "package":
                        suggested = [p["thumbnail_text"] for p in x.data.get("pairs", []) if p.get("thumbnail_text")]
        title = str(d.get("youtube_title") or d.get("title") or a.title)
        text = str(ctx.inputs.get("thumb_text") or "").strip()
        sections = d.get("script", {}).get("sections", [])
        language = script_language(d, ctx.schedule.language)
        v = {
            "title": title,
            "format": "short" if format_of(d) == "short" else "long",
            "language": LANG_NAMES.get(language, language),
            "opening": " ".join(str(s.get("spoken", "")) for s in sections[:1])[:600],
            "suggested": suggested,
            "text": text,
            "notes": str(ctx.inputs.get("thumb_notes") or "").strip(),
            "attempt": ctx.inputs.get("attempt") or 0,
            "stickers": thumbnails.stickers(),
        }
        try:
            design = self.ask(ctx, "smart", "thumbnail", v, ThumbnailOut).model_dump()
        except Exception as e:
            log.warning("thumbnail design failed (%s); a plain one is drawn from the title", e)
            design = {}
        return thumbnails.finish(design, text, title, suggested)


@register
class DrawThumbnail(Step):
    name = "draw_thumbnail"
    queue = Queue.MEDIA
    needs = ("plan_thumbnail",)

    def run(self, ctx: Context) -> dict[str, Any]:
        a = load_script(self, ctx)
        t = ctx.out("plan_thumbnail")
        short = format_of(a.data) == "short"
        key = thumbnails.draw(self.deps.images, self.deps.storage, self.deps.clock, t, short, f"{a.channel_id}/{a.id}")
        with self.deps.uow() as uow:
            fresh = uow.assets.get(a.id or 0, ctx.user.id)
            old = fresh.data.get("thumb_key")
            fresh.data = fresh.data | {
                "thumb_key": key,
                "thumb": t,
                "thumb_made_at": self.deps.clock.now().isoformat(),
                "thumb_attempts": int(fresh.data.get("thumb_attempts") or 0) + 1,
            }
            uow.assets.update(fresh)
            uow.commit()
        if old and old != key:  # the thumbnail made before this one is replaced
            self.deps.storage.delete(old)
        return {"thumb_key": key}
