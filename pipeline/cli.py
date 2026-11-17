"""python -m pipeline <command>. Run with --help for the options of each command."""

import argparse
import json
import logging
import math
import os
import shutil
import struct
import sys
import tempfile
import time
import wave
from pathlib import Path

from pipeline import codegen, markup, plan, render, transcribe, video
from pipeline.schema import Timeline
from pipeline.settings import ROOT, Settings, load
from pipeline.video import video_info

log = logging.getLogger("pipeline")
DEMO = ROOT / "demo"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m pipeline", description="Kinetic typography videos from a voice-over.")
    p.add_argument("--config", type=Path, default=ROOT / "config" / "kinetic.yaml")
    p.add_argument("--json-logs", action="store_true", help="one JSON object per log line")
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("make", help="voice-over (+ script/storyboard) → finished mp4")
    m.add_argument("--audio", type=Path, required=True, help="the voice-over (wav/mp3/m4a)")
    m.add_argument("--script", type=Path, help="the exact words spoken, with emphasis markers; fixes ASR spelling")
    m.add_argument(
        "--storyboard", type=Path, help="YAML storyboard (manual mode); without it scenes are planned automatically"
    )
    m.add_argument("--words", type=Path, help="words.json with word timings; skips transcription")
    m.add_argument("--format", default="16x9", help="16x9 or 9x16 (see config formats)")
    m.add_argument("--out", type=Path, default=ROOT / "exports")
    m.add_argument("--music", type=Path, help="background music, looped and ducked under the voice")
    m.add_argument("--sfx", action="store_true", help="add pops/whooshes/clicks from assets/sfx")
    m.add_argument(
        "--llm", action="store_true", help="auto mode: let the LLM in config pick scenes, colours and stickers"
    )
    m.add_argument("--until", type=float, help="only render the first N seconds")
    m.add_argument("--keep-temp", action="store_true")

    sub.add_parser("gen", help="regenerate renderer/src/types.gen.ts and theme.gen.ts from the Python schema/theme")

    s = sub.add_parser("stills", help="PNG stills at given seconds, for checking the look")
    s.add_argument("--composition", default="Demo")
    s.add_argument("--timeline", type=Path, help="timeline.json (default: the composition's built-in demo)")
    s.add_argument("--at", default="1,4,7,12,18", help="comma-separated seconds")
    s.add_argument("--out", type=Path, default=ROOT / "docs" / "previews")
    s.add_argument("--prefix", default="")

    d = sub.add_parser("demo", help="the reference video from demo/story.yaml with fake word timings and a click track")
    d.add_argument("--format", default="16x9")
    d.add_argument("--out", type=Path, default=ROOT / "exports")
    d.add_argument("--until", type=float)
    d.add_argument("--write-timeline", action="store_true", help="only rewrite demo/timeline.json (the first 22 s)")

    v = sub.add_parser("voice", help="read a storyboard aloud (Microsoft neural voice) → voice wav + words.json")
    v.add_argument("--storyboard", type=Path, required=True)
    v.add_argument("--out", type=Path, required=True, help="the .wav to write; words go next to it as .words.json")
    v.add_argument(
        "--voice", default="en-IN-PrabhatNeural", help="Edge neural voice; Indian English reads Hinglish best"
    )

    a = p.parse_args(argv)
    _logging(a.json_logs)
    cfg = load(a.config)
    try:
        if a.cmd == "gen":
            for path in codegen.write():
                log.info("wrote %s", path.relative_to(ROOT))
        elif a.cmd == "make":
            make(
                cfg,
                a.audio,
                a.script,
                a.storyboard,
                a.words,
                a.format,
                a.out,
                a.music,
                a.sfx,
                a.llm,
                a.until,
                a.keep_temp,
            )
        elif a.cmd == "stills":
            stills(cfg, a.composition, a.timeline, [float(x) for x in a.at.split(",")], a.out, a.prefix)
        elif a.cmd == "demo":
            demo(cfg, a.format, a.out, a.until, a.write_timeline)
        elif a.cmd == "voice":
            voice(a.storyboard, a.out, a.voice)
    except (render.RenderError, ValueError, FileNotFoundError) as e:
        log.error("%s", e)
        return 1
    return 0


