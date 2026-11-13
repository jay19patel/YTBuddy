"""FFmpeg media processing, Pillow images, faster-whisper transcription.

Karaoke subtitles are drawn with Pillow as transparent PNG frames (one per spoken word, current word
highlighted) and overlaid with FFmpeg's core `overlay` filter. That needs no libass/drawtext, so it works
with any FFmpeg build.
"""

import os
import shutil
import subprocess
import tempfile
import textwrap
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from ytstudio.domain.errors import PermanentError
from ytstudio.domain.models import Cue

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
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
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


VIDEO_SIZES = {True: (1080, 1920), False: (1920, 1080)}  # vertical (Shorts) or landscape
INK, ACCENT, PAPER = (20, 20, 20), (229, 50, 45), (255, 255, 255)


def chunk_words(words: list[str], size: int = 8) -> list[list[str]]:
    """Even groups of at most `size` words, so a subtitle never shows a long sentence at once."""
    if not words:
        return []
    groups = -(-len(words) // size)
    each = -(-len(words) // groups)
    return [words[i : i + each] for i in range(0, len(words), each)]


def subtitle_words(units: list[dict[str, Any]]) -> list[tuple[float, float, int, list[str]]]:
    """(start, end, index of the word being said, words on screen) for every word of every spoken line.
    A line's time is shared out by word length, one subtitle chunk after another."""
    cues: list[Cue] = []
    for u in units:
        chunks = chunk_words(str(u["text"]).split())
        total = sum(len(w) + 1 for c in chunks for w in c) or 1
        t = float(u["start"])
        for c in chunks:
            dt = (float(u["end"]) - float(u["start"])) * sum(len(w) + 1 for w in c) / total
            cues.append(Cue(t, t + dt, " ".join(c)))
            t += dt
    return words_timed(cues, 0.0, max((c.end for c in cues), default=0.0) + 1)


def subtitle_frame(words: list[str], current: int, size: tuple[int, int], path: Path) -> None:
    """Black text on a white screen; the word being said is red."""
    width, height = size
    vertical = height > width
    img = Image.new("RGB", size, PAPER)
    d = ImageDraw.Draw(img)
    f = _font_for(" ".join(words), 92 if vertical else 84)
    lines = textwrap.wrap(" ".join(words), width=16 if vertical else 30) or [""]
    step = 118 if vertical else 108
    y = (height - len(lines) * step) // 2
    k = 0
    for line in lines:
        x = (width - d.textlength(line, font=f)) / 2
        for w in line.split():
            d.text((x, y), w, font=f, fill=ACCENT if k == current else INK)
            x += d.textlength(w + " ", font=f)
            k += 1
        y += step
    img.save(path, compress_level=1)


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
        part = out.with_name(f".{out.name}.part.mp3")
        _ffmpeg("-i", str(wav), "-c:a", "libmp3lame", "-q:a", "3", str(part), timeout=600)
        os.replace(part, out)

    def render_subtitle_video(self, units: list[dict[str, Any]], audio: Path, vertical: bool, out: Path) -> None:
        """White screen, black subtitles that follow the voice. One picture per spoken word, held until the next."""
        size = VIDEO_SIZES[vertical]
        words = subtitle_words(units)
        if not words:
            raise PermanentError("There are no words to show as subtitles.")
        total = self.duration(audio)
        out.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            blank = tmp / "blank.png"
            Image.new("RGB", size, PAPER).save(blank, compress_level=1)
            lines = ["ffconcat version 1.0"]
            if words[0][0] > 0.02:
                lines += [f"file '{blank}'", f"duration {words[0][0]:.3f}"]
            last = blank
            for n, (t0, _t1, idx, ws) in enumerate(words):
                nxt = words[n + 1][0] if n + 1 < len(words) else max(total, t0 + 0.1)
                last = tmp / f"w{n:05d}.png"
                subtitle_frame(ws, idx, size, last)
                lines += [f"file '{last}'", f"duration {max(nxt - t0, 0.04):.3f}"]
            lines.append(f"file '{last}'")
            concat = tmp / "subs.ffconcat"
            concat.write_text("\n".join(lines) + "\n")
            part = out.with_name(f".{out.name}.part.mp4")
            _ffmpeg(
                "-f", "concat", "-safe", "0", "-i", str(concat), "-i", str(audio),
                "-vf", "fps=30,format=yuv420p",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-shortest",
                str(part),
            )  # fmt: skip
            os.replace(part, out)

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


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
