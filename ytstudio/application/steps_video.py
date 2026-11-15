"""Script to video: a voice-over and a kinetic typography video timed to it. Nothing here is published."""

import logging
import re
import tempfile
from pathlib import Path
from typing import Any

from pydantic import Field, create_model

from ytstudio.application.pipeline import Context, Step, register
from ytstudio.application.schemas import SpeechOut, StoryboardOut
from ytstudio.application.steps_daily import LLMStep
from ytstudio.config import ROOT
from ytstudio.domain.errors import PermanentError
from ytstudio.domain.models import Asset, Queue, format_of
from ytstudio.domain.video import voice_for

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
        stickers = sorted(p.stem for p in (ROOT / "assets" / "stickers").glob("*.png"))  # new files are used at once
        scenes: list[dict[str, Any]] = []
        try:
            for i, chunk in enumerate(chunks):
                v = {
                    "sentences": chunk,
                    "first": i == 0,
                    "last": i == len(chunks) - 1,
                    "format": fmt,
                    "stickers": stickers,
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
        for i in range(0, len(lines), SPEECH_CHUNK):
            chunk = lines[i : i + SPEECH_CHUNK]
            exact = create_model(
                "SpeechOut", __base__=SpeechOut, lines=(list[str], Field(min_length=len(chunk), max_length=len(chunk)))
            )
            out += self.ask(ctx, "fast", "speech", {"sentences": chunk}, exact).lines
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
        said = [(x, pause) for x, (_, pause) in zip(speech, lines, strict=True)] if speech else lines
        with tempfile.TemporaryDirectory() as d:
            wav, mp3 = Path(d) / "voice.wav", Path(d) / "voice.mp3"
            timings, engine = self.deps.narrator.speak(said, voice, wav)
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


@register
class RenderScriptVideo(Step):
    name = "render_script_video"
    queue = Queue.MEDIA
    needs = ("narrate_script",)  # plan_storyboard is optional: without it the plan is rule-based

    def run(self, ctx: Context) -> dict[str, Any]:
        a = load_script(self, ctx)
        n = ctx.out("narrate_script")
        stamp = int(self.deps.clock.now().timestamp())
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "video.mp4"
            self.deps.media.render_script_video(
                n["units"],
                self.deps.storage.get(n["audio_key"]),
                format_of(a.data) == "short",
                out,
                (ctx.outputs.get("plan_storyboard") or {}).get("story"),
            )
            key = self.deps.storage.put(out, f"videos/{a.channel_id}/{a.id}-{stamp}.mp4")
        with self.deps.uow() as uow:
            fresh = uow.assets.get(a.id or 0, ctx.user.id)
            old = [fresh.data.get(k) for k in ("video_key", "audio_key")]
            fresh.data = fresh.data | {
                "video_key": key,
                "audio_key": n["audio_key"],
                "video_seconds": n["seconds"],
                "video_voice": n["voice"],
                "video_engine": n["engine"],
                "video_made_at": self.deps.clock.now().isoformat(),
            }
            uow.assets.update(fresh)
            uow.commit()
        for k in old:  # the video made before this one is replaced
            if k and k not in (key, n["audio_key"]):
                self.deps.storage.delete(k)
        return {"video_key": key, "seconds": n["seconds"]}
