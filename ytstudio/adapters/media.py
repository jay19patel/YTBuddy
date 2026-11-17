"""FFmpeg media processing, Pillow images, faster-whisper transcription.

Karaoke subtitles are drawn with Pillow as transparent PNG frames (one per spoken word, current word
highlighted) and overlaid with FFmpeg's core `overlay` filter. That needs no libass/drawtext, so it works
with any FFmpeg build.
"""

import logging
import os
import shutil
import subprocess
import tempfile
import textwrap
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from ytstudio.domain.errors import PermanentError
from ytstudio.domain.models import Cue

log = logging.getLogger(__name__)

W, H = 1080, 1920
SUB_Y, SUB_H = 1180, 520  # caption strip, lower third
FONTS = [
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]
DEVANAGARI_FONTS = [
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
    "/System/Library/Fonts/Supplemental/DevanagariMT.ttc",
]


@lru_cache(maxsize=16)
def font(size: int, devanagari: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in (DEVANAGARI_FONTS if devanagari else []) + FONTS:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def _font_for(text: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return font(size, any("ऀ" <= ch <= "ॿ" for ch in text))


def _ffmpeg(*args: str, timeout: int = 3600) -> None:
    try:
        p = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as e:
        raise PermanentError("ffmpeg is not installed or not on PATH") from e
    except subprocess.TimeoutExpired as e:
        raise PermanentError(f"ffmpeg did not finish within {timeout} s") from e
    if p.returncode:
        raise PermanentError(f"ffmpeg failed: {p.stderr.strip()[-500:]}")


def words_timed(cues: list[Cue], start: float, end: float) -> list[tuple[float, float, int, list[str]]]:
    """(t0, t1, highlighted word index, words of the line) relative to the clip start.
    Captions rarely carry word timing, so each cue's time is shared out by word length."""
    out = []
    for c in cues:
        if c.end <= start or c.start >= end:
            continue
        ws = c.text.split()
        if not ws:
            continue
        a, b = max(c.start, start) - start, min(c.end, end) - start
        total = sum(len(w) + 1 for w in ws)
        t = a
        for i, w in enumerate(ws):
            dt = (b - a) * (len(w) + 1) / total
            out.append((t, t + dt, i, ws))
            t += dt
    return out


def caption_frame(words: list[str], current: int, path: Path) -> None:
    img = Image.new("RGBA", (W, SUB_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = _font_for(" ".join(words), 78)
    lines = textwrap.wrap(" ".join(words), width=18) or [""]
    y = (SUB_H - len(lines) * 96) // 2
    k = 0
    for line in lines:
        lw = d.textlength(line, font=f)
        x = (W - lw) / 2
        for w in line.split():
            color = (255, 214, 0, 255) if k == current else (255, 255, 255, 255)
            d.text((x, y), w, font=f, fill=color, stroke_width=6, stroke_fill=(0, 0, 0, 255))
            x += d.textlength(w + " ", font=f)
            k += 1
        y += 96
    img.save(path)


class FFmpegMediaProcessor:
    def duration(self, path: Path) -> float:
        p = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        try:
            return float(p.stdout.strip())
        except ValueError as e:
            raise PermanentError(f"ffprobe could not read {path.name}") from e

    def render_short(self, src: Path, start: float, end: float, cues: list[Cue], out: Path) -> None:
        """9:16 center crop, 1080x1920 H.264/AAC, karaoke captions, loudness normalized to -14 LUFS.
        Written to a temp file next to `out`, then renamed: readers never see half a file."""
        out.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as d:
            tmpdir = Path(d)
            blank = tmpdir / "blank.png"
            Image.new("RGBA", (W, SUB_H), (0, 0, 0, 0)).save(blank)
            lines = ["ffconcat version 1.0"]
            t = 0.0
            for n, (a, b, idx, ws) in enumerate(words_timed(cues, start, end)):
                if a > t + 0.01:
                    lines += [f"file '{blank}'", f"duration {a - t:.3f}"]
                frame = tmpdir / f"w{n:05d}.png"
                caption_frame(ws, idx, frame)
                lines += [f"file '{frame}'", f"duration {b - a:.3f}"]
                t = b
            lines += [f"file '{blank}'", f"duration {max(end - start - t, 0.04):.3f}", f"file '{blank}'"]
            concat = tmpdir / "subs.ffconcat"
            concat.write_text("\n".join(lines) + "\n")
            part = out.with_name(f".{out.name}.part.mp4")
            # Stereo aformat around loudnorm: ffmpeg 5.1 (the Docker image) cannot link it to the encoder otherwise.
            _ffmpeg(
                "-ss",
                f"{start:.3f}",
                "-to",
                f"{end:.3f}",
                "-i",
                str(src),
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat),
                "-filter_complex",
                "[0:v]crop='min(iw,ih*9/16)':'min(ih,iw*16/9)',scale=1080:1920,setsar=1[bg];"
                f"[1:v]format=rgba[sub];[bg][sub]overlay=0:{SUB_Y}:eof_action=pass,format=yuv420p[v];"
                "[0:a]aformat=channel_layouts=stereo,loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,aformat=channel_layouts=stereo[a]",
                "-map",
                "[v]",
                "-map",
                "[a]",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "20",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-movflags",
                "+faststart",
                "-shortest",
                str(part),
            )
            os.replace(part, out)

    def encode_audio(self, wav: Path, out: Path) -> None:
        """Studio finish for a synthetic voice: roll off the rumble, even out the level, then set it to -16 LUFS."""
        part = out.with_name(f".{out.name}.part.mp3")
        chain = (
            "aformat=channel_layouts=mono,highpass=f=70,"
            "acompressor=threshold=-21dB:ratio=2.5:attack=12:release=180:makeup=2,"
            "loudnorm=I=-16:TP=-1.5:LRA=9,aformat=channel_layouts=mono"
        )
        _ffmpeg("-i", str(wav), "-af", chain, "-ar", "44100", "-c:a", "libmp3lame", "-q:a", "2", str(part), timeout=600)
        os.replace(part, out)

    def render_script_video(
        self,
        units: list[dict[str, Any]],
        audio: Path,
        vertical: bool,
        out: Path,
        story: list[dict[str, Any]] | None = None,
    ) -> None:
        """Kinetic typography (pipeline/ + renderer/): every word on screen when it is said, 9:16 for a Short, 16:9
        otherwise. `story` is the LLM's storyboard (scenes naming the sentences of `units` they say); when it is
        missing or unusable the scenes, colours and stickers are planned from the words by rule."""
        from pipeline import markup, plan, video
        from pipeline.render import RenderError
        from pipeline.settings import load
        from pipeline.transcribe import TimedWord, align
        from ytstudio.adapters.kinetic import on_speech

        cfg = load()
        tokens = [markup.Token(t) for u in units for w in str(u["text"]).split() if (t := _plain_word(w))]
        heard = [TimedWord(word=w["text"], start=w["start"], end=w["end"]) for u in units for w in u.get("words") or []]
        if heard:  # the voice reported its words: lay the script's spelling and punctuation onto them
            words = align([t.text for t in tokens], heard)
        else:  # Chatterbox reports no word times: where each word was heard, so the text keeps in sync
            spoken = [(a, b) for u in units for a, b in u.get("heard") or []] or speech_times(
                audio, cfg.transcribe.model
            )
            words = [
                TimedWord(word=t, start=w.start, end=w.end)
                for w in on_speech(units, spoken)
                if (t := _plain_word(w.text))
            ]
            tokens = [markup.Token(w.word) for w in words]
        if not words:
            raise PermanentError("There are no words to show in the video.")
        info = video.video_info(cfg, "9x16" if vertical else "16x9")
        planned = None
        if story:
            try:
                planned = plan.from_sentences(story, [str(u["text"]) for u in units], info, cfg.assets)
                tokens = plan.script_of(planned)
                words = align([t.text for t in tokens], words)
            except ValueError as e:
                log.warning("storyboard not usable (%s); the rule-based plan is used", e)
                planned = None
        try:
            timeline = plan.plan(planned or video.auto_story(cfg, tokens, words), words, info, self.duration(audio))
            out.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory() as d:
                part = out.with_name(f".{out.name}.part.mp4")
                video.render_video(cfg, timeline, audio, part, Path(d), video.music_bed(cfg), sfx=True)
                os.replace(part, out)
        except (RenderError, FileNotFoundError, ValueError) as e:
            raise PermanentError(f"The video could not be rendered: {e}") from e

    def frame(self, src: Path, at: float, out: Path) -> None:
        _ffmpeg(
            "-ss",
            f"{at:.3f}",
            "-i",
            str(src),
            "-frames:v",
            "1",
            "-vf",
            "crop='min(iw,ih*9/16)':'min(ih,iw*16/9)',scale=1080:1920",
            str(out),
            timeout=120,
        )


class PillowImageRenderer:
    def cover(self, frame: Path, text: str, out: Path) -> None:
        img = Image.open(frame).convert("RGB").resize((W, H))
        d = ImageDraw.Draw(img)
        f = _font_for(text, 120)
        y = 260
        for line in textwrap.wrap(text.upper() if text.isascii() else text, width=12)[:4]:
            lw = d.textlength(line, font=f)
            d.text(((W - lw) / 2, y), line, font=f, fill=(255, 255, 255), stroke_width=10, stroke_fill=(0, 0, 0))
            y += 140
        part = out.with_name(f".{out.name}.part.jpg")
        img.save(part, "JPEG", quality=90)
        os.replace(part, out)

    def thumbnail(self, text: str, highlight: str, bg: str, sticker: Path | None, short: bool, out: Path) -> None:
        """A thumbnail in the videos' look: flat palette colour, huge Bebas Neue words with one word boxed, and a big
        3D sticker. 1280x720 for a long video, 1080x1920 for a Short."""
        from pipeline.theme import PALETTE

        w, h = (1080, 1920) if short else (1280, 720)
        base = _rgb(PALETTE.get(bg, PALETTE["black"]))
        light = sum(base) > 450
        img = Image.new("RGB", (w, h), base)
        d = ImageDraw.Draw(img)
        shade = (
            tuple(min(255, c + 24) for c in base)
            if sum(base) < 90
            else tuple(int(c * (0.86 if light else 0.7)) for c in base)
        )
        area: tuple[float, float, float, float]
        if short:
            d.polygon([(0, h * 0.62), (w, h * 0.5), (w, h), (0, h)], fill=shade)
            spot, size, area = (w * 0.5, h * 0.76), int(w * 0.68), (70, 200, w - 70, h * 0.56)
        else:
            d.polygon([(w * 0.62, 0), (w, 0), (w, h), (w * 0.52, h)], fill=shade)
            spot, size, area = (w * 0.78, h * 0.52), int(h * 0.82), (56, 50, w * 0.6, h - 50)
        if sticker and sticker.exists():
            s = Image.open(sticker).convert("RGBA")
            s = s.resize((size, size * s.height // s.width), Image.Resampling.LANCZOS).rotate(
                -8, expand=True, resample=Image.Resampling.BICUBIC
            )
            shadow = Image.new("RGBA", s.size, (0, 0, 0, 0))
            shadow.putalpha(s.getchannel("A").point(lambda a: a * 0.45))
            shadow = shadow.filter(ImageFilter.GaussianBlur(18))
            sx, sy = int(spot[0] - s.width / 2), int(spot[1] - s.height / 2)
            img.paste(shadow, (sx + 14, sy + 22), shadow)
            img.paste(s, (sx, sy), s)
        ink = _rgb(PALETTE["ink"] if light else PALETTE["white"])
        accent = _rgb(PALETTE["crimson"] if bg in ("yellow", "orange", "beige") else PALETTE["yellow"])
        accent_ink = _rgb(PALETTE["white"] if accent == _rgb(PALETTE["crimson"]) else PALETTE["ink"])
        devanagari = any("ऀ" <= ch <= "ॿ" for ch in text)
        words = (text if devanagari else text.upper()).split()
        marked = {_bare(x) for x in highlight.split()}
        f, lines = _fit_words(d, words, area[2] - area[0], area[3] - area[1], devanagari)
        cap = f.getbbox("H")[3] - f.getbbox("H")[1]
        step, pad = int(cap * 1.32), int(cap * 0.16)
        y = area[1] + ((area[3] - area[1]) - (step * len(lines) - (step - cap))) / 2 + cap
        for line in lines:
            x = area[0] + (((area[2] - area[0]) - d.textlength(" ".join(line), font=f)) / 2 if short else 0)
            for word in line:
                lw = d.textlength(word, font=f)
                if _bare(word) in marked:
                    d.rectangle([x - pad, y - cap - pad, x + lw + pad, y + pad], fill=accent)
                    d.text((x, y), word, font=f, fill=accent_ink, anchor="ls")
                else:
                    if not light:  # a hard shadow keeps white words readable at phone size
                        d.text((x + 5, y + 6), word, font=f, fill=(0, 0, 0), anchor="ls")
                    d.text((x, y), word, font=f, fill=ink, anchor="ls")
                x += lw + d.textlength(" ", font=f) + pad
            y += step
        part = out.with_name(f".{out.name}.part.jpg")
        img.save(part, "JPEG", quality=92)
        os.replace(part, out)

    def to_jpeg(self, src: Path, out: Path) -> None:
        """Any image as a JPEG of at most 1920 px a side and under YouTube's 2 MB thumbnail limit."""
        try:
            img = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
        except (OSError, ValueError) as e:
            raise PermanentError("That file is not an image. Use a JPG or PNG.") from e
        img.thumbnail((1920, 1920))
        part = out.with_name(f".{out.name}.part.jpg")
        for quality in (90, 80, 70, 60):
            img.save(part, "JPEG", quality=quality)
            if part.stat().st_size < 2_000_000:
                break
        os.replace(part, out)


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    return tuple(int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]


def _bare(word: str) -> str:
    return "".join(ch for ch in word.lower() if ch.isalnum())


def _thumb_font(size: int, devanagari: bool) -> ImageFont.FreeTypeFont:
    fonts = Path(__file__).resolve().parents[2] / "assets" / "fonts"
    f = ImageFont.truetype(str(fonts / ("NotoSansDevanagari.ttf" if devanagari else "BebasNeue-Regular.ttf")), size)
    if devanagari:
        try:
            f.set_variation_by_name("Bold")
        except (OSError, ValueError):
            pass
    return f


def _fit_words(
    d: ImageDraw.ImageDraw, words: list[str], width: float, height: float, devanagari: bool
) -> tuple[ImageFont.FreeTypeFont, list[list[str]]]:
    """The biggest font that fits the words in at most 3 lines inside width x height, and those lines."""
    for size in range(300, 40, -6):
        f = _thumb_font(size, devanagari)
        space = d.textlength(" ", font=f) + size * 0.04
        lines: list[list[str]] = [[]]
        for word in words:
            if lines[-1] and d.textlength(" ".join([*lines[-1], word]), font=f) + space * len(lines[-1]) > width:
                lines.append([])
            lines[-1].append(word)
        cap = f.getbbox("H")[3] - f.getbbox("H")[1]
        widest = max(d.textlength(" ".join(line), font=f) + space * len(line) for line in lines)
        if len(lines) <= 3 and widest <= width and cap * 1.32 * len(lines) <= height:
            return f, lines
    return f, lines


def decode_16k_mono(path: Path) -> Any:
    """The audio as 16 kHz mono floats. faster-whisper's own decoder breaks on newer PyAV releases, ffmpeg does not."""
    import numpy as np

    r = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
        capture_output=True,
    )
    if r.returncode or not r.stdout:
        raise PermanentError(f"Could not read the audio of this file: {r.stderr.decode(errors='replace')[-300:]}")
    return np.frombuffer(r.stdout, dtype=np.float32)


class FasterWhisperTranscriber:
    """Fallback when captions are missing. Install with: uv sync --extra media"""

    def __init__(self, model: str):
        self.model_name = model
        self._model: Any = None

    def transcribe(self, media_path: Path, language: str | None) -> list[Cue]:
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise PermanentError("faster-whisper is not installed (uv sync --extra media)") from e
        if self._model is None:
            self._model = WhisperModel(self.model_name, compute_type="int8")
        lang = None if language in (None, "hinglish") else language
        segments, _ = self._model.transcribe(decode_16k_mono(media_path), language=lang, vad_filter=True)
        return [Cue(s.start, s.end, s.text.strip()) for s in segments]


@lru_cache(maxsize=1)
def _whisper(model: str) -> Any:
    from faster_whisper import WhisperModel

    return WhisperModel(model, compute_type="int8")


def heard_words(audio: Path, language: str | None = None, model: str = "small") -> list[tuple[str, float, float]]:
    """(word, start, end) of every word heard in the audio, in whatever language it is spoken (or `language`). Empty
    when faster-whisper is not installed: the words then share each sentence's time by length."""
    try:
        whisper = _whisper(model)
    except ImportError:
        log.warning("faster-whisper is not installed: word times are estimated, not heard")
        return []
    segments, _ = whisper.transcribe(decode_16k_mono(audio), language=language, word_timestamps=True, vad_filter=True)
    return [(w.word.strip(), w.start, w.end) for s in segments for w in (s.words or [])]


def speech_times(audio: Path, model: str) -> list[tuple[float, float]]:
    """(start, end) of every word heard in the narration."""
    return [(a, b) for _, a, b in heard_words(audio, model=model)]


def _plain_word(w: str) -> str:
    """A script word without characters the storyboard markup would read as emphasis markers."""
    return w.replace("!!", "!").translate(str.maketrans("", "", "*[]~{}"))


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