def _logging(as_json: bool) -> None:
    from ytstudio.logging_setup import setup_logging

    setup_logging("INFO", as_json=as_json)
    if not as_json:
        logging.getLogger().handlers[0].setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
        )


def make(
    cfg: Settings,
    audio: Path,
    script: Path | None,
    storyboard: Path | None,
    words_file: Path | None,
    fmt: str,
    out_dir: Path,
    music: Path | None,
    sfx: bool,
    llm: bool,
    until: float | None,
    keep_temp: bool,
) -> Path:
    from ytstudio.adapters.media import FFmpegMediaProcessor

    t0 = time.monotonic()
    for f in (audio, script, storyboard, words_file, music):
        if f and not f.exists():
            raise FileNotFoundError(f"{f} does not exist")
    render.preflight(cfg.renderer)
    story = plan.load_story(storyboard) if storyboard else None
    tokens = (
        plan.script_of(story)
        if story
        else [t for line in script.read_text(encoding="utf-8").splitlines() for t in markup.parse(line)]
        if script
        else None
    )
    text = " ".join(t.text for t in tokens or [])
    heard = (
        transcribe.load(words_file)
        if words_file
        else transcribe.transcribe(audio, cfg.transcribe.model, cfg.transcribe.language, text)
    )
    words = transcribe.align([t.text for t in tokens], heard) if tokens else heard
    tokens = tokens or [markup.Token(w.word) for w in words]
    if not words:
        raise ValueError("no words were found in the audio")
    total = FFmpegMediaProcessor().duration(audio)
    story = story or video.auto_story(cfg, tokens, words, llm)
    timeline = plan.plan(story, words, video_info(cfg, fmt), total, cfg.assets)
    if until:
        timeline = cut(timeline, until)

    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{audio.stem}-{fmt}"
    work = Path(tempfile.mkdtemp(prefix=f"{name}-"))
    try:
        transcribe.save(words, out_dir / f"{name}.words.json")
        (out_dir / f"{name}.timeline.json").write_text(timeline.model_dump_json(indent=1), encoding="utf-8")
        if storyboard is None:  # so the automatic plan can be edited and re-run as a storyboard
            import yaml

            (out_dir / f"{name}.story.yaml").write_text(
                yaml.safe_dump(story.model_dump(exclude_none=True), allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
        final = out_dir / f"{name}.mp4"
        part = out_dir / f".{name}.part.mp4"
        video.render_video(cfg, timeline, audio, part, work, music, sfx)
        os.replace(part, final)
    finally:
        if keep_temp:
            log.info("temp files kept in %s", work)
        else:
            shutil.rmtree(work, ignore_errors=True)
    log.info("done in %.0f s: %s", time.monotonic() - t0, final)
    return final


def cut(t: Timeline, seconds: float) -> Timeline:
    scenes = [s.model_copy(update={"end": min(s.end, seconds)}) for s in t.scenes if s.start < seconds]
    video = t.video.model_copy(update={"duration_frames": round(min(seconds, scenes[-1].end) * t.video.fps)})
    return Timeline(video=video, scenes=scenes)


def stills(cfg: Settings, composition: str, timeline: Path | None, at: list[float], out: Path, prefix: str) -> None:
    render.preflight(cfg.renderer)
    out.mkdir(parents=True, exist_ok=True)
    fps = cfg.fps
    props = None
    work = Path(tempfile.mkdtemp())
    try:
        if timeline:
            t = Timeline.model_validate_json(timeline.read_text(encoding="utf-8"))
            fps = t.video.fps
            props = work / "props.json"
            props.write_text(json.dumps({"timeline": t.model_dump(mode="json")}), encoding="utf-8")
        for s in at:
            png = out / f"{prefix}{composition.lower()}-{s:05.1f}s.png"
            render.run(render.still_cmd(composition, props, round(s * fps), png.resolve()), cfg.renderer, 600)
            log.info("still %s", png.relative_to(ROOT) if png.is_relative_to(ROOT) else png)
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------- demo: no real voice needed


def fake_words(story: plan.Story, total: float) -> list[transcribe.TimedWord]:
    """Each scene's words spread evenly (by length) between its reference `start` and the next scene's."""
    starts = [s.start for s in story.scenes]
    if any(x is None for x in starts):
        raise ValueError("every demo scene needs a reference `start`")
    out: list[transcribe.TimedWord] = []
    for i, s in enumerate(story.scenes):
        toks = [t.text for e in s.content if (txt := plan.spoken_text(e)) is not None for t in markup.parse(txt)]
        a = float(starts[i]) + 0.2  # type: ignore[arg-type]
        b = (float(starts[i + 1]) if i + 1 < len(starts) else total) - 0.3  # type: ignore[arg-type]
        t = a
        unit = (b - a) / max(1, sum(len(x) + 3 for x in toks))
        for x in toks:
            dt = unit * (len(x) + 3)
            out.append(transcribe.TimedWord(word=x, start=round(t, 3), end=round(t + dt * 0.85, 3)))
            t += dt
    return out


def click_track(words: list[transcribe.TimedWord], total: float, path: Path, rate: int = 24000) -> None:
    """A quiet tick on every word start: stands in for a voice so audio/video sync can be checked by ear."""
    samples = bytearray(int(total * rate) * 2)
    for w in words:
        i0 = int(w.start * rate)
        for k in range(int(0.03 * rate)):
            if i0 + k < len(samples) // 2:
                v = int(6000 * math.sin(2 * math.pi * 880 * k / rate) * (1 - k / (0.03 * rate)))
                struct.pack_into("<h", samples, (i0 + k) * 2, v)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(bytes(samples))


def voice(storyboard: Path, out: Path, name: str) -> Path:
    """Each scene's spoken lines read by the existing EdgeNarrator as one passage (the service pads every request with
    silence, so one request per line drags); its word boundaries become words.json, so no transcription is needed."""
    from ytstudio.adapters.narrator import EdgeNarrator

    story = plan.load_story(storyboard)
    lines: list[tuple[str, float]] = []
    for s in story.scenes:
        spoken = [markup.plain(t) for e in s.content if (t := plan.spoken_text(e)) is not None]
        if spoken:  # a comma between lines gives a natural breath inside the scene
            lines.append((" ".join(t if t[-1] in ".?!," else t + "," for t in spoken), 0.15))
    out.parent.mkdir(parents=True, exist_ok=True)
    log.info("reading %d lines with %s", len(lines), name)
    timings = EdgeNarrator().speak(lines, name, out)
    words = [transcribe.TimedWord(word=w["text"], start=w["start"], end=w["end"]) for t in timings for w in t["words"]]
    words_file = out.with_suffix(".words.json")
    transcribe.save(words, words_file)
    log.info("wrote %s (%.1f s) and %s (%d words)", out.name, timings[-1]["end"], words_file.name, len(words))
    return out


def demo(cfg: Settings, fmt: str, out: Path, until: float | None, write_timeline: bool) -> None:
    story = plan.load_story(DEMO / "story.yaml")
    total = 90.6
    words = fake_words(story, total)
    if write_timeline:
        t = cut(plan.plan(story, words, video_info(cfg, "16x9"), total, cfg.assets), 22)
        (DEMO / "timeline.json").write_text(t.model_dump_json(indent=1), encoding="utf-8")
        log.info("wrote demo/timeline.json (%d scenes, 0–22 s)", len(t.scenes))
        return
    out.mkdir(parents=True, exist_ok=True)
    wav = out / "demo-voice.wav"
    words_file = out / "demo-fake.words.json"
    click_track(words, total, wav)
    transcribe.save(words, words_file)
    make(cfg, wav, None, DEMO / "story.yaml", words_file, fmt, out, None, False, False, until, False)


if __name__ == "__main__":
    sys.exit(main())
