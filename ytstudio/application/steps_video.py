"""Script to video: a voice-over and a white screen with black subtitles. Nothing here is published."""

import re
import tempfile
from pathlib import Path
from typing import Any

from ytstudio.application.pipeline import Context, Step, register
from ytstudio.domain.errors import PermanentError
from ytstudio.domain.models import Asset, Queue, format_of

SENTENCE_END = re.compile(r"(?<=[.!?।])\s+|\n+")
PAUSE_SENTENCE, PAUSE_SECTION = 0.2, 0.55


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
class NarrateScript(Step):
    """Piper reads the script aloud, sentence by sentence, so the subtitles know when each one is said."""

    name = "narrate_script"
    queue = Queue.MEDIA

    def run(self, ctx: Context) -> dict[str, Any]:
        a = load_script(self, ctx)
        lines = spoken_lines(a.data.get("script", {}).get("sections", []))
        if not lines:
            raise PermanentError("This script has no spoken words to read.")
        language = ctx.inputs.get("voice") or ("hi" if ctx.schedule.language == "hi" else "en")
        with tempfile.TemporaryDirectory() as d:
            wav, mp3 = Path(d) / "voice.wav", Path(d) / "voice.mp3"
            timings = self.deps.narrator.speak(lines, language, wav)
            self.deps.media.encode_audio(wav, mp3)
            key = self.deps.storage.put(
                mp3, f"audio/{a.channel_id}/{a.id}-{int(self.deps.clock.now().timestamp())}.mp3"
            )
        units = [
            {"text": text, "start": round(t0, 3), "end": round(t1, 3)}
            for (text, _), (t0, t1) in zip(lines, timings, strict=True)
        ]
        seconds = round(timings[-1][1], 2)
        return {"audio_key": key, "seconds": seconds, "units": units, "voice": language}


@register
class RenderScriptVideo(Step):
    name = "render_script_video"
    queue = Queue.MEDIA
    needs = ("narrate_script",)

    def run(self, ctx: Context) -> dict[str, Any]:
        a = load_script(self, ctx)
        n = ctx.out("narrate_script")
        stamp = int(self.deps.clock.now().timestamp())
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "video.mp4"
            self.deps.media.render_subtitle_video(
                n["units"], self.deps.storage.get(n["audio_key"]), format_of(a.data) == "short", out
            )
            key = self.deps.storage.put(out, f"videos/{a.channel_id}/{a.id}-{stamp}.mp4")
        with self.deps.uow() as uow:
            fresh = uow.assets.get(a.id or 0, ctx.user.id)
            old = [fresh.data.get(k) for k in ("video_key", "audio_key")]
            fresh.data = fresh.data | {
                "video_key": key,
                "audio_key": n["audio_key"],
                "video_seconds": n["seconds"],
                "video_made_at": self.deps.clock.now().isoformat(),
            }
            uow.assets.update(fresh)
            uow.commit()
        for k in old:  # the video made before this one is replaced
            if k and k not in (key, n["audio_key"]):
                self.deps.storage.delete(k)
        return {"video_key": key, "seconds": n["seconds"]}
